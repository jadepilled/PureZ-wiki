"""Read DayZ's binarised ODOL P3D models (version 56) and convert them to MLOD.

    python pipeline/odol.py <model.p3d> [out.p3d]        # writes <model>.mlod.p3d by default
    python pipeline/odol.py --batch <folder>              # every ODOL under the folder, in place (original kept as .odol)

The layout was worked out from the game's own files, guided by armake's ODOL v71 writer. Each LOD gives
points, normals, UVs, faces with their texture and material, named selections, named properties and
proxies (with their full transform). Vertices that ODOL split per normal/UV are merged back into shared
points by position, so MLOD selections and the Blender import behave as if the model had never been
binarised. Arrays over 1023 bytes are LZO-compressed (lzo1x).
"""
import argparse, array, math, os, struct, sys, time

VISUAL_LOD_MAX = 1000.0
MAX_FILE_MB = 48          # --batch skips bigger files unless --max-size raises this
MAX_LOD_MB = 24           # a single LOD bigger than this is skipped


class Reader:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def u8(self):
        v = self.d[self.p]; self.p += 1; return v

    def u16(self):
        v = struct.unpack_from("<H", self.d, self.p)[0]; self.p += 2; return v

    def i16(self):
        v = struct.unpack_from("<h", self.d, self.p)[0]; self.p += 2; return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]; self.p += 4; return v

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]; self.p += 4; return v

    def f32(self, n=1):
        v = struct.unpack_from(f"<{n}f", self.d, self.p); self.p += 4 * n
        return v if n > 1 else v[0]

    def raw(self, n):
        v = self.d[self.p:self.p + n]; self.p += n; return v

    def asciiz(self):
        e = self.d.index(b"\0", self.p)
        v = self.d[self.p:e].decode("latin1"); self.p = e + 1
        return v

    def lzo(self, expected):
        """LZO1X decompression of the next block; the stream's own end marker tells how much input it used."""
        out, used = lzo1x_decompress(self.d, self.p, expected)
        self.p += used
        return out

    def compressed(self, count, size):
        n = count * size
        return self.lzo(n) if n > 1023 else self.raw(n)

    def condensed(self, count, size):
        """CondensedArray: a fill flag, then one element for all, or the array."""
        if count == 0:
            return b""
        if self.u8():
            return self.raw(size) * count
        return self.compressed(count, size)


def lzo1x_decompress(src, ip, expected):
    """Pure-Python LZO1X decoder (minilzo's lzo1x_decompress). Returns (bytes, input bytes consumed)."""
    out = bytearray()
    start = ip
    t = src[ip]
    state = 0           # 0: expect a literal run, 1: a match follows directly
    if t > 17:
        ip += 1
        t -= 17
        out += src[ip:ip + t]; ip += t
        state = 1
        t = src[ip]; ip += 1
    while True:
        if state == 0:
            t = src[ip]; ip += 1
            if t < 16:
                if t == 0:
                    while src[ip] == 0:
                        t += 255; ip += 1
                    t += 15 + src[ip]; ip += 1
                t += 3
                out += src[ip:ip + t]; ip += t
                t = src[ip]; ip += 1
                if t < 16:                           # short match after a literal run
                    pos = len(out) - (1 + 0x800) - (t >> 2) - (src[ip] << 2); ip += 1
                    for _ in range(3):
                        out.append(out[pos]); pos += 1   # three bytes, may overlap
                    t = src[ip - 2] & 3
                    if t == 0:
                        continue
                    out += src[ip:ip + t]; ip += t
                    t = src[ip]; ip += 1
                    state = 1
                    continue
        # a match instruction in t
        while True:
            if t >= 64:
                pos = len(out) - 1 - ((t >> 2) & 7) - (src[ip] << 3); ip += 1
                length = (t >> 5) - 1 + 2
            elif t >= 32:
                length = t & 31
                if length == 0:
                    while src[ip] == 0:
                        length += 255; ip += 1
                    length += 31 + src[ip]; ip += 1
                pos = len(out) - 1 - ((src[ip] >> 2) + (src[ip + 1] << 6)); ip += 2
                length += 2
            elif t >= 16:
                pos = len(out) - ((t & 8) << 11)
                length = t & 7
                if length == 0:
                    while src[ip] == 0:
                        length += 255; ip += 1
                    length += 7 + src[ip]; ip += 1
                pos -= (src[ip] >> 2) + (src[ip + 1] << 6); ip += 2
                if pos == len(out):                  # end of stream
                    if len(out) != expected:
                        raise ValueError(f"LZO block is {len(out)} bytes, expected {expected}")
                    return bytes(out), ip - start
                pos -= 0x4000
                length += 2
            else:
                pos = len(out) - 1 - (t >> 2) - (src[ip] << 2); ip += 1
                length = 2
            if pos < 0:
                raise ValueError("LZO match before the start of the block")
            while length > 0:                        # overlapping matches repeat what was just written
                chunk = out[pos:pos + min(length, len(out) - pos)]
                out += chunk
                pos += len(chunk); length -= len(chunk)
            t = src[ip - 2] & 3
            if t == 0:
                state = 0
                break
            out += src[ip:ip + t]; ip += t
            t = src[ip]; ip += 1
            state = 1


class Lod:
    """One LOD in compact arrays (flat floats and ints) so large models stay within memory."""

    def __init__(self):
        self.resolution = 0.0
        self.proxies = []           # dicts: name, x, y, z, n (triplets), id, selection, bone, section
        self.textures, self.materials = [], []
        self.face_n = array.array("B")      # vertices per face (3 or 4)
        self.face_v = array.array("I")      # vertex indices, flat
        self.face_tex = array.array("h")    # texture index per face (-1 none)
        self.face_mat = array.array("h")    # material index per face (-1 none)
        self.sections = []          # dicts: start, end (byte offsets into face data), texture, material
        self.selections = {}        # name -> {faces: array, vertices: array}
        self.properties = {}
        self.points = array.array("f")      # x y z per vertex
        self.normals = array.array("f")
        self.uvs = array.array("f")         # u v per vertex

    @property
    def npoints(self):
        return len(self.points) // 3

    @property
    def nfaces(self):
        return len(self.face_n)

    def faces(self):
        """Yield (first index into face_v, count) per face."""
        off = 0
        for n in self.face_n:
            yield off, n
            off += n

    def point(self, i):
        return tuple(self.points[3 * i:3 * i + 3])


def read_material(r):
    m = {"path": r.asciiz(), "type": r.u32()}
    m["colors"] = [r.f32(4) for _ in range(6)]
    r.raw(108)                                 # DayZ shader parameters (27 floats), then the usual tail
    m["pixelShader"], m["vertexShader"] = r.u32(), r.u32()
    r.u32(); r.u32()
    m["surface"] = r.asciiz()
    r.u32()
    m["renderFlags"] = r.u32()
    nt, ntr = r.u32(), r.u32()
    m["stages"] = []
    for _ in range(nt):
        st = {"filter": r.u32(), "texture": r.asciiz(), "transform": r.u32()}
        r.u8()
        m["stages"].append(st)
    for _ in range(ntr):
        r.u32(); r.f32(12)
    r.u32(); r.asciiz(); r.u32(); r.u8()      # dummy stage
    return m


def read_animations(r, nlods):
    n = r.u32()
    anims = []
    for _ in range(n):
        a = {"type": r.u32(), "name": r.asciiz(), "source": r.asciiz()}
        a["min"], a["max"], a["minPhase"], a["maxPhase"] = r.f32(4)
        a["sourceAddress"] = r.u32()
        t = a["type"]
        if t <= 7:
            r.f32(2)
        elif t == 8:
            r.f32(8)
        elif t == 9:
            r.f32(2)
        anims.append(a)
    nlods = r.u32()
    for _ in range(nlods):                     # bones -> animations
        for _ in range(r.u32()):
            k = r.u32(); r.raw(4 * k)
    for _ in range(nlods):                     # animations -> bones
        for a in anims:
            bone = r.i32()
            if bone != -1 and a["type"] < 8:
                r.f32(6)
    return anims


def read_odol(data, which="visual", max_lod_bytes=MAX_LOD_MB << 20, log=None):
    """Parse an ODOL file. which: "visual" (resolution below 1000), "all", or a set of resolutions."""
    r = Reader(data)
    if r.raw(4) != b"ODOL":
        raise ValueError("not an ODOL file")
    version = r.u32()
    if version != 56:
        raise ValueError(f"ODOL version {version}; only version 56 (DayZ) is handled")
    nlods = r.u32()
    resolutions = [r.f32() for _ in range(nlods)]

    # ---- model info (only what the converter needs is kept)
    r.u32(); r.f32(); r.f32(); r.raw(12); r.f32(3); r.u32(); r.u32(); r.f32()
    bmin, bmax = r.f32(3), r.f32(3)
    r.f32(3); r.f32(3); r.f32(3); r.f32(3); r.f32(3); r.f32(9)
    r.raw(5)                                   # autocenter, lock, canOcclude, canBeOccluded, +1
    r.f32(6); r.u8(); r.i32(); r.u8(); r.raw(8); r.f32(); r.u8()
    skeleton = r.asciiz()
    bones = []
    if skeleton:
        r.u8()
        for _ in range(r.u32()):
            bones.append((r.asciiz(), r.asciiz()))
        r.u8()
    r.u8()                                     # map type
    nf = r.u32()
    if nf:
        r.compressed(nf, 4)
    r.f32(4)                                   # mass, 1/mass, armor, 1/armor
    r.raw(13)                                  # special lod indices
    r.u32(); r.u8(); r.asciiz(); r.asciiz(); r.u8(); r.u32(); r.u8()
    animated = r.u8()
    if animated:
        read_animations(r, nlods)
    starts = [r.u32() for _ in range(nlods)]
    ends = [r.u32() for _ in range(nlods)]
    permanent = r.raw(nlods)
    for i in range(nlods):
        if not permanent[i]:
            r.raw(25)                          # face count, colour, special, or-hints, flag, vertices, area

    lods, skipped = [], []
    for i in range(nlods):
        res = resolutions[i]
        wanted = which == "all" or (which == "visual" and res < VISUAL_LOD_MAX) or (isinstance(which, set) and res in which)
        if not wanted:
            continue
        if ends[i] - starts[i] > max_lod_bytes:
            skipped.append(res)
            continue
        lod = read_lod(Reader(data, starts[i]), ends[i])
        lod.resolution = res
        lods.append(lod)
    if skipped and log:
        log(f"  skipped {len(skipped)} LOD(s) over {max_lod_bytes >> 20} MB: {', '.join(f'{x:g}' for x in skipped)}")
    return {"version": version, "skeleton": skeleton, "bones": bones, "bbox": (bmin, bmax), "lods": lods, "skipped": skipped}


def read_lod(r, end):
    lod = Lod()
    for _ in range(r.u32()):
        p = {"name": r.asciiz()}
        p["x"], p["y"], p["z"], p["n"] = r.f32(3), r.f32(3), r.f32(3), r.f32(3)
        p["id"], p["selection"], p["bone"], p["section"] = r.u32(), r.u32(), r.i32(), r.u32()
        lod.proxies.append(p)
    r.raw(4 * r.u32())                         # subskeleton -> skeleton
    for _ in range(r.u32()):                   # skeleton -> subskeleton links
        r.raw(4 * r.u32())
    npoints = r.u32()
    r.f32(); r.u32(); r.u32(); r.f32(3); r.f32(3); r.f32(3); r.f32()
    lod.textures = [r.asciiz() for _ in range(r.u32())]
    lod.materials = [read_material(r) for _ in range(r.u32())]
    n = r.u32()
    if n:
        r.compressed(n, 2)                     # point -> vertex
    n = r.u32()
    if n:
        r.compressed(n, 2)                     # vertex -> point
    nfaces, alloc = r.u32(), r.u32()
    r.u16()
    face_start = r.p
    # vertex indices are 16-bit unless the LOD has more than 65535 vertices (then 32-bit)
    isz, ich = (2, "H") if npoints <= 0xFFFF else (4, "I")
    face_offsets = array.array("I")
    d = r.d
    for _ in range(nfaces):
        face_offsets.append(r.p - face_start)
        k = d[r.p]; r.p += 1
        lod.face_n.append(k)
        lod.face_v.extend(struct.unpack_from(f"<{k}{ich}", d, r.p))
        r.p += k * isz
    if r.p - face_start != alloc and r.p - face_start + nfaces != alloc:
        raise ValueError(f"face table size {r.p - face_start} != allocation {alloc}")
    for _ in range(r.u32()):                   # sections
        s = {"start": r.u32(), "end": r.u32()}
        r.u32(); r.u32(); r.u32()
        s["texture"] = struct.unpack("<h", r.raw(2))[0]
        r.u32()
        s["material"] = r.i32()
        if s["material"] == -1:
            r.u8()
        nstages = r.u32()
        if nstages:
            r.f32(nstages)
        lod.sections.append(s)
    # index arrays are 16-bit unless the LOD has more than 65535 faces/vertices
    fsz, fch = (2, "H") if nfaces <= 0xFFFF else (4, "I")
    vsz, vch = (2, "H") if npoints <= 0xFFFF else (4, "I")
    for _ in range(r.u32()):                   # named selections
        name = r.asciiz()
        nf = r.u32()
        faces = array.array("I", struct.unpack(f"<{nf}{fch}", r.compressed(nf, fsz))) if nf else array.array("I")
        r.u32()
        r.u8()
        ns = r.u32()
        if ns:
            r.compressed(ns, 4)
        nv = r.u32()
        verts = array.array("I", struct.unpack(f"<{nv}{vch}", r.compressed(nv, vsz))) if nv else array.array("I")
        nw = r.u32()
        weights = r.compressed(nw, 1) if nw else b""
        lod.selections[name] = {"faces": faces, "vertices": verts, "weights": weights}
    for _ in range(r.u32()):
        key = r.asciiz()
        lod.properties[key] = r.asciiz()
    nframes = r.u32()
    for _ in range(nframes):
        r.f32(); r.f32(npoints)
    r.u32(); r.u32(); r.u32(); r.u8()          # icon colour, selected colour, flags, simple bone refs
    r.u32()                                    # vertex table size
    r.condensed(r.u32(), 4)                    # point flags
    uv_scale = r.f32(4)
    lod.uvs = read_uvs(r, uv_scale)
    for _ in range(r.u32() - 1):               # further UV sets
        read_uvs(r, r.f32(4))
    n = r.u32()
    lod.points = array.array("f")
    lod.points.frombytes(r.compressed(n, 12))
    n = r.u32()
    packed = array.array("I")
    packed.frombytes(r.condensed(n, 4))
    lod.normals = array.array("f")
    for v in packed:
        lod.normals.extend(unpack_normal(v))
    # tangents, bone weights and the rest of the vertex table are not needed
    if lod.npoints != npoints:
        raise ValueError("point count mismatch")

    # texture and material per face from the sections (byte ranges of the face table)
    lod.face_tex = array.array("h", [-1]) * nfaces
    lod.face_mat = array.array("h", [-1]) * nfaces
    secs = sorted(lod.sections, key=lambda x: x["start"])
    si = 0
    for i, off in enumerate(face_offsets):
        while si < len(secs) and off >= secs[si]["end"]:
            si += 1
        if si < len(secs) and secs[si]["start"] <= off:
            t, m = secs[si]["texture"], secs[si]["material"]
            lod.face_tex[i] = t if 0 <= t < len(lod.textures) else -1
            lod.face_mat[i] = m if 0 <= m < len(lod.materials) else -1
    return lod


def read_uvs(r, scale):
    n = r.u32()
    raw = array.array("h")
    raw.frombytes(r.condensed(n, 4))
    u0, v0, u1, v1 = scale
    su, sv = (u1 - u0) / 65534, (v1 - v0) / 65534
    out = array.array("f", [0.0]) * (2 * n)
    for i in range(n):
        out[2 * i] = (raw[2 * i] + 32767) * su + u0
        out[2 * i + 1] = (raw[2 * i + 1] + 32767) * sv + v0
    return out


def unpack_normal(v):
    def s10(x):
        x &= 0x3FF
        return x - 1024 if x & 0x200 else x
    x, y, z = s10(v), s10(v >> 10), s10(v >> 20)
    return (-x / 511.0, -y / 511.0, -z / 511.0)


# ----------------------------------------------------------------------------- MLOD writer
def proxy_triangle(p):
    """The MLOD placeholder for a proxy: a right-angled triangle at the proxy's origin whose long
    leg follows its forward (Z) axis and short leg its up (Y) axis; X = Y x Z recovers the frame."""
    o = p["n"]
    return [tuple(o), tuple(o[k] + 0.1 * p["z"][k] for k in range(3)), tuple(o[k] + 0.05 * p["y"][k] for k in range(3))]


def mlod_chunks(model, lods=None):
    """Yield the MLOD file as chunks, one LOD at a time, so a big model never sits in memory twice."""
    chosen = [l for l in model["lods"] if lods is None or l.resolution in lods]
    yield b"MLOD" + struct.pack("<II", 257, len(chosen))
    for lod in chosen:
        # ODOL splits a point per normal/UV; weld them back by position so faces share vertices
        weld, remap, points = {}, array.array("I"), array.array("f")
        P = lod.points
        for i in range(lod.npoints):
            key = (round(P[3 * i], 6), round(P[3 * i + 1], 6), round(P[3 * i + 2], 6))
            j = weld.get(key)
            if j is None:
                j = weld[key] = len(points) // 3
                points.extend(P[3 * i:3 * i + 3])
            remap.append(j)
        del weld
        normals = array.array("f", lod.normals) if len(lod.normals) else array.array("f", [0.0, 1.0, 0.0])
        nnorm = len(normals) // 3
        faces = []                          # (verts [(pt, nrm, u, v)], tex, mat)
        U = lod.uvs
        for off, n in lod.faces():
            verts = []
            for k in range(off, off + n):
                vi = lod.face_v[k]
                verts.append((remap[vi], vi if vi < nnorm else 0, U[2 * vi] if 2 * vi + 1 < len(U) else 0.0, U[2 * vi + 1] if 2 * vi + 1 < len(U) else 0.0))
            faces.append([verts, lod.face_tex[len(faces)], lod.face_mat[len(faces)]])
        sel_faces = {name: set(sel["faces"]) for name, sel in lod.selections.items()}
        sel_points = {name: {remap[v] for v in sel["vertices"] if v < len(remap)} for name, sel in lod.selections.items()}
        # proxies: replace the stored placeholder triangle with one built from the proxy's transform
        names = list(lod.selections)
        for p in lod.proxies:
            name = names[p["selection"]] if p["selection"] < len(names) else f"proxy:{p['name']}.{p['id']:03d}"
            base = len(points) // 3
            for pt in proxy_triangle(p):
                points.extend(pt)
            normals.extend((0.0, 1.0, 0.0))
            ni = len(normals) // 3 - 1
            verts = [(base, ni, 0.0, 0.0), (base + 1, ni, 0.0, 0.0), (base + 2, ni, 0.0, 0.0)]
            old = sorted(sel_faces.get(name, ()))
            if old:
                faces[old[0]] = [verts, -1, -1]
                for extra in old[1:]:
                    faces[extra] = [[], -1, -1]          # dropped below
                sel_faces[name] = {old[0]}
            else:
                faces.append([verts, -1, -1])
                sel_faces[name] = {len(faces) - 1}
            sel_points[name] = {base, base + 1, base + 2}
        keep = [i for i, f in enumerate(faces) if f[0]]
        renum = {old: new for new, old in enumerate(keep)}
        faces = [faces[i] for i in keep]
        sel_faces = {n: {renum[i] for i in fs if i in renum} for n, fs in sel_faces.items()}
        npts = len(points) // 3

        out = bytearray(b"P3DM" + struct.pack("<IIIIII", 28, 256, npts, nnorm + (len(normals) // 3 - nnorm), len(faces), 0))
        for i in range(npts):
            out += struct.pack("<fffI", points[3 * i], points[3 * i + 1], points[3 * i + 2], 0)
        out += normals.tobytes()
        yield bytes(out)
        out = bytearray()
        tex_names = [t.encode("latin1") for t in lod.textures]
        mat_names = [m["path"].encode("latin1") for m in lod.materials]
        for verts, ti, mi in faces:
            out += struct.pack("<I", len(verts))
            for k in range(4):
                pi, ni, u, v = verts[k] if k < len(verts) else (0, 0, 0.0, 0.0)
                out += struct.pack("<IIff", pi, ni, u, v)
            out += b"\0\0\0\0" + (tex_names[ti] if ti >= 0 else b"") + b"\0" + (mat_names[mi] if mi >= 0 else b"") + b"\0"
            if len(out) > (4 << 20):
                yield bytes(out)
                out = bytearray()
        out += b"TAGG"
        for name in sel_faces:
            body = bytearray(npts + len(faces))
            for i in sel_points.get(name, ()):
                body[i] = 1
            for i in sel_faces[name]:
                body[npts + i] = 1
            out += b"\x01" + name.encode("latin1") + b"\0" + struct.pack("<I", len(body)) + body
        for k, v in lod.properties.items():
            body = k.encode("latin1")[:63].ljust(64, b"\0") + v.encode("latin1")[:63].ljust(64, b"\0")
            out += b"\x01#Property#\0" + struct.pack("<I", len(body)) + body
        out += b"\x01#EndOfFile#\0" + struct.pack("<I", 0)
        out += struct.pack("<f", lod.resolution)
        yield bytes(out)


def mlod_bytes(model, lods=None):
    return b"".join(mlod_chunks(model, lods))


def write_mlod(model, path, lods=None):
    with open(path, "wb") as f:
        for chunk in mlod_chunks(model, lods):
            f.write(chunk)


def convert(src, dst, keep_all=False, max_lod_bytes=MAX_LOD_MB << 20, log=None):
    with open(src, "rb") as f:
        data = f.read()
    model = read_odol(data, "all" if keep_all else "visual", max_lod_bytes, log)
    del data
    write_mlod(model, dst)
    return model


def lower_priority():
    """Run below normal priority so the desktop stays responsive."""
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
        else:
            os.nice(10)
    except Exception:
        pass


def batch(a):
    lower_priority()
    files = []
    for d, _, fs in os.walk(a.src):
        for f in fs:
            if f.lower().endswith(".p3d"):
                files.append(os.path.join(d, f))
    files.sort()
    todo, skipped_big, already = [], [], 0
    for path in files:
        with open(path, "rb") as fh:
            sig = fh.read(4)
        if sig != b"ODOL":
            already += 1
            continue
        if os.path.getsize(path) > a.max_size << 20:
            skipped_big.append(path)
            continue
        todo.append(path)
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(files)} models: {len(todo)} to convert, {already} already MLOD, {len(skipped_big)} over {a.max_size} MB (skipped)")
    if not files:
        print(f"no .p3d files under {a.src}")
    log = open(os.path.join(a.src, "odol.log"), "a", encoding="utf-8")
    done, failed, t0 = 0, [], time.time()
    for i, path in enumerate(todo, 1):
        rel = os.path.relpath(path, a.src)
        size = os.path.getsize(path)
        elapsed = time.time() - t0
        eta = (elapsed / (i - 1) * (len(todo) - i + 1)) if i > 1 else 0
        print(f"[{i}/{len(todo)}] {rel} ({size / 1048576:.1f} MB)  elapsed {elapsed / 60:.0f} min, about {eta / 60:.0f} min left", flush=True)
        t1 = time.time()
        try:
            model = convert(path, path + ".tmp", a.all_lods, a.max_lod << 20, log=lambda m: print(m, flush=True))
            os.replace(path, path[:-4] + ".odol")
            os.replace(path + ".tmp", path)
            done += 1
            lods = model["lods"]
            print(f"    ok  {len(lods)} LOD(s), {sum(l.nfaces for l in lods)} faces, {time.time() - t1:.1f} s", flush=True)
            log.write(f"ok  {rel}\n")
        except MemoryError:
            failed.append(rel)
            print("    FAILED: out of memory, skipped", flush=True)
            log.write(f"FAIL {rel}: out of memory\n")
        except Exception as e:
            failed.append(rel)
            print(f"    FAILED: {e}", flush=True)
            log.write(f"FAIL {rel}: {e}\n")
        if os.path.exists(path + ".tmp"):
            os.remove(path + ".tmp")
        log.flush()
        time.sleep(a.sleep)
    log.close()
    print(f"\nconverted {done}, failed {len(failed)}, already MLOD {already}, skipped as too big {len(skipped_big)}"
          f"  ({(time.time() - t0) / 60:.0f} min)")
    if skipped_big:
        print("too big for the default limit (raise --max-size to include them):")
        for p in skipped_big[:20]:
            print("  " + os.path.relpath(p, a.src))
    if failed:
        print("failed (see odol.log):")
        for p in failed[:20]:
            print("  " + p)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="an ODOL .p3d, or a folder with --batch")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--batch", action="store_true", help="convert every ODOL .p3d under src in place, keeping the original as .odol")
    ap.add_argument("--all-lods", action="store_true", help="keep geometry, memory and other non-visual LODs too")
    ap.add_argument("--max-size", type=int, default=MAX_FILE_MB, help=f"skip files bigger than this many MB (default {MAX_FILE_MB})")
    ap.add_argument("--max-lod", type=int, default=MAX_LOD_MB, help=f"skip a single LOD bigger than this many MB (default {MAX_LOD_MB})")
    ap.add_argument("--sleep", type=float, default=0.25, help="seconds to pause between files (default 0.25)")
    ap.add_argument("--limit", type=int, help="stop after this many conversions (for a trial run)")
    a = ap.parse_args()
    if not os.path.exists(a.src):
        sys.exit(f"not found: {a.src}\n(in Git Bash, quote Windows paths or use forward slashes: \"F:\\PureZ\\PureZ-render\" or F:/PureZ/PureZ-render)")
    if a.batch:
        batch(a)
    else:
        dst = a.dst or a.src[:-4] + ".mlod.p3d"
        model = convert(a.src, dst, a.all_lods, a.max_lod << 20, log=print)
        for l in model["lods"]:
            print(f"lod {l.resolution:g}: {l.npoints} points, {l.nfaces} faces, {len(l.proxies)} proxies, "
                  f"{len(l.selections)} selections, textures {[t for t in l.textures if t]}")
        print(f"wrote {dst}")


if __name__ == "__main__":
    main()
