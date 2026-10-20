"""Prepare the render work tree: one folder per wiki item with its converted model.

    python pipeline/layout.py --render <trimmed render folder> [--all] [--only AKM]

Creates pipeline/work/<category>/<Class>/model.glb (for Blender) + web.glb (served on the wiki, the same
single LOD with lossless WebP textures stored once each) + item.json for every class the wiki has a
page for (every item in a mission's economy in data/missions, plus hand-written pages with a
`class`; with --all, every public config class that has a model), applying the class's
hiddenSelectionsTextures so colour variants render correctly. Then run blender_render.py.

Weapons are assembled from their proxies: each proxy in the model marks an attachment slot, and
the attachment PureZ spawns there most often (buttstock, handguard and magazine by default;
--slots to change) is merged into the GLB at the proxy's position and orientation.

Binarised (ODOL) models are decoded on the fly by odol.py, so the render folder can be used as is.
"""
import argparse, glob, json, os, re, sys, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import p3d  # noqa: E402

# Wiki category from a model's game path, for --all
PATH_CATEGORIES = [
    (r"dz/weapons/(attachments|optics)", "attachments"), (r"dz/weapons/ammunition", "ammunition"),
    (r"dz/weapons/", "weapons"), (r"dz/characters/", "clothing"), (r"dz/vehicles/", "vehicles"),
    (r"dz/gear/medical", "medical"), (r"dz/gear/(food|drinks)", "food"), (r"dz/", "gear"),
]


def front_matter(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    meta = {}
    if text.startswith("---"):
        for line in text[3:text.find("\n---", 3)].strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta


def missions():
    """Every mission in data/missions (and the old single data/mission.json)."""
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "missions", "*.json"))) + [os.path.join(ROOT, "data", "mission.json")]:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                out.append(json.load(f).get("purez", {}))
    return out


def wiki_items(cfg):
    """Classes the wiki has pages for: hand-written pages with a class, and every economy item of every mission.
    Categories follow build.py so renders land where the pages look for them (media/renders/<category>/)."""
    out = {}
    for path in glob.glob(os.path.join(ROOT, "content", "**", "*.md"), recursive=True):
        meta = front_matter(path)
        if meta.get("class"):
            cat = meta.get("category") or os.path.relpath(path, os.path.join(ROOT, "content")).split(os.sep)[0]
            out[meta["class"]] = {"category": cat, "model": meta.get("model")}
    try:
        sys.path.insert(0, ROOT)
        try:
            import build
        except ModuleNotFoundError:   # build.py imports jinja2 and markdown; only its pure category rules are needed here
            import types
            for mod, names in (("markdown", ["Markdown"]), ("jinja2", ["Environment", "FileSystemLoader", "Undefined", "select_autoescape"])):
                if mod not in sys.modules:
                    stub = types.ModuleType(mod)
                    for n in names:
                        setattr(stub, n, object)
                    sys.modules[mod] = stub
            import build
        vehicle_slots = {a.lower() for c in cfg.values() if set(c.get("_lineage") or []) & build.VEHICLE_BASES
                         for a in c.get("attachments") or [] if not re.search(r"(?i)barrel|cargo|crate|material|canister|planks|sheets|logs", a)}
        categorise = lambda cls, t: build.wiki_category(cls, t, (cfg.get(cls) or {}).get("inventorySlot") or (), vehicle_slots)
    except Exception as e:   # build.py needs jinja2 + markdown; without them fall back to the model path
        print(f"note: categories from model paths ({type(e).__name__}: {e}); install requirements.txt for the wiki's own categories")
        categorise = lambda cls, t: category_for((cfg.get(cls) or {}).get("model"))
    for m in missions():
        for cls, t in m.get("types", {}).items():
            if cls not in out and (cfg.get(cls) or {}).get("model"):
                out[cls] = {"category": categorise(cls, t), "model": None}
    return out


def configs():
    path = os.path.join(ROOT, "data", "configs.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    merged = {}
    for sec in ("weapons", "magazines", "vehicles"):
        merged.update(data.get(sec, {}))
    return merged


# Slots filled in a weapon render, matched against the attachment's inventorySlot
DEFAULT_SLOTS = ["buttstock", "handguard", "magazine"]


def model_key(model):
    return re.sub(r"\.p3d$", "", (model or "").replace("/", "\\").lstrip("\\").lower())


def spawn_groups():
    """Attachment spawn groups merged across missions (the first mission wins for a weapon)."""
    out = {}
    for m in missions():
        for cls, g in m.get("spawnable", {}).items():
            out.setdefault(cls, g)
    return out


def pick_attachments(cls, c, lod, cfg, by_model, spawnable, slots):
    """Attachment per proxy slot: the one PureZ fits most often, else the first the config allows.
    Returns [{slot, class, proxy, origin, frame}]."""
    chances = {}   # attachment class -> chance it spawns on this weapon
    for g in (spawnable.get(cls) or {}).get("groups", []):
        for item, share in g.get("items", []):
            chances[item] = max(chances.get(item, 0), g.get("chance", 0) * share)
    allowed = {a.lower() for a in c.get("attachments") or []} | {"magazine"}
    out, used = [], set()
    for px in p3d.proxies(lod):
        holders = by_model.get(px["path"], [])
        slot = next((sl for h in holders for sl in (cfg[h].get("inventorySlot") or []) if sl.lower() in allowed), None)
        if not slot:
            continue
        want = next((w for w in slots if w in slot.lower()), None)
        if not want or slot.lower() in used:
            continue
        if want == "magazine":
            cands = [m for m in c.get("magazines") or [] if m in cfg]
        else:
            cands = [k for k, v in cfg.items() if slot.lower() in [x.lower() for x in v.get("inventorySlot") or []] and v.get("model")]
        if not cands:
            continue
        best = max(cands, key=lambda k: (chances.get(k, 0), -cands.index(k)))
        used.add(slot.lower())
        out.append({"slot": slot, "class": best, "proxy": px["name"], "origin": px["origin"], "frame": px["frame"],
                    "chance": round(chances.get(best, 0), 3)})
    return out


def category_for(model):
    m = (model or "").replace("\\", "/").lower().lstrip("/")
    return next((cat for rx, cat in PATH_CATEGORIES if re.search(rx, m)), "gear")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", required=True, help="render folder from dayz_trim.py (contains DZ/)")
    ap.add_argument("--work", default=os.path.join(HERE, "work"))
    ap.add_argument("--all", action="store_true", help="every public config class with a model")
    ap.add_argument("--only")
    ap.add_argument("--slots", default=",".join(DEFAULT_SLOTS), help="attachment slots merged into weapon renders (comma list, '' for none)")
    ap.add_argument("--web-tex", type=int, default=0, help="longest texture side in the web GLB (0 = as in the game files)")
    ap.add_argument("--web-lossy", type=int, default=0, help="WebP quality for the web GLB textures (0 = lossless)")
    ap.add_argument("--no-web", action="store_true", help="skip the web GLB")
    ap.add_argument("--proxy-frame", default="triangle", choices=["identity", "triangle"],
                    help="rotate attachments by the proxy's transform (the default; it comes straight from the ODOL file), or only move them to its origin")
    a = ap.parse_args()
    slots = [x.strip().lower() for x in a.slots.split(",") if x.strip()]

    if not os.path.isdir(a.render):
        sys.exit(f"render folder not found: {a.render}\n"
                 "In Git Bash backslashes are stripped from arguments: write the path with forward slashes "
                 "(F:/PureZ/PureZ-render) or in single quotes.")
    if not os.path.isdir(os.path.join(a.render, "DZ")) and not os.path.isdir(os.path.join(a.render, "dz")):
        print(f"warning: no DZ folder under {a.render}; model paths like dz\\weapons\\... will not resolve")
    cfg = configs()
    items = wiki_items(cfg)
    if a.all:
        for cls, c in cfg.items():
            if c.get("model") and cls not in items:
                items[cls] = {"category": category_for(c["model"]), "model": None}
    if a.only:   # exact class name first, else any class containing the text
        exact = {k: v for k, v in items.items() if k.lower() == a.only.lower()}
        items = exact or {k: v for k, v in items.items() if a.only.lower() in k.lower()}
    if not items:
        sys.exit("nothing to prepare: no classes found (is data/configs.json present?)")
    print(f"{len(items)} classes to prepare from {a.render}")

    files = p3d.Files(a.render)
    by_model = {}
    for k, v in cfg.items():
        if v.get("model"):
            by_model.setdefault(model_key(v["model"]), []).append(k)
    spawnable = spawn_groups()
    stats = {"converted": 0, "odol": 0, "no model": 0, "missing file": 0, "failed": 0}
    for cls, it in sorted(items.items()):
        c = cfg.get(cls, {})
        model = it.get("model") or c.get("model")
        if not model:
            stats["no model"] += 1
            continue
        src = files.find(model) or files.find(model + ".p3d")
        if not src:
            stats["missing file"] += 1
            print(f"missing  {cls}: {model}", flush=True)
            continue
        out_dir = os.path.join(a.work, it["category"], cls)
        hidden = dict(zip([s.lower() for s in c.get("hiddenSelections") or []], c.get("hiddenSelectionsTextures") or []))
        mats = dict(zip([s.lower() for s in c.get("hiddenSelections") or []], c.get("hiddenSelectionsMaterials") or []))
        try:
            lod = p3d.best_lod(p3d.read_p3d(src))
            if not lod:
                stats["empty"] = stats.get("empty", 0) + 1
                print(f"empty    {cls}: {model} has no visual geometry ({os.path.getsize(src)} bytes)", flush=True)
                continue
            parts, fitted = [], []
            if it["category"] == "weapons" and slots and c.get("attachments"):
                for att in pick_attachments(cls, c, lod, cfg, by_model, spawnable, slots):
                    ac = cfg[att["class"]]
                    asrc = files.find(ac["model"])
                    if not asrc:
                        print(f"  missing attachment model {att['class']}: {ac['model']}")
                        continue
                    alod = p3d.best_lod(p3d.read_p3d(asrc))
                    if not alod:
                        continue
                    parts.append({"lod": alod, "name": att["class"], "origin": att["origin"],
                                  "frame": att["frame"] if a.proxy_frame == "triangle" else None,
                                  "hidden": dict(zip([x.lower() for x in ac.get("hiddenSelections") or []], ac.get("hiddenSelectionsTextures") or [])),
                                  "mats": dict(zip([x.lower() for x in ac.get("hiddenSelections") or []], ac.get("hiddenSelectionsMaterials") or []))})
                    fitted.append({k: att[k] for k in ("slot", "class", "proxy", "chance")} | {"origin": [round(x, 4) for x in att["origin"]]})
            info = p3d.export(lod, os.path.join(out_dir, "model.glb"), files, hidden, mats, parts, tex={"png_level": 1})
            if not a.no_web:
                p3d.export(lod, os.path.join(out_dir, "web.glb"), files, hidden, mats, parts,
                           tex={"fmt": "webp", "lossless": not a.web_lossy, "quality": a.web_lossy or 90, "max_size": a.web_tex})
                info["web_bytes"] = os.path.getsize(os.path.join(out_dir, "web.glb"))
            with open(os.path.join(out_dir, "item.json"), "w", encoding="utf-8") as f:
                json.dump({"class": cls, "category": it["category"], "model": model, "hidden": hidden, "fitted": fitted, **info}, f, indent=1)
            stats["converted"] += 1
            extra = f" + {', '.join(x['class'] for x in fitted)}" if fitted else ""
            size = f", web {info['web_bytes'] / 1e6:.1f} MB" if info.get("web_bytes") else ""
            print(f"ok       {it['category']}/{cls} ({info['faces']} faces{size}){extra}", flush=True)
        except NotImplementedError as e:
            stats["odol"] += 1
            print(f"odol     {cls}: {e}", flush=True)
        except Exception as e:
            stats["failed"] += 1
            tb = traceback.extract_tb(e.__traceback__)[-1]
            print(f"failed   {cls}: {type(e).__name__}: {e}  [{os.path.basename(tb.filename)}:{tb.lineno} in {tb.name}]", flush=True)
    print(stats)
    print(f"work tree: {a.work}  (next: pipeline\\render.bat, or blender -b --factory-startup -P pipeline/blender_render.py -- --work {a.work} --out media)")


if __name__ == "__main__":
    main()
