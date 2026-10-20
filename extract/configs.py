"""Extract item, weapon, ammo and vehicle data from DayZ configs.

    python extract/configs.py --dz <trimmed data folder containing DZ/> --out data/configs.json

Reads every config.cpp (text) or config.bin (rapified) under the folder, merges them into
one class tree, resolves inheritance, swaps $STR_ keys for English text from the
stringtables, and writes the public classes the wiki needs. Ballistic tables are computed
from magazine muzzle velocity and ammo air friction.
"""
import argparse, csv, glob, io, json, math, os, re, struct

# ----------------------------------------------------------------------------------------
# Class tree


class Cls:
    __slots__ = ("name", "base", "props", "children", "parent", "deleted", "extern")

    def __init__(self, name, base=None, parent=None, extern=False):
        self.name, self.base, self.parent = name, base, parent
        self.props, self.children, self.deleted, self.extern = {}, {}, False, extern

    def child(self, name, base=None, extern=False):
        key = name.lower()
        c = self.children.get(key)
        if c is None:
            c = Cls(name, base, self, extern)
            self.children[key] = c
        elif not extern:
            if base:
                c.base = base
            c.extern = False
        c.deleted = False
        return c

    def _find(self, scope, key):
        """A class visible from `scope`: its own children, then those it inherits.
        Forward declarations (`class Single;`) defer to the inherited definition."""
        c, seen = scope, set()
        while c is not None and id(c) not in seen:
            seen.add(id(c))
            x = c.children.get(key)
            if x is not None and x is not self and not x.extern:
                return x
            c = c.base_cls()
        return None

    # inheritance: base is looked up in the parent's scope (and what it inherits), then further out
    def base_cls(self):
        if not self.base:
            return None
        key, scope = self.base.lower(), self.parent
        while scope is not None:
            b = self._find(scope, key)
            if b is not None:
                return b
            scope = scope.parent
        return None

    def get(self, key, default=None):
        c, seen = self, set()
        while c is not None and id(c) not in seen:
            seen.add(id(c))
            if key.lower() in c.props:
                return c.props[key.lower()][1]
            c = c.base_cls()
        return default

    def sub(self, name):
        c, seen = self, set()
        while c is not None and id(c) not in seen:
            seen.add(id(c))
            s = c.children.get(name.lower())
            if s is not None and not s.deleted and not s.extern:
                return s
            c = c.base_cls()
        return None

    def all_props(self):
        chain, c, seen = [], self, set()
        while c is not None and id(c) not in seen:
            seen.add(id(c))
            chain.append(c)
            c = c.base_cls()
        out = {}
        for c in reversed(chain):
            for k, (name, v) in c.props.items():
                out[name] = v
        return out

    def all_children(self):
        chain, c, seen = [], self, set()
        while c is not None and id(c) not in seen:
            seen.add(id(c))
            chain.append(c)
            c = c.base_cls()
        out = {}
        for c in reversed(chain):
            for k, ch in c.children.items():
                if not ch.deleted and not ch.extern:
                    out[k] = ch
        return out

    def inherits(self, name):
        c, seen = self, set()
        while c is not None and id(c) not in seen:
            seen.add(id(c))
            if c.name.lower() == name.lower():
                return True
            c = c.base_cls()
        return False

    def lineage(self):
        out, c, seen = [], self, set()
        while c is not None and id(c) not in seen:
            seen.add(id(c))
            out.append(c.name)
            c = c.base_cls()
        return out


# ----------------------------------------------------------------------------------------
# config.cpp parser

TOKEN = re.compile(r"""
    (?P<ws>\s+|//[^\n]*|/\*.*?\*/|\#[^\n]*) |
    (?P<str>"(?:[^"]|"")*") |
    (?P<num>[-+]?(?:\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)(?![\w.])) |
    (?P<word>[A-Za-z_][\w.$]*) |
    (?P<op>\+=|[{}\[\];:=,])
""", re.S | re.X)


def tokenize(src):
    pos = 0
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if not m:
            # tolerate stray characters (macros, odd unicode) by skipping them
            pos += 1
            continue
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        yield kind, m.group(kind)


def parse_cpp(src, root):
    toks = list(tokenize(src))
    i = 0

    def value(tok):
        kind, v = tok
        if kind == "str":
            v = v[1:-1].replace('""', '"')
            # a few numbers are written as expressions in quotes, e.g. reloadTime="60/800"
            if re.fullmatch(r"[\d.eE+\-*/ ()]+", v) and re.search(r"\d", v):
                try:
                    f = float(eval(v, {"__builtins__": {}}, {}))
                    return int(f) if f.is_integer() and "." not in v and "/" not in v else f
                except Exception:
                    pass
            return v
        if kind == "num":
            f = float(v)
            return int(f) if f.is_integer() and "." not in v and "e" not in v.lower() else f
        return v

    def array():
        nonlocal i
        assert toks[i][1] == "{"
        i += 1
        out = []
        while toks[i][1] != "}":
            if toks[i][1] == "{":
                out.append(array())
            elif toks[i][1] == ",":
                i += 1
                continue
            else:
                out.append(value(toks[i]))
                i += 1
        i += 1
        return out

    def body(cls):
        nonlocal i
        while i < len(toks) and toks[i][1] != "}":
            t = toks[i][1]
            if t == ";":
                i += 1
            elif t == "class":
                name = toks[i + 1][1]
                i += 2
                base = None
                if toks[i][1] == ":":
                    base = toks[i + 1][1]
                    i += 2
                if toks[i][1] == ";":  # external declaration
                    cls.child(name, base, extern=True)
                    i += 1
                    continue
                c = cls.child(name, base)
                i += 1  # {
                body(c)
                i += 1  # }
            elif t == "delete":
                name = toks[i + 1][1]
                c = cls.children.get(name.lower())
                if c:
                    c.deleted = True
                i += 2
            else:
                name = t
                i += 1
                is_arr = False
                if toks[i][1] == "[":
                    is_arr = True
                    i += 2  # [ ]
                op = toks[i][1]
                i += 1
                if is_arr:
                    v = array()
                    if op == "+=":
                        old = cls.get(name, [])
                        v = list(old if isinstance(old, list) else []) + v
                else:
                    # unquoted multi-token values are rare; take tokens until ;
                    start = i
                    while toks[i][1] != ";":
                        i += 1
                    parts = toks[start:i]
                    v = value(parts[0]) if len(parts) == 1 else " ".join(p[1] for p in parts)
                cls.props[name.lower()] = (name, v)

    body(root)


# ----------------------------------------------------------------------------------------
# config.bin (rapified) parser


class Rap:
    def __init__(self, data):
        self.d = data

    def cint(self, p):
        v, shift = 0, 0
        while True:
            b = self.d[p]
            p += 1
            v |= (b & 0x7F) << shift
            if not b & 0x80:
                return v, p
            shift += 7

    def asciiz(self, p):
        e = self.d.index(b"\0", p)
        return self.d[p:e].decode("utf-8", "replace"), e + 1

    def array(self, p):
        n, p = self.cint(p)
        out = []
        for _ in range(n):
            t = self.d[p]
            p += 1
            if t in (0, 4):
                v, p = self.asciiz(p)
            elif t == 1:
                v = struct.unpack_from("<f", self.d, p)[0]
                v = round(v, 6)
                p += 4
            elif t == 2:
                v = struct.unpack_from("<i", self.d, p)[0]
                p += 4
            elif t == 3:
                v, p = self.array(p)
            else:
                raise ValueError(f"array element type {t}")
            out.append(v)
        return out, p

    def body(self, p, cls):
        _base, p = self.asciiz(p)
        n, p = self.cint(p)
        for _ in range(n):
            t = self.d[p]
            p += 1
            if t == 0:
                name, p = self.asciiz(p)
                off = struct.unpack_from("<I", self.d, p)[0]
                p += 4
                base, _ = self.asciiz(off)
                c = cls.child(name, base or None)
                self.body(off, c)
            elif t == 1:
                sub = self.d[p]
                p += 1
                name, p = self.asciiz(p)
                if sub == 0 or sub == 4:
                    v, p = self.asciiz(p)
                elif sub == 1:
                    v = round(struct.unpack_from("<f", self.d, p)[0], 6)
                    p += 4
                elif sub == 2:
                    v = struct.unpack_from("<i", self.d, p)[0]
                    p += 4
                elif sub == 6:  # 64-bit int
                    v = struct.unpack_from("<q", self.d, p)[0]
                    p += 8
                else:
                    raise ValueError(f"value subtype {sub}")
                cls.props[name.lower()] = (name, v)
            elif t == 2:
                name, p = self.asciiz(p)
                v, p = self.array(p)
                cls.props[name.lower()] = (name, v)
            elif t == 3:
                name, p = self.asciiz(p)
                cls.child(name, extern=True)
            elif t == 4:
                name, p = self.asciiz(p)
                c = cls.children.get(name.lower())
                if c:
                    c.deleted = True
            elif t == 5:
                p += 4  # flags
                name, p = self.asciiz(p)
                v, p = self.array(p)
                old = cls.get(name, [])
                cls.props[name.lower()] = (name, list(old if isinstance(old, list) else []) + v)
            else:
                raise ValueError(f"entry type {t} at {p}")
        return p


def parse_bin(data, root):
    if data[:4] != b"\0raP":
        raise ValueError("not a rapified config")
    Rap(data).body(16, root)


# ----------------------------------------------------------------------------------------
# Loading and load order


def config_files(base):
    files = {}
    for path in glob.glob(os.path.join(base, "**", "config.*"), recursive=True):
        d, f = os.path.split(path)
        if f.lower() not in ("config.cpp", "config.bin"):
            continue
        # prefer text when both exist
        if d not in files or f.lower() == "config.cpp":
            files[d] = path
    return sorted(files.values())


def patch_info(path):
    """(patch names, requiredAddons) from a single config, used for load ordering."""
    root = Cls("")
    load_one(path, root)
    patches = root.children.get("cfgpatches")
    if not patches:
        return [], []
    names = [c.name for c in patches.children.values()]
    req = []
    for c in patches.children.values():
        req += [r for r in (c.get("requiredAddons") or []) if isinstance(r, str)]
    return names, req


def load_one(path, root):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] == b"\0raP":
        parse_bin(data, root)
    else:
        parse_cpp(data.decode("utf-8", "replace"), root)


def ordered(paths):
    info = {p: patch_info(p) for p in paths}
    provider = {}
    for p, (names, _) in info.items():
        for n in names:
            provider[n.lower()] = p
    done, out = set(), []

    def visit(p, stack=()):
        if p in done or p in stack:
            return
        for r in info[p][1]:
            dep = provider.get(r.lower())
            if dep and dep != p:
                visit(dep, stack + (p,))
        done.add(p)
        out.append(p)

    for p in paths:
        visit(p)
    return out


# ----------------------------------------------------------------------------------------
# Stringtables


def stringtables(base):
    table = {}
    for path in glob.glob(os.path.join(base, "**", "stringtable.csv"), recursive=True):
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            rows = list(csv.reader(f))
        if not rows:
            continue
        head = [h.strip().lower() for h in rows[0]]
        col = head.index("english") if "english" in head else (head.index("original") if "original" in head else 1)
        orig = head.index("original") if "original" in head else col
        for r in rows[1:]:
            if len(r) > max(col, orig) and r[0]:
                table[r[0].lower()] = r[col] or r[orig]
    return table


def tr(v, table):
    if isinstance(v, str) and v.startswith("$"):
        return table.get(v[1:].lower(), v)
    if isinstance(v, str) and v.startswith("#"):
        return table.get(v[1:].lower(), v)
    return v


# ----------------------------------------------------------------------------------------
# Ballistics: RV engine point-mass model, a = airFriction * v|v| + g


def trajectory(v0, air_friction, zero=100, ranges=(50, 100, 200, 300, 400, 500, 600, 800, 1000), sight=0.07):
    """Velocity, drop (cm vs line of sight) and time of flight at each range, zeroed at `zero` m."""
    if not v0 or air_friction is None:
        return None

    def fly(angle, until):
        x, y = 0.0, -sight
        vx, vy = v0 * math.cos(angle), v0 * math.sin(angle)
        t, dt, out, targets = 0.0, 0.0005, {}, sorted(until)
        while targets and t < 10:
            v = math.hypot(vx, vy)
            vx += air_friction * v * vx * dt
            vy += (air_friction * v * vy - 9.81) * dt
            x += vx * dt
            y += vy * dt
            t += dt
            while targets and x >= targets[0]:
                out[targets.pop(0)] = (y, math.hypot(vx, vy), t)
        return out

    lo, hi = -0.01, 0.05
    for _ in range(40):  # find the angle that hits line of sight at the zero range
        mid = (lo + hi) / 2
        y = fly(mid, [zero]).get(zero, (-1, 0, 0))[0]
        lo, hi = (mid, hi) if y < 0 else (lo, mid)
    res = fly((lo + hi) / 2, list(ranges))
    return {"zero": zero, "rows": [
        {"range": r, "velocity": round(res[r][1]), "drop": round(res[r][0] * 100, 1), "time": round(res[r][2], 3)}
        for r in ranges if r in res]}


# ----------------------------------------------------------------------------------------
# Export

KEEP_SUBCLASSES = {"damagesystem", "nutrition", "food", "energymanager", "cargo", "opticsinfo", "opticsinfoweaponoverride", "meleemodes",
                   "animevents", "simplehiddenselections", "inventoryequipment"}
SKIP_PROPS = {"simulation", "animclass"}  # hiddenSelections* are kept: the render pipeline uses them for colour variants


def plain(cls, table, depth=0):
    out = {}
    for k, v in cls.all_props().items():
        if k.lower() in SKIP_PROPS:
            continue
        out[k] = tr(v, table) if isinstance(v, str) else v
    if depth < 4:
        for k, ch in cls.all_children().items():
            if depth > 0 or k in KEEP_SUBCLASSES:
                out[ch.name] = plain(ch, table, depth + 1)
    return out


def sound_samples(root, set_names):
    sets = root.children.get("cfgsoundsets")
    shaders = root.children.get("cfgsoundshaders")
    out = {}
    for s in set_names or []:
        if not isinstance(s, str) or sets is None:
            continue
        sc = sets.children.get(s.lower())
        if sc is None:
            continue
        samples = []
        for sh in sc.get("soundShaders") or []:
            shc = shaders.children.get(str(sh).lower()) if shaders else None
            if shc is None:
                continue
            for smp in shc.get("samples") or []:
                if isinstance(smp, list) and smp and isinstance(smp[0], str):
                    samples.append({"path": smp[0].replace("\\", "/"), "shader": shc.name, "range": shc.get("range")})
        out[s] = samples
    return out


def export(root, table):
    data = {"weapons": {}, "magazines": {}, "ammo": {}, "vehicles": {}}
    weapons = root.children.get("cfgweapons")
    mags = root.children.get("cfgmagazines")
    ammo = root.children.get("cfgammo")
    vehicles = root.children.get("cfgvehicles")

    for key, sec in (("weapons", weapons), ("magazines", mags), ("vehicles", vehicles)):
        if sec is None:
            continue
        for c in sec.children.values():
            if c.deleted or c.extern or (c.get("scope") or 0) < 2:
                continue
            rec = plain(c, table)
            rec["_lineage"] = c.lineage()[1:]
            if key == "weapons":
                modes = {}
                for m in c.get("modes") or []:
                    mc = c.sub(str(m))
                    if mc is None:
                        continue
                    rt = mc.get("reloadTime")
                    modes[str(m)] = {
                        "reloadTime": rt, "rpm": round(60 / rt) if rt else None,
                        "dispersion": mc.get("dispersion"), "burst": mc.get("burst"),
                        "sounds": sound_samples(root, mc.get("soundSetShot")),
                    }
                rec["_modes"] = modes
            data[key][c.name] = rec

    if ammo is not None:
        for c in ammo.children.values():
            if c.deleted:
                continue
            rec = plain(c, table)
            dmg = c.sub("DamageApplied")
            if dmg is not None:
                rec["DamageApplied"] = plain(dmg, table, 1)
            data["ammo"][c.name] = rec

    # place names per world (CfgWorlds <World> Names), for map labels
    worlds = root.children.get("cfgworlds")
    data["places"] = {}
    if worlds is not None:
        for w in worlds.children.values():
            names = w.sub("Names")
            if w.extern or names is None:
                continue
            places = []
            for n in names.all_children().values():
                pos, label = n.get("position"), tr(n.get("name"), table)
                if isinstance(pos, list) and len(pos) >= 2 and label:
                    places.append({"n": label, "t": n.get("type") or "", "x": round(pos[0]), "z": round(pos[1])})
            if places:
                data["places"][w.name] = places

    # ballistics per magazine (ammo piles are magazines too)
    for name, m in data["magazines"].items():
        a = data["ammo"].get(m.get("ammo") or "")
        if a:
            m["_ballistics"] = trajectory(m.get("initSpeed"), a.get("airFriction"))
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dz", required=True, help="folder containing DZ/ (and its config.cpp/bin files)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root = Cls("")
    files = ordered(config_files(a.dz))
    errors = 0
    for p in files:
        try:
            load_one(p, root)
        except Exception as e:  # keep going; report at the end
            errors += 1
            print("  skipped", p, "-", e)
    table = stringtables(a.dz)
    data = export(root, table)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"configs: {len(files)} files ({errors} skipped), {len(table)} strings,",
          {k: len(v) for k, v in data.items()}, "->", a.out)


if __name__ == "__main__":
    main()
