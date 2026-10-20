"""Convert DayZ P3D models to GLB for Blender and the web.

    python pipeline/p3d.py <model.p3d> <out.glb> --root <render folder> [--hidden camo=path.paa ...]

Reads MLOD (editable) P3D; the game ships binarised ODOL models, so debinarise them first with
pipeline/deodol.py. The best visual LOD is exported with its textures embedded: colour (_co/_ca),
normal (_nohq) and specular/gloss (_smdi -> glTF metallic-roughness). `--hidden` applies config
hiddenSelectionsTextures for colour variants. Proxy triangles (attachment slots) are read by
proxies() and never drawn; layout.py fills them with the attachments a weapon spawns with.
"""
import argparse, io, json, math, os, re, struct, sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paa  # noqa: E402

VISUAL_LOD_MAX = 1000.0  # resolutions below this are graphical LODs


# ----------------------------------------------------------------------------- reading
class Reader:
    def __init__(self, data):
        self.d, self.p = data, 0

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def f32(self, n=1):
        v = struct.unpack_from(f"<{n}f", self.d, self.p)
        self.p += 4 * n
        return v if n > 1 else v[0]

    def raw(self, n):
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def asciiz(self):
        e = self.d.index(b"\0", self.p)
        v = self.d[self.p:e].decode("utf-8", "replace")
        self.p = e + 1
        return v


class Lod:
    def __init__(self):
        self.points, self.normals, self.faces = [], [], []
        self.selections, self.resolution = {}, 0.0


def read_mlod(data):
    r = Reader(data)
    if r.raw(4) != b"MLOD":
        raise ValueError("not an MLOD")
    r.u32()  # version
    lods = []
    for _ in range(r.u32()):
        lod = Lod()
        sig = r.raw(4)
        if sig != b"P3DM":
            raise ValueError(f"unsupported LOD signature {sig!r}")
        r.u32(), r.u32()  # major, minor
        npts, nnorm, nface = r.u32(), r.u32(), r.u32()
        r.u32()  # flags
        for _ in range(npts):
            x, y, z = r.f32(3)
            r.u32()
            lod.points.append((x, y, z))
        for _ in range(nnorm):
            lod.normals.append(r.f32(3))
        for _ in range(nface):
            n = r.u32()
            verts = []
            for i in range(4):
                pi, ni = r.u32(), r.u32()
                u, v = r.f32(2)
                if i < n:
                    verts.append((pi, ni, u, v))
            r.u32()  # face flags
            tex, mat = r.asciiz(), r.asciiz()
            lod.faces.append({"v": verts, "tex": tex, "mat": mat})
        if r.raw(4) != b"TAGG":
            raise ValueError("missing TAGG")
        while True:
            r.raw(1)
            name = r.asciiz()
            size = r.u32()
            body = r.raw(size)
            if name == "#EndOfFile#":
                break
            if not name.startswith("#"):  # named selection: point weights then face flags
                faces = body[npts:npts + nface]
                lod.selections[name.lower()] = {i for i, b in enumerate(faces) if b}
        lod.resolution = r.f32()
        lods.append(lod)
    return lods


def read_p3d(path):
    with open(path, "rb") as f:
        data = f.read()
    sig = data[:4]
    if sig == b"MLOD":
        return read_mlod(data)
    if sig == b"ODOL":
        import odol
        model = odol.read_odol(data, "visual")
        return read_mlod(odol.mlod_bytes(model))
    raise ValueError(f"unknown P3D signature {sig!r}")


def proxies(lod):
    """Proxy placeholders in a LOD: [{name, path, index, faces, origin, frame}]. The game swaps each
    for the model in the matching slot; path is the placeholder model (dz\\weapons\\...\\x, no .p3d)."""
    out = []
    for name, faces in lod.selections.items():
        m = re.match(r"proxy:(.+?)(?:\.(\d+))?$", name)
        if not m or not faces:
            continue
        verts = [lod.points[v[0]] for i in sorted(faces) for v in lod.faces[i]["v"]]
        origin, frame = proxy_frame(verts[:3]) if len(verts) >= 3 else ((0.0, 0.0, 0.0), None)
        out.append({"name": name, "path": m.group(1).replace("/", "\\").lstrip("\\"), "index": int(m.group(2) or 1),
                    "faces": set(faces), "origin": origin, "frame": frame})
    return out


def proxy_frame(tri):
    """Frame of a proxy triangle: the right-angle vertex is the origin; the longer leg points forward
    (+Z), the shorter one up (+Y). Returns (origin, [X, Y, Z]) in model space (Arma, left-handed)."""
    best = None
    for i in range(3):
        o, a, b = tri[i], tri[(i + 1) % 3], tri[(i + 2) % 3]
        la, lb = sub(a, o), sub(b, o)
        c = abs(dot(norm(la), norm(lb)))
        if best is None or c < best[0]:
            best = (c, o, la, lb)
    _, o, la, lb = best
    z, y = (la, lb) if dot(la, la) >= dot(lb, lb) else (lb, la)
    z, y = norm(z), norm(y)
    x = norm(cross(y, z))
    y = norm(cross(z, x))
    return o, [x, y, z]


def apply_frame(p, origin, frame):
    if frame is None:
        return (p[0] + origin[0], p[1] + origin[1], p[2] + origin[2])
    x, y, z = frame
    return (origin[0] + p[0] * x[0] + p[1] * y[0] + p[2] * z[0],
            origin[1] + p[0] * x[1] + p[1] * y[1] + p[2] * z[1],
            origin[2] + p[0] * x[2] + p[1] * y[2] + p[2] * z[2])


def rotate(p, frame):
    if frame is None:
        return p
    x, y, z = frame
    return (p[0] * x[0] + p[1] * y[0] + p[2] * z[0], p[0] * x[1] + p[1] * y[1] + p[2] * z[1], p[0] * x[2] + p[1] * y[2] + p[2] * z[2])


def best_lod(lods):
    visual = [l for l in lods if l.resolution < VISUAL_LOD_MAX and l.faces]
    return min(visual, key=lambda l: l.resolution) if visual else None


# ----------------------------------------------------------------------------- textures
class Files:
    """Case-insensitive lookup of game paths (dz\\weapons\\...) under the render folder."""

    def __init__(self, root):
        self.root, self.index = root, {}
        for d, _, fs in os.walk(root):
            for f in fs:
                full = os.path.join(d, f)
                self.index[os.path.relpath(full, root).replace("\\", "/").lower()] = full

    def find(self, game_path):
        """The game path's file, or its .odol twin when only the binarised original is left (interrupted conversion)."""
        if not game_path or game_path.startswith("#"):
            return None
        key = game_path.replace("\\", "/").lstrip("/").lower()
        return self.index.get(key) or (self.index.get(key[:-4] + ".odol") if key.endswith(".p3d") else None)


def rvmat_textures(path):
    """Stage textures of a material by role, from text or binarised rvmat."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extract"))
        from configs import Cls, load_one
        root = Cls("")
        load_one(path, root)
    except Exception:
        return {}
    roles = {}
    for c in root.children.values():
        t = c.get("texture")
        if isinstance(t, str):
            low = t.lower()
            for suffix, role in (("_nohq", "normal"), ("_smdi", "smdi"), ("_as", "ao")):
                if suffix in low:
                    roles.setdefault(role, t)
    return roles


def png_bytes(img):
    b = io.BytesIO()
    img.save(b, "PNG", optimize=True)
    return b.getvalue()


def load_texture(files, game_path, cache):
    if game_path in cache:
        return cache[game_path]
    img = None
    src = files.find(game_path)
    if src:
        try:
            img = paa.read_paa(src) if src.lower().endswith(".paa") else Image.open(src).convert("RGBA")
        except Exception as e:
            print(f"  texture {game_path}: {e}", file=sys.stderr)
    m = re.match(r"#\(argb,\d+,\d+,\d+\)color\(([\d.]+),([\d.]+),([\d.]+),([\d.]+)", game_path or "")
    if img is None and m:  # procedural solid colour
        rgba = tuple(int(float(x) * 255) for x in m.groups())
        img = Image.new("RGBA", (4, 4), rgba)
    cache[game_path] = img
    return img


def smdi_to_mr(img):
    """DayZ SMDI (G = specular, B = gloss) -> glTF metallic-roughness (G = roughness, B = metal)."""
    _, g, b, _ = img.convert("RGBA").split()
    rough = b.point(lambda v: 255 - v)
    zero = Image.new("L", img.size, 0)
    return Image.merge("RGB", (zero, rough, zero))


# ----------------------------------------------------------------------------- GLB
class Glb:
    def __init__(self):
        self.bin = bytearray()
        self.gltf = {"asset": {"version": "2.0", "generator": "PureZ p3d.py"}, "scene": 0,
                     "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "model"}],
                     "meshes": [{"primitives": []}], "accessors": [], "bufferViews": [],
                     "materials": [], "textures": [], "images": [], "samplers": [{"magFilter": 9729, "minFilter": 9987}]}

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.gltf["bufferViews"].append(v)
        return len(self.gltf["bufferViews"]) - 1

    def accessor(self, values, kind, comp=5126, target=34962, minmax=False):
        width = {"VEC3": 3, "VEC2": 2, "SCALAR": 1}[kind]
        fmt = "<" + ("f" if comp == 5126 else "I") * len(values)
        flat = values
        acc = {"bufferView": self.view(struct.pack(fmt, *flat), target), "componentType": comp,
               "count": len(values) // width, "type": kind}
        if minmax:
            cols = [flat[i::width] for i in range(width)]
            acc["min"], acc["max"] = [min(c) for c in cols], [max(c) for c in cols]
        self.gltf["accessors"].append(acc)
        return len(self.gltf["accessors"]) - 1

    def image(self, img):
        self.gltf["images"].append({"bufferView": self.view(png_bytes(img)), "mimeType": "image/png"})
        self.gltf["textures"].append({"source": len(self.gltf["images"]) - 1, "sampler": 0})
        return len(self.gltf["textures"]) - 1

    def save(self, path):
        g = self.gltf
        for k in ("textures", "images", "materials"):
            if not g[k]:
                del g[k]
        g["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(g, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        binb = bytes(self.bin) + b"\0" * (-len(self.bin) % 4)
        total = 12 + 8 + len(js) + 8 + len(binb)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            f.write(struct.pack("<II", len(binb), 0x004E4942) + binb)


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(v):
    l = math.sqrt(dot(v, v)) or 1.0
    return (v[0] / l, v[1] / l, v[2] / l)


def export(lod, out, files, hidden=None, materials_override=None, parts=None):
    """Write a GLB. hidden: {selection: texture} from hiddenSelectionsTextures.
    parts: attachments to merge in, [{"lod", "hidden", "mats", "origin", "frame", "name"}], each placed
    at its proxy's origin (and frame, when given) in the main model's space."""
    items = [{"lod": lod, "hidden": hidden, "mats": materials_override, "origin": (0.0, 0.0, 0.0), "frame": None, "name": "model"}]
    items += parts or []
    groups, total_faces = {}, 0
    for it in items:
        L = it["lod"]
        hid = {k.lower(): v for k, v in (it.get("hidden") or {}).items()}
        mats = {k.lower(): v for k, v in (it.get("mats") or {}).items()}
        face_tex = [f["tex"] for f in L.faces]
        face_mat = [f["mat"] for f in L.faces]
        for sel, tex in hid.items():
            for i in L.selections.get(sel, ()):
                face_tex[i] = tex
                if sel in mats:
                    face_mat[i] = mats[sel]
        skip = set()
        for px in proxies(L):
            skip |= px["faces"]
        # Arma is left-handed with +Z forward: mirror X to keep the model's front at glTF +Z
        pts = [apply_frame(p, it["origin"], it["frame"]) for p in L.points]
        pts = [(-x, y, z) for x, y, z in pts]
        nrm = [rotate(n, it["frame"]) for n in L.normals]
        nrm = [(-x, y, z) for x, y, z in nrm]
        # Some exporters store inward normals: compare with geometric normals and flip if needed
        agree = 0.0
        for f in L.faces:
            v = f["v"]
            if len(v) < 3 or not L.normals:
                continue
            g = norm(cross(sub(pts[v[1][0]], pts[v[0][0]]), sub(pts[v[2][0]], pts[v[0][0]])))
            agree += dot(g, nrm[v[0][1]])
        flip = agree < 0
        for i, f in enumerate(L.faces):
            if i in skip:
                continue
            groups.setdefault((face_tex[i], face_mat[i]), []).append((f, pts, nrm, flip, bool(L.normals)))
            total_faces += 1

    glb, cache = Glb(), {}
    for (tex, mat), faces in groups.items():
        P, N, T, idx, seen = [], [], [], [], {}
        for f, pts, nrm, flip_normals, has_normals in faces:
            v = f["v"]
            tris = [(0, 1, 2)] if len(v) == 3 else [(0, 1, 2), (0, 2, 3)]
            for tri in tris:
                corners = [v[k] for k in tri]
                g = norm(cross(sub(pts[corners[1][0]], pts[corners[0][0]]), sub(pts[corners[2][0]], pts[corners[0][0]])))
                n0 = nrm[corners[0][1]] if has_normals else g
                n0 = (-n0[0], -n0[1], -n0[2]) if flip_normals else n0
                if dot(g, n0) < 0:  # make winding agree with the normal (counter-clockwise front)
                    corners = [corners[0], corners[2], corners[1]]
                for pi, ni, u, vv in corners:
                    n = nrm[ni] if has_normals else g
                    if flip_normals:
                        n = (-n[0], -n[1], -n[2])
                    key = (id(pts), pi, ni, round(u, 6), round(vv, 6))
                    if key not in seen:
                        seen[key] = len(P) // 3
                        P += pts[pi]
                        N += norm(n)
                        T += (u, vv)
                    idx.append(seen[key])
        prim = {"attributes": {"POSITION": glb.accessor(P, "VEC3", minmax=True),
                               "NORMAL": glb.accessor(N, "VEC3"), "TEXCOORD_0": glb.accessor(T, "VEC2")},
                "indices": glb.accessor(idx, "SCALAR", comp=5125, target=34963)}

        material = {"name": os.path.basename(tex or "untextured"), "doubleSided": True,
                    "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.75}}
        color = load_texture(files, tex, cache)
        if color is not None:
            material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": glb.image(color)}
            if "_ca" in (tex or "").lower() and color.getextrema()[3][0] < 250:
                material["alphaMode"], material["alphaCutoff"] = "MASK", 0.5
        roles = rvmat_textures(files.find(mat)) if files.find(mat) else {}
        if roles.get("normal"):
            n_img = load_texture(files, roles["normal"], cache)
            if n_img is not None:
                material["normalTexture"] = {"index": glb.image(n_img.convert("RGB"))}
        if roles.get("smdi"):
            s_img = load_texture(files, roles["smdi"], cache)
            if s_img is not None:
                material["pbrMetallicRoughness"]["metallicRoughnessTexture"] = {"index": glb.image(smdi_to_mr(s_img))}
                material["pbrMetallicRoughness"]["roughnessFactor"] = 1.0
        glb.gltf["materials"].append(material)
        prim["material"] = len(glb.gltf["materials"]) - 1
        glb.gltf["meshes"][0]["primitives"].append(prim)
    glb.save(out)
    return {"faces": total_faces, "materials": len(groups), "parts": [it["name"] for it in items[1:]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("p3d")
    ap.add_argument("out")
    ap.add_argument("--root", required=True, help="render folder containing DZ/")
    ap.add_argument("--hidden", nargs="*", default=[], help="selection=texture overrides")
    a = ap.parse_args()
    files = Files(a.root)
    lod = best_lod(read_p3d(a.p3d))
    if not lod:
        sys.exit("no visual LOD")
    hidden = dict(h.split("=", 1) for h in a.hidden)
    print(export(lod, a.out, files, hidden))


if __name__ == "__main__":
    main()
