"""Read DayZ's binarised ODOL P3D models (version 56) and convert them to MLOD.

    python pipeline/odol.py <model.p3d> [out.p3d]        # writes <model>.mlod.p3d by default
    python pipeline/odol.py --batch <folder>              # every ODOL under the folder, in place (original kept as .odol)

The layout was worked out from the game's own files, guided by armake's ODOL v71 writer. Each LOD gives
points, normals, UVs, faces with their texture and material, named selections, named properties and
proxies (with their full transform). Vertices that ODOL split per normal/UV are merged back into shared
points by position, so MLOD selections and the Blender import behave as if the model had never been
binarised. Arrays over 1023 bytes are LZO-compressed (lzo1x).
"""
import argparse, math, os, struct, sys

VISUAL_LOD_MAX = 1000.0


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
                        out.append(out[pos]); pos += 1
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
            for _ in range(length):
                out.append(out[pos]); pos += 1
            t = src[ip - 2] & 3
            if t == 0:
                state = 0
                break
            out += src[ip:ip + t]; ip += t
            t = src[ip]; ip += 1
            state = 1


class Lod:
    def __init__(self):
        self.resolution = 0.0
        self.proxies = []           # dicts: name, x, y, z, n (triplets), id, selection, bone, section
        self.textures, self.materials = [], []
        self.faces = []             # [vertex indices]
        self.sections = []          # dicts: start, end (byte offsets into face data), texture, material
        self.selections = {}        # name -> {faces: [..], vertices: [..]}
        self.properties = {}
        self.points, self.normals, self.uvs = [], [], []
        self.face_tex, self.face_mat = [], []   # per face, from sections


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


def read_odol(data):
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

    lods = []
    for i in range(nlods):
        lod = read_lod(Reader(data, starts[i]), ends[i])
        lod.resolution = resolutions[i]
        lods.append(lod)
    return {"version": version, "skeleton": skeleton, "bones": bones, "bbox": (bmin, bmax), "lods": lods}


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
    isz = 2 if npoints <= 0xFFFF else 4
    face_offsets = []
    for _ in range(nfaces):
        face_offsets.append(r.p - face_start)
        k = r.u8()
        lod.faces.append(list(struct.unpack_from(f"<{k}{'H' if isz == 2 else 'I'}", r.d, r.p)))
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
        faces = list(struct.unpack(f"<{nf}{fch}", r.compressed(nf, fsz))) if nf else []
        r.u32()
        r.u8()
        ns = r.u32()
        if ns:
            r.compressed(ns, 4)
        nv = r.u32()
        verts = list(struct.unpack(f"<{nv}{vch}", r.compressed(nv, vsz))) if nv else []
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
    lod.points = list(struct.iter_unpack("<3f", r.compressed(n, 12)))
    n = r.u32()
    packed = r.condensed(n, 4)
    lod.normals = [unpack_normal(v) for (v,) in struct.iter_unpack("<I", packed)]
    # tangents, bone weights and the rest of the vertex table are not needed
    if len(lod.points) != npoints:
        raise ValueError("point count mismatch")

    # texture and material per face from the sections (byte ranges of the face table)
    lod.face_tex = [""] * nfaces
    lod.face_mat = [""] * nfaces
    for s in lod.sections:
        tex = lod.textures[s["texture"]] if 0 <= s["texture"] < len(lod.textures) else ""
        mat = lod.materials[s["material"]]["path"] if 0 <= s["material"] < len(lod.materials) else ""
        for i, off in enumerate(face_offsets):
            if s["start"] <= off < s["end"]:
                lod.face_tex[i], lod.face_mat[i] = tex, mat
    return lod


def read_uvs(r, scale):
    n = r.u32()
    data = r.condensed(n, 4)
    u0, v0, u1, v1 = scale
    out = []
    for (u, v) in struct.iter_unpack("<hh", data):
        out.append(((u + 32767) / 65534 * (u1 - u0) + u0, (v + 32767) / 65534 * (v1 - v0) + v0))
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


def mlod_bytes(model, lods=None):
    out = bytearray(b"MLOD" + struct.pack("<II", 257, 0))
    chosen = [l for l in model["lods"] if lods is None or l.resolution in lods]
    struct.pack_into("<I", out, 8, len(chosen))
    for lod in chosen:
        # ODOL splits a point per normal/UV; weld them back by position so faces share vertices
        weld, points = {}, []
        remap = []
        for pt in lod.points:
            key = tuple(round(c, 6) for c in pt)
            if key not in weld:
                weld[key] = len(points)
                points.append(pt)
            remap.append(weld[key])
        normals = list(lod.normals) or [(0.0, 1.0, 0.0)]
        faces = []
        for i, idx in enumerate(lod.faces):
            verts = [(remap[vi], vi if vi < len(normals) else 0,
                      lod.uvs[vi][0] if vi < len(lod.uvs) else 0.0, lod.uvs[vi][1] if vi < len(lod.uvs) else 0.0) for vi in idx]
            faces.append([verts, lod.face_tex[i], lod.face_mat[i]])
        sel_faces = {name: set(sel["faces"]) for name, sel in lod.selections.items()}
        sel_points = {name: {remap[v] for v in sel["vertices"] if v < len(remap)} for name, sel in lod.selections.items()}
        # proxies: replace the stored placeholder triangle with one built from the proxy's transform
        names = list(lod.selections)
        for p in lod.proxies:
            name = names[p["selection"]] if p["selection"] < len(names) else f"proxy:{p['name']}.{p['id']:03d}"
            tri = proxy_triangle(p)
            base = len(points)
            points += tri
            normals.append((0.0, 1.0, 0.0))
            ni = len(normals) - 1
            verts = [(base, ni, 0.0, 0.0), (base + 1, ni, 0.0, 0.0), (base + 2, ni, 0.0, 0.0)]
            old = sorted(sel_faces.get(name, ()))
            if old:
                faces[old[0]] = [verts, "", ""]
                for extra in old[1:]:
                    faces[extra] = [[], "", ""]          # dropped below
                sel_faces[name] = {old[0]}
            else:
                faces.append([verts, "", ""])
                sel_faces[name] = {len(faces) - 1}
            sel_points[name] = {base, base + 1, base + 2}
        keep = [i for i, f in enumerate(faces) if f[0]]
        renum = {old: new for new, old in enumerate(keep)}
        faces = [faces[i] for i in keep]
        sel_faces = {n: {renum[i] for i in fs if i in renum} for n, fs in sel_faces.items()}

        out += b"P3DM" + struct.pack("<IIIIII", 28, 256, len(points), len(normals), len(faces), 0)
        for x, y, z in points:
            out += struct.pack("<fffI", x, y, z, 0)
        for x, y, z in normals:
            out += struct.pack("<fff", x, y, z)
        for verts, tex, mat in faces:
            out += struct.pack("<I", len(verts))
            for k in range(4):
                pi, ni, u, v = verts[k] if k < len(verts) else (0, 0, 0.0, 0.0)
                out += struct.pack("<IIff", pi, ni, u, v)
            out += struct.pack("<I", 0) + tex.encode("latin1") + b"\0" + mat.encode("latin1") + b"\0"
        out += b"TAGG"
        for name in sel_faces:
            body = bytes(1 if i in sel_points.get(name, ()) else 0 for i in range(len(points)))
            body += bytes(1 if i in sel_faces[name] else 0 for i in range(len(faces)))
            out += b"\x01" + name.encode("latin1") + b"\0" + struct.pack("<I", len(body)) + body
        for k, v in lod.properties.items():
            body = k.encode("latin1")[:63].ljust(64, b"\0") + v.encode("latin1")[:63].ljust(64, b"\0")
            out += b"\x01#Property#\0" + struct.pack("<I", len(body)) + body
        out += b"\x01#EndOfFile#\0" + struct.pack("<I", 0)
        out += struct.pack("<f", lod.resolution)
    return bytes(out)


def write_mlod(model, path, lods=None):
    with open(path, "wb") as f:
        f.write(mlod_bytes(model, lods))


def convert(src, dst, keep_all=False):
    with open(src, "rb") as f:
        data = f.read()
    model = read_odol(data)
    write_mlod(model, dst, None if keep_all else {l.resolution for l in model["lods"] if l.resolution < VISUAL_LOD_MAX})
    return model


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="an ODOL .p3d, or a folder with --batch")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--batch", action="store_true", help="convert every ODOL .p3d under src in place, keeping the original as .odol")
    ap.add_argument("--all-lods", action="store_true", help="keep geometry, memory and other non-visual LODs too")
    a = ap.parse_args()
    if not os.path.exists(a.src):
        sys.exit(f"not found: {a.src}\n(in Git Bash, quote Windows paths or use forward slashes: \"F:\\PureZ\\PureZ-render\" or F:/PureZ/PureZ-render)")
    if a.batch:
        done = failed = skipped = 0
        for d, _, fs in os.walk(a.src):
            for f in fs:
                if not f.lower().endswith(".p3d"):
                    continue
                path = os.path.join(d, f)
                with open(path, "rb") as fh:
                    sig = fh.read(4)
                if sig != b"ODOL":
                    skipped += 1
                    continue
                try:
                    convert(path, path + ".tmp", a.all_lods)
                    os.replace(path, path[:-4] + ".odol")
                    os.replace(path + ".tmp", path)
                    done += 1
                except Exception as e:
                    failed += 1
                    print(f"failed  {os.path.relpath(path, a.src)}: {e}")
                    if os.path.exists(path + ".tmp"):
                        os.remove(path + ".tmp")
        print(f"converted {done}, failed {failed}, already MLOD {skipped}")
        if not (done or failed or skipped):
            print(f"no .p3d files under {a.src}")
    else:
        dst = a.dst or a.src[:-4] + ".mlod.p3d"
        model = convert(a.src, dst, a.all_lods)
        for l in model["lods"]:
            print(f"lod {l.resolution:g}: {len(l.points)} points, {len(l.faces)} faces, {len(l.proxies)} proxies, "
                  f"{len(l.selections)} selections, textures {[t for t in l.textures if t]}")
        print(f"wrote {dst}")


if __name__ == "__main__":
    main()
