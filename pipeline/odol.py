"""Read DayZ's binarised ODOL P3D models (versions 53 to 56) and convert them to MLOD.

    python pipeline/odol.py <model.p3d> [out.p3d]        # writes <model>.mlod.p3d by default
    python pipeline/odol.py --batch <folder>              # every ODOL under the folder, in place (original kept as .odol)

The layout was worked out from the game's own files, guided by armake's ODOL v71 writer. Each LOD gives
points, normals, UVs, faces with their texture and material, named selections, named properties and
proxies (with their full transform). Vertices that ODOL split per normal/UV are merged back into shared
points by position, so MLOD selections and the Blender import behave as if the model had never been
binarised. Arrays over 1023 bytes are LZO-compressed (lzo1x).
"""
import argparse, array, math, os, struct, sys, time

import numpy as np

try:
    import lzo as _lzo          # python-lzo: C decoder, several times faster than the pure-Python one
except ImportError:
    _lzo = None

VISUAL_LOD_MAX = 1000.0
MAX_FILE_MB = 160         # --batch skips bigger files unless --max-size raises this
MAX_LOD_MB = 64           # a single LOD bigger than this is skipped


MAX_ARRAY = 256 << 20     # no single array is bigger than this; larger counts mean a misparsed file
MAX_COUNT = 20_000_000


class Reader:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def count(self, limit=MAX_COUNT):
        """A u32 element count that must be plausible, so a misread never turns into a huge loop."""
        v = self.u32()
        if v > limit:
            raise ValueError(f"implausible count {v} at 0x{self.p - 4:x}")
        return v

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
        if _lzo is not None:
            out, used = lzo_c_decompress(self.d, self.p, expected)
        else:
            out, used = lzo1x_decompress(self.d, self.p, expected)
        self.p += used
        return out

    def compressed(self, count, size):
        n = count * size
        if n > MAX_ARRAY or n > 400 * (len(self.d) - self.p) + 4096:
            raise ValueError(f"array of {n} bytes at 0x{self.p:x} cannot fit the file")
        if n > 1023:
            return self.lzo(n)
        if self.p + n > len(self.d):
            raise ValueError(f"array runs past the end of the file at 0x{self.p:x}")
        return self.raw(n)

    def condensed(self, count, size):
        """CondensedArray: a fill flag, then one element for all, or the array."""
        if count == 0:
            return b""
        if self.u8():
            return self.raw(size) * count
        return self.compressed(count, size)


def lzo_c_decompress(src, ip, expected):
    """python-lzo does not report how much input it consumed, so find the block length by bisection:
    too short fails with an input overrun, too long with input not consumed, exact succeeds."""
    hi = min(len(src) - ip, expected + expected // 16 + 64)
    lo = 0
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        try:
            out = _lzo.decompress(bytes(src[ip:ip + mid]), False, expected)
            best = (out, mid)
            hi = mid - 1
        except _lzo.error as e:
            if "not consumed" in str(e).lower():
                hi = mid - 1
            else:
                lo = mid + 1
    if best is None:
        return lzo1x_decompress(src, ip, expected)
    return best


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
                if len(out) + t > expected + 64 or ip + t > len(src):
                    raise ValueError("LZO literal run longer than expected")
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
            if pos < 0 or pos >= len(out):
                raise ValueError("LZO match outside the block")
            if len(out) + length > expected + 64:
                raise ValueError("LZO block longer than expected")
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

    def face_counts(self):
        return np.frombuffer(self.face_n.tobytes(), dtype=np.uint8) if len(self.face_n) else np.zeros(0, np.uint8)

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
    nt, ntr = r.count(1000), r.count(1000)
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
    if not 53 <= version <= 56:
        raise ValueError(f"ODOL version {version} is not supported (DayZ ships 53 to 56)")
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
        try:
            lod = read_lod(Reader(data, starts[i]), ends[i])
        except Exception as e:
            raise ValueError(f"version {version}, LOD {res:g}: {e}") from e
        lod.resolution = res
        lods.append(lod)
    if skipped and log:
        log(f"  skipped {len(skipped)} LOD(s) over {max_lod_bytes >> 20} MB: {', '.join(f'{x:g}' for x in skipped)}")
    return {"version": version, "skeleton": skeleton, "bones": bones, "bbox": (bmin, bmax), "lods": lods, "skipped": skipped}


def read_lod(r, end):
    lod = Lod()
    for _ in range(r.count(100000)):
        p = {"name": r.asciiz()}
        p["x"], p["y"], p["z"], p["n"] = r.f32(3), r.f32(3), r.f32(3), r.f32(3)
        p["id"], p["selection"], p["bone"], p["section"] = r.u32(), r.u32(), r.i32(), r.u32()
        lod.proxies.append(p)
    r.raw(4 * r.count())                       # subskeleton -> skeleton
    for _ in range(r.count()):                 # skeleton -> subskeleton links
        r.raw(4 * r.count())
    npoints = r.count()
    r.f32(); r.u32(); r.u32(); r.f32(3); r.f32(3); r.f32(3); r.f32()
    lod.textures = [r.asciiz() for _ in range(r.count(100000))]
    lod.materials = [read_material(r) for _ in range(r.count(100000))]
    n = r.u32()
    if n:
        r.compressed(n, 2)                     # point -> vertex
    n = r.u32()
    if n:
        r.compressed(n, 2)                     # vertex -> point
    nfaces, alloc = r.count(), r.u32()
    if alloc > len(r.d) - r.p:
        raise ValueError(f"face table of {alloc} bytes at 0x{r.p:x} runs past the end of the file")
    r.u16()
    face_start = r.p
    # vertex indices are 16-bit unless the LOD has more than 65535 vertices (then 32-bit)
    isz, ich = (2, "H") if npoints <= 0xFFFF else (4, "I")
    d = r.d
    uniform = next((k for k in (3, 4) if nfaces * (1 + k * isz) in (alloc, alloc - nfaces)), None)
    if uniform and all(d[face_start + i * (1 + uniform * isz)] == uniform for i in range(0, nfaces, max(1, nfaces // 64))):
        rec = np.dtype([("n", "u1"), ("v", f"<{ich.lower() if ich == 'I' else 'u2'}", (uniform,))]) if ich == "H" else np.dtype([("n", "u1"), ("v", "<u4", (uniform,))])
        table = np.frombuffer(d, dtype=rec, count=nfaces, offset=face_start)
        if not np.all(table["n"] == uniform):
            uniform = None
        else:
            lod.face_n = array.array("B", bytes([uniform]) * nfaces)
            lod.face_v = array.array("I", table["v"].astype("<u4").reshape(-1).tobytes())
            face_offsets = np.arange(nfaces, dtype=np.uint32) * (1 + uniform * isz)
            r.p = face_start + nfaces * (1 + uniform * isz)
    if not uniform:
        face_offsets = array.array("I")
        for _ in range(nfaces):
            face_offsets.append(r.p - face_start)
            k = d[r.p]; r.p += 1
            lod.face_n.append(k)
            lod.face_v.extend(struct.unpack_from(f"<{k}{ich}", d, r.p))
            r.p += k * isz
    if r.p - face_start != alloc and r.p - face_start + nfaces != alloc:
        raise ValueError(f"face table size {r.p - face_start} != allocation {alloc}")
    for _ in range(r.count(1000000)):          # sections
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
    for _ in range(r.count(100000)):           # named selections
        name = r.asciiz()
        nf = r.count()
        faces = array.array("I", struct.unpack(f"<{nf}{fch}", r.compressed(nf, fsz))) if nf else array.array("I")
        r.u32()
        r.u8()
        ns = r.count()
        if ns:
            r.compressed(ns, 4)
        nv = r.count()
        verts = array.array("I", struct.unpack(f"<{nv}{vch}", r.compressed(nv, vsz))) if nv else array.array("I")
        nw = r.count()
        weights = r.compressed(nw, 1) if nw else b""
        lod.selections[name] = {"faces": faces, "vertices": verts, "weights": weights}
    for _ in range(r.count(100000)):
        key = r.asciiz()
        lod.properties[key] = r.asciiz()
    nframes = r.count(100000)
    for _ in range(nframes):
        r.f32(); r.f32(npoints)
    r.u32(); r.u32(); r.u32(); r.u8()          # icon colour, selected colour, flags, simple bone refs
    r.u32()                                    # vertex table size
    r.condensed(r.count(), 4)                  # point flags
    uv_scale = r.f32(4)
    lod.uvs = read_uvs(r, uv_scale)
    for _ in range(r.count(64) - 1):           # further UV sets
        read_uvs(r, r.f32(4))
    n = r.count()
    lod.points = np.frombuffer(r.compressed(n, 12), dtype="<f4").copy()
    n = r.count()
    packed = np.frombuffer(r.condensed(n, 4), dtype="<u4")
    comps = np.stack([(packed >> sh) & 0x3FF for sh in (0, 10, 20)], axis=1).astype(np.int32)
    comps = np.where(comps & 0x200, comps - 1024, comps)
    lod.normals = (comps / -511.0).astype("<f4").reshape(-1)
    # tangents, bone weights and the rest of the vertex table are not needed
    if lod.npoints != npoints:
        raise ValueError("point count mismatch")

    # texture and material per face from the sections (byte ranges of the face table)
    offs = np.asarray(face_offsets, dtype=np.int64)
    tex = np.full(nfaces, -1, np.int16)
    mat = np.full(nfaces, -1, np.int16)
    for sec in lod.sections:
        m = (offs >= sec["start"]) & (offs < sec["end"])
        tex[m] = sec["texture"] if 0 <= sec["texture"] < len(lod.textures) else -1
        mat[m] = sec["material"] if 0 <= sec["material"] < len(lod.materials) else -1
    lod.face_tex, lod.face_mat = tex, mat
    return lod


def read_uvs(r, scale):
    n = r.count()
    raw = np.frombuffer(r.condensed(n, 4), dtype="<i2").reshape(-1, 2).astype(np.float32)
    u0, v0, u1, v1 = scale
    out = (raw + 32767.0) * np.array([(u1 - u0) / 65534, (v1 - v0) / 65534], dtype=np.float32) + np.array([u0, v0], dtype=np.float32)
    return out.astype("<f4").reshape(-1)


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


FACE_REC = np.dtype([("n", "<u4")] + [(f"{k}{i}", t) for i in range(4) for k, t in (("p", "<u4"), ("nm", "<u4"), ("u", "<f4"), ("v", "<f4"))] + [("flags", "<u4")])


def mlod_chunks(model, lods=None):
    """Yield the MLOD file as chunks, one LOD at a time, so a big model never sits in memory twice."""
    chosen = [l for l in model["lods"] if lods is None or l.resolution in lods]
    yield b"MLOD" + struct.pack("<II", 257, len(chosen))
    for lod in chosen:
        npts_src = lod.npoints
        P = np.asarray(lod.points, dtype=np.float32).reshape(-1, 3)
        # ODOL splits a point per normal/UV; weld them back by position so faces share vertices
        if npts_src:
            uniq, remap = np.unique(np.round(P, 6), axis=0, return_inverse=True)
            remap = remap.reshape(-1).astype(np.uint32)
            order = np.full(len(uniq), npts_src, np.int64)   # first source vertex of each welded point, keeps order stable
            np.minimum.at(order, remap, np.arange(npts_src))
            keep = np.argsort(order)
            rank = np.empty_like(keep); rank[keep] = np.arange(len(keep))
            remap = rank[remap].astype(np.uint32)
            points = P[order[keep]]
        else:
            remap, points = np.zeros(0, np.uint32), np.zeros((0, 3), np.float32)
        normals = np.asarray(lod.normals, dtype=np.float32).reshape(-1, 3) if len(lod.normals) else np.array([[0.0, 1.0, 0.0]], np.float32)
        nnorm = len(normals)
        uvs = np.asarray(lod.uvs, dtype=np.float32).reshape(-1, 2) if len(lod.uvs) else np.zeros((0, 2), np.float32)

        # faces: fixed-size records, plus the texture/material names per face
        counts = lod.face_counts().astype(np.int64)
        nf = len(counts)
        starts = np.concatenate([[0], np.cumsum(counts)[:-1]]) if nf else np.zeros(0, np.int64)
        fv = np.frombuffer(lod.face_v.tobytes(), dtype="<u4").astype(np.int64) if len(lod.face_v) else np.zeros(0, np.int64)
        rec = np.zeros(nf, FACE_REC)
        rec["n"] = counts
        for k in range(4):
            has = counts > k
            idx = fv[np.minimum(starts + k, max(len(fv) - 1, 0))] if len(fv) else np.zeros(nf, np.int64)
            vi = np.where(has, idx, 0)
            rec[f"p{k}"] = np.where(has, remap[np.minimum(vi, max(npts_src - 1, 0))] if npts_src else 0, 0)
            rec[f"nm{k}"] = np.where(has & (vi < nnorm), vi, 0)
            if len(uvs):
                uv = uvs[np.minimum(vi, len(uvs) - 1)]
                rec[f"u{k}"] = np.where(has, uv[:, 0], 0.0)
                rec[f"v{k}"] = np.where(has, uv[:, 1], 0.0)
        tex_i = np.asarray(lod.face_tex, dtype=np.int64)
        mat_i = np.asarray(lod.face_mat, dtype=np.int64)
        tails = {}
        for ti in np.unique(tex_i):
            for mi in np.unique(mat_i[tex_i == ti]):
                tails[(int(ti), int(mi))] = ((lod.textures[ti].encode("latin1") if ti >= 0 else b"") + b"\0"
                                            + (lod.materials[mi]["path"].encode("latin1") if mi >= 0 else b"") + b"\0")
        sel_faces = {name: set(sel["faces"].tolist()) for name, sel in lod.selections.items()}
        sel_points = {name: set(remap[np.asarray(sel["vertices"], dtype=np.int64)[np.asarray(sel["vertices"], dtype=np.int64) < npts_src]].tolist())
                      for name, sel in lod.selections.items()}

        # proxies: replace the stored placeholder triangle with one built from the proxy's transform
        names = list(lod.selections)
        extra_pts, extra_nrm, extra_faces, drop = [], [], [], set()
        for p in lod.proxies:
            name = names[p["selection"]] if p["selection"] < len(names) else f"proxy:{p['name']}.{p['id']:03d}"
            base = len(points) + len(extra_pts)
            extra_pts += proxy_triangle(p)
            ni = nnorm + len(extra_nrm)
            extra_nrm.append((0.0, 1.0, 0.0))
            face = np.zeros(1, FACE_REC)
            face["n"] = 3
            for k in range(3):
                face[f"p{k}"], face[f"nm{k}"] = base + k, ni
            old = sorted(sel_faces.get(name, ()))
            if old:
                rec[old[0]] = face[0]
                tails_key = (-1, -1)
                tex_i[old[0]], mat_i[old[0]] = -1, -1
                drop |= set(old[1:])
                sel_faces[name] = {old[0]}
            else:
                extra_faces.append(face[0])
                sel_faces[name] = {nf + len(extra_faces) - 1}
            sel_points[name] = {base, base + 1, base + 2}
        tails[(-1, -1)] = b"\0\0"
        if extra_pts:
            points = np.concatenate([points, np.array(extra_pts, np.float32)])
            normals = np.concatenate([normals, np.array(extra_nrm, np.float32)])
        if extra_faces:
            rec = np.concatenate([rec, np.array(extra_faces, FACE_REC)])
            tex_i = np.concatenate([tex_i, np.full(len(extra_faces), -1)])
            mat_i = np.concatenate([mat_i, np.full(len(extra_faces), -1)])
        if drop:
            keep_f = np.array([i for i in range(len(rec)) if i not in drop])
            renum = {int(old): new for new, old in enumerate(keep_f)}
            rec, tex_i, mat_i = rec[keep_f], tex_i[keep_f], mat_i[keep_f]
            sel_faces = {n: {renum[i] for i in fs if i in renum} for n, fs in sel_faces.items()}
        npts, nfaces_out = len(points), len(rec)

        head = bytearray(b"P3DM" + struct.pack("<IIIIII", 28, 256, npts, len(normals), nfaces_out, 0))
        pts_rec = np.zeros(npts, np.dtype([("xyz", "<f4", (3,)), ("f", "<u4")]))
        pts_rec["xyz"] = points
        head += pts_rec.tobytes()
        head += normals.astype("<f4").tobytes()
        yield bytes(head)
        fixed = rec.tobytes()
        sz = FACE_REC.itemsize
        mv = memoryview(fixed)
        keys = list(zip(tex_i.tolist(), mat_i.tolist()))
        for i0 in range(0, nfaces_out, 50000):
            yield b"".join(bytes(mv[i * sz:(i + 1) * sz]) + tails[keys[i]] for i in range(i0, min(nfaces_out, i0 + 50000)))
        out = bytearray(b"TAGG")
        for name in sel_faces:
            body = np.zeros(npts + nfaces_out, np.uint8)
            pi = np.fromiter(sel_points.get(name, ()), dtype=np.int64)
            fi = np.fromiter(sel_faces[name], dtype=np.int64)
            body[pi[pi < npts]] = 1
            body[npts + fi[fi < nfaces_out]] = 1
            out += b"\x01" + name.encode("latin1") + b"\0" + struct.pack("<I", len(body)) + body.tobytes()
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


def _convert_job(path, keep_all, max_lod_bytes):
    """Runs in a worker process: convert one file, return (lods, faces) or raise."""
    lower_priority()
    model = convert(path, path + ".tmp", keep_all, max_lod_bytes)
    return len(model["lods"]), sum(l.nfaces for l in model["lods"]), [f"{x:g}" for x in model["skipped"]]


def batch(a):
    import concurrent.futures as cf
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
    print("LZO decoder: " + ("python-lzo (C)" if _lzo else "pure Python (pip install python-lzo for a faster run)")
          + f", {a.workers} worker(s), {a.timeout} s per file")
    log = open(os.path.join(a.src, "odol.log"), "a", encoding="utf-8")
    done, failed, t0 = 0, [], time.time()
    samples = {}          # (version, first words of the error) -> smallest failing file so far
    if a.collect_samples:
        os.makedirs(a.collect_samples, exist_ok=True)

    def finish(path, outcome, detail):
        nonlocal done
        rel = os.path.relpath(path, a.src)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        if outcome == "ok":
            os.replace(path, path[:-4] + ".odol")
            os.replace(path + ".tmp", path)
            done += 1
            print(f"    ok  {rel}: {detail}", flush=True)
            log.write(f"ok  {rel}\n")
        else:
            failed.append(rel)
            print(f"    FAILED {rel}: {detail}", flush=True)
            log.write(f"FAIL {rel}: {detail}\n")
            try:
                with open(path, "rb") as fh:
                    ver = struct.unpack("<4xI", fh.read(8))[0]
            except Exception:
                ver = 0
            key = (ver, " ".join(str(detail).split()[:5]))
            if a.collect_samples and (key not in samples or size < os.path.getsize(samples[key])):
                import shutil
                samples[key] = path
                shutil.copy2(path, os.path.join(a.collect_samples, f"v{ver}_{os.path.basename(path)}"))
        if os.path.exists(path + ".tmp"):
            os.remove(path + ".tmp")
        log.flush()

    pool = cf.ProcessPoolExecutor(max_workers=a.workers)
    pending = {}
    queue = list(todo)
    started = 0
    try:
        while queue or pending:
            while queue and len(pending) < a.workers:
                path = queue.pop(0)
                started += 1
                elapsed = time.time() - t0
                rate = (done + len(failed)) / elapsed if elapsed > 0 and (done + len(failed)) else 0
                eta = (len(todo) - done - len(failed)) / rate / 60 if rate else 0
                print(f"[{started}/{len(todo)}] {os.path.relpath(path, a.src)} ({os.path.getsize(path) / 1048576:.1f} MB)"
                      f"  {elapsed / 60:.0f} min elapsed, about {eta:.0f} min left", flush=True)
                fut = pool.submit(_convert_job, path, a.all_lods, a.max_lod << 20)
                pending[fut] = (path, time.time())
            donefs, _ = cf.wait(list(pending), timeout=1, return_when=cf.FIRST_COMPLETED)
            for fut in donefs:
                path, t1 = pending.pop(fut)
                try:
                    nl, nf, skipped = fut.result()
                    finish(path, "ok", f"{nl} LOD(s), {nf} faces, {time.time() - t1:.1f} s" + (f", skipped big LOD(s) {', '.join(skipped)}" if skipped else ""))
                except Exception as e:
                    finish(path, "fail", "out of memory" if isinstance(e, MemoryError) else str(e))
            # a file that runs past the timeout is abandoned with its worker
            stuck = [f for f, (path, t1) in pending.items() if time.time() - t1 > a.timeout]
            if stuck:
                for f in stuck:
                    path, _ = pending.pop(f)
                    finish(path, "fail", f"no result after {a.timeout} s, worker restarted")
                for proc in list(pool._processes.values()):
                    proc.terminate()
                pool.shutdown(wait=False, cancel_futures=True)
                for f, (path, _) in pending.items():
                    queue.insert(0, path)
                pending.clear()
                pool = cf.ProcessPoolExecutor(max_workers=a.workers)
            time.sleep(a.sleep)
    except KeyboardInterrupt:
        print("\nstopped; run again to continue where it left off", flush=True)
        for proc in list(pool._processes.values()):
            proc.terminate()
    pool.shutdown(wait=False, cancel_futures=True)
    log.close()
    print(f"\nconverted {done}, failed {len(failed)}, already MLOD {already}, skipped as too big {len(skipped_big)}"
          f"  ({(time.time() - t0) / 60:.0f} min)")
    if skipped_big:
        print("too big for the default limit (raise --max-size to include them):")
        for p in skipped_big[:20]:
            print("  " + os.path.relpath(p, a.src))
    if failed:
        by_ver = {}
        for (ver, _), path in samples.items():
            by_ver.setdefault(ver, []).append(path)
        print(f"{len(failed)} failed (listed in odol.log)" + (": " + ", ".join(f"v{v}: {len(ps)} kind(s)" for v, ps in sorted(by_ver.items())) if by_ver else ""))
        if a.collect_samples:
            print(f"one sample of each failing kind is in {a.collect_samples}: commit that folder so the decoder can be extended")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="an ODOL .p3d, or a folder with --batch")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--batch", action="store_true", help="convert every ODOL .p3d under src in place, keeping the original as .odol")
    ap.add_argument("--all-lods", action="store_true", help="keep geometry, memory and other non-visual LODs too")
    ap.add_argument("--max-size", type=int, default=MAX_FILE_MB, help=f"skip files bigger than this many MB (default {MAX_FILE_MB})")
    ap.add_argument("--max-lod", type=int, default=MAX_LOD_MB, help=f"skip a single LOD bigger than this many MB (default {MAX_LOD_MB})")
    ap.add_argument("--sleep", type=float, default=0.05, help="seconds to pause between files (default 0.05)")
    ap.add_argument("--limit", type=int, help="stop after this many conversions (for a trial run)")
    ap.add_argument("--collect-samples", metavar="DIR", help="copy the smallest failing file of each version/error into DIR")
    ap.add_argument("--workers", type=int, default=2, help="files converted at once (default 2)")
    ap.add_argument("--timeout", type=int, default=180, help="seconds allowed per file before it is abandoned (default 180)")
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
