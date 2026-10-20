"""Batch-render the work tree to transparent images and web GLBs. Runs inside Blender (4.2+, 5.x).

    blender -b --factory-startup -P pipeline/blender_render.py -- --work pipeline/work --out media [--resume]

Reads work/<category>/<Class>/model.glb (made by layout.py) and writes
media/renders/<category>/<Class>.webp (cropped to the model with a margin) and media/models/<Class>.glb
(the web.glb made by layout.py; --blender-glb re-exports from Blender instead), plus work/render.log and
media/renders/report.json. Resumable: --resume skips classes that already have an image.

Options: --only <text> (substring, repeatable), --limit N, --shard-index I --shard-count N,
--size 2400x1800 (the frame before cropping), --quality 92, --format webp|png, --engine EEVEE|CYCLES
(EEVEE default), --samples N, --gpu, --exposure -0.3, --views main,back,top (also front; each extra view is
<Class>.<view>.webp), --no-glb, --check (verify Blender and the work tree only).
"""
import argparse, json, os, sys, time, traceback

try:
    import bpy
    from mathutils import Vector
except ModuleNotFoundError:
    sys.exit("This script runs inside Blender, not plain Python (bpy is Blender's own module, not a pip package).\n"
             "Run it as:\n"
             "  blender -b --factory-startup -P pipeline/blender_render.py -- --work pipeline/work --out media\n"
             "or on Windows with the full path, e.g.\n"
             "  \"C:/Program Files/Blender Foundation/Blender 5.1/blender.exe\" -b --factory-startup -P pipeline/blender_render.py -- --work pipeline/work --out media")

import math  # noqa: E402

# Camera presets per wiki category: (azimuth degrees around +Y from the model's front, elevation, lens mm)
# Models face glTF +Z. A model whose long axis is X instead (DayZ guns) gets the azimuth turned by 90.
PRESETS = {
    "weapons": (90, 4, 85), "attachments": (90, 10, 85), "ammunition": (35, 22, 85),
    "clothing": (25, 8, 70), "gear": (35, 22, 70), "medical": (35, 25, 85), "food": (35, 25, 85),
    "vehicles": (40, 14, 50), "default": (35, 18, 70),
}
FILL = 0.88  # share of the frame the model may cover
# Extra views as changes to the preset: (azimuth added, elevation, file suffix)
VIEWS = {"main": (0, None, ""), "back": (180, None, ".back"), "top": (0, 72, ".top"), "front": (90, None, ".front")}
LOG = None


def log(msg):
    print(msg, flush=True)
    if LOG:
        LOG.write(msg + "\n"); LOG.flush()


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", action="append", default=[], help="class name substring (repeatable)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1)
    ap.add_argument("--resume", action="store_true", help="skip classes that already have an image")
    ap.add_argument("--size", default="2400x1800")
    ap.add_argument("--quality", type=int, default=92, help="WebP quality (100 = lossless)")
    ap.add_argument("--format", default="webp", choices=["webp", "png"])
    ap.add_argument("--exposure", type=float, default=-0.3)
    ap.add_argument("--no-crop", action="store_true", help="keep the full frame instead of cropping to the model")
    ap.add_argument("--views", default="main,back,top", help=f"views rendered per item, from {','.join(VIEWS)}")
    ap.add_argument("--blender-glb", action="store_true", help="export media/models/<Class>.glb from Blender instead of copying web.glb")
    ap.add_argument("--samples", type=int, default=0, help="render samples (default 64 EEVEE, 96 Cycles)")
    ap.add_argument("--engine", default="EEVEE", choices=["CYCLES", "EEVEE"])
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--no-glb", action="store_true")
    ap.add_argument("--check", action="store_true")
    return ap.parse_args(argv)


# ---------------------------------------------------------------- scene
def enum_ids(rna_struct, prop):
    return {e.identifier for e in rna_struct.bl_rna.properties[prop].enum_items}


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def ensure_gltf_io():
    """The glTF add-on ships with Blender; make sure it is enabled (factory startup normally has it on)."""
    if hasattr(bpy.ops.import_scene, "gltf") and bpy.ops.import_scene.gltf.poll() is not None:
        return True
    for mod in ("io_scene_gltf2", "bl_ext.blender_org.io_scene_gltf2"):
        try:
            bpy.ops.preferences.addon_enable(module=mod)
            if hasattr(bpy.ops.import_scene, "gltf"):
                return True
        except Exception:
            continue
    return hasattr(bpy.ops.import_scene, "gltf")


def setup_render(a):
    sc = bpy.context.scene
    w, h = (int(x) for x in a.size.lower().split("x"))
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = w, h, 100
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = a.format.upper()
    sc.render.image_settings.color_mode = "RGBA"
    if a.format == "webp":
        sc.render.image_settings.quality = a.quality
        if a.quality >= 100:
            try:
                sc.render.image_settings.color_management = "FOLLOW_SCENE"
            except Exception:
                pass
    else:
        sc.render.image_settings.compression = 30
    sc.render.use_file_extension = False
    for key, val in (("view_transform", "AgX"), ("look", "AgX - Base Contrast"), ("exposure", a.exposure)):
        try:
            setattr(sc.view_settings, key, val)
        except Exception:
            pass   # older colour management names; the default look is fine
    if a.engine == "CYCLES":
        sc.render.engine = "CYCLES"
        sc.cycles.samples = a.samples or 96
        sc.cycles.use_denoising = True
        if a.gpu:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            for backend in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
                try:
                    prefs.compute_device_type = backend
                    prefs.get_devices()
                    if any(d.type != "CPU" for d in prefs.devices):
                        for d in prefs.devices:
                            d.use = True
                        sc.cycles.device = "GPU"
                        break
                except TypeError:
                    continue
    else:
        engines = enum_ids(sc.render, "engine")
        sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
        try:
            sc.eevee.taa_render_samples = a.samples or 64
        except Exception:
            pass
    world = bpy.data.worlds.new("studio")
    try:
        world.use_nodes = True
    except Exception:
        pass
    nt = world.node_tree
    bg = nt.nodes.get("Background") if nt else None
    if nt and not bg:
        bg = nt.nodes.new("ShaderNodeBackground")
        out = next((n for n in nt.nodes if n.type == "OUTPUT_WORLD"), None) or nt.nodes.new("ShaderNodeOutputWorld")
        nt.links.new(bg.outputs[0], out.inputs[0])
    if bg:
        bg.inputs[0].default_value = (0.02, 0.02, 0.024, 1)  # faint dark fill for reflections; hidden by film transparency
        bg.inputs[1].default_value = 1.0
    else:
        world.color = (0.045, 0.045, 0.05)
    sc.world = world


# ---------------------------------------------------------------- framing
def bounds(objs):
    """World-space box of the evaluated meshes (vertex positions after modifiers, not the cached bound_box)."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    lo, hi = Vector((1e9, 1e9, 1e9)), Vector((-1e9, -1e9, -1e9))
    for o in objs:
        ev = o.evaluated_get(dg)
        verts = getattr(ev.data, "vertices", None)
        pts = (ev.matrix_world @ v.co for v in verts) if verts else (ev.matrix_world @ Vector(c) for c in ev.bound_box)
        for p in pts:
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    if lo.x > hi.x:
        raise RuntimeError("no geometry")
    return lo, hi


def add_light(name, kind, loc, target, energy, size):
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    if kind == "AREA":
        data.shape = "DISK"
        data.size = size
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = loc
    obj.rotation_euler = (target - loc).to_track_quat("-Z", "Y").to_euler()
    return obj


def frame(objs, category, aspect, view="main"):
    az, el, lens = PRESETS.get(category, PRESETS["default"])
    d_az, v_el, _ = VIEWS[view]
    az += d_az
    if v_el is not None:
        el = v_el
    lo, hi = bounds(objs)
    center, size = (lo + hi) / 2, hi - lo
    # presets assume the model's long axis runs front to back (Blender Y). DayZ weapons are modelled
    # along X (muzzle towards -X), so turn the camera to keep a side-on view of whatever is longest
    if size.x > size.y * 1.3:
        az -= 90
    radius = max(size.length / 2, 1e-3)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = lens
    cam_data.sensor_width = 36
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    fov = 2 * math.atan(cam_data.sensor_width / 2 / lens)
    vfov = 2 * math.atan(math.tan(fov / 2) / aspect)
    dist = radius / math.sin(min(fov, vfov) / 2) * 1.04
    # Blender scene: glTF +Y up becomes +Z up, glTF +Z (model front) becomes -Y
    a, e = math.radians(az), math.radians(el)
    direction = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = center + direction * dist
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()

    # Tighten: move along the view axis until the projected box fills the frame with a margin
    bpy.context.view_layer.update()
    inv = cam.matrix_world.inverted()
    th, tv = math.tan(fov / 2) * FILL, math.tan(vfov / 2) * FILL
    corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    shift = max(max(abs(c.x) / th, abs(c.y) / tv) + c.z for c in (inv @ p for p in corners))
    cam.location = cam.location + direction * shift
    dist = (cam.location - center).length
    cam_data.clip_start, cam_data.clip_end = dist / 100, dist * 10

    # three-point studio lighting; wattage scales with the square of the model size so every item is lit alike
    r = radius * 4
    side = Vector((direction.y, -direction.x, 0)).normalized()
    add_light("key", "AREA", center + (direction + side * 0.9 + Vector((0, 0, 0.9))).normalized() * r, center, 420 * radius ** 2, radius * 3)
    add_light("fill", "AREA", center + (direction - side * 1.1 + Vector((0, 0, 0.2))).normalized() * r, center, 150 * radius ** 2, radius * 3.5)
    add_light("rim", "AREA", center + (-direction + Vector((0, 0, 0.8))).normalized() * r, center, 320 * radius ** 2, radius * 2.5)
    return cam, corners


def crop_to_model(cam, corners, margin=0.04):
    """Render border around the model's projected box (plus a margin) and crop the output to it:
    fewer empty pixels, no change to the pixels that matter."""
    from bpy_extras.object_utils import world_to_camera_view
    sc = bpy.context.scene
    bpy.context.view_layer.update()            # the camera was just moved: project with its current matrix
    pts = [world_to_camera_view(sc, cam, p) for p in corners]
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    lo_x, hi_x = max(0.0, min(xs) - margin), min(1.0, max(xs) + margin)
    lo_y, hi_y = max(0.0, min(ys) - margin), min(1.0, max(ys) + margin)
    sc.render.use_border = sc.render.use_crop_to_border = True
    sc.render.border_min_x, sc.render.border_max_x = lo_x, hi_x
    sc.render.border_min_y, sc.render.border_max_y = lo_y, hi_y


# ---------------------------------------------------------------- one item
def render_item(item_dir, category, cls, a):
    reset_scene()
    setup_render(a)
    bpy.ops.import_scene.gltf(filepath=os.path.join(item_dir, "model.glb"))
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not objs:
        raise RuntimeError("no meshes imported")
    for o in objs:
        if hasattr(o.data, "shade_smooth"):
            o.data.shade_smooth()
        else:
            for poly in o.data.polygons:
                poly.use_smooth = True
    sc = bpy.context.scene
    img_out = None
    for view in views(a):
        for o in list(sc.objects):
            if o.type in ("CAMERA", "LIGHT"):
                bpy.data.objects.remove(o, do_unlink=True)
        cam, corners = frame(objs, category, sc.render.resolution_x / sc.render.resolution_y, view)
        if not a.no_crop:
            crop_to_model(cam, corners)
        out = image_path(a, category, cls, view)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        sc.render.filepath = out
        bpy.ops.render.render(write_still=True)
        if not os.path.exists(out):
            raise RuntimeError(f"Blender wrote no file at {out}")
        img_out = img_out or out
    if not a.no_glb:
        glb_out = os.path.join(a.out, "models", f"{cls}.glb")
        os.makedirs(os.path.dirname(glb_out), exist_ok=True)
        web = os.path.join(item_dir, "web.glb")
        if os.path.exists(web) and not a.blender_glb:
            import shutil
            shutil.copyfile(web, glb_out)
            return img_out
        for o in bpy.context.scene.objects:
            o.select_set(o.type == "MESH")
        kw = dict(filepath=glb_out, export_format="GLB", use_selection=True)
        for extra in ({"export_image_format": "WEBP", "export_draco_mesh_compression_enable": True}, {"export_image_format": "WEBP"}, {}):
            try:
                bpy.ops.export_scene.gltf(**kw, **extra)
                break
            except Exception:
                continue
    return img_out


def views(a):
    v = [x.strip() for x in a.views.split(",") if x.strip()]
    bad = [x for x in v if x not in VIEWS]
    if bad:
        sys.exit(f"unknown view(s) {bad}; choose from {', '.join(VIEWS)}")
    return v or ["main"]


def image_path(a, category, cls, view="main"):
    return os.path.join(a.out, "renders", category, f"{cls}{VIEWS[view][2]}.{a.format}")


def work_items(a):
    items = []
    for category in sorted(os.listdir(a.work)):
        cdir = os.path.join(a.work, category)
        if not os.path.isdir(cdir):
            continue
        for cls in sorted(os.listdir(cdir)):
            if os.path.exists(os.path.join(cdir, cls, "model.glb")):
                items.append((category, cls))
    if a.only:   # exact class names first, else any class containing the text
        exact = [it for it in items if any(o.lower() == it[1].lower() for o in a.only)]
        items = exact or [it for it in items if any(o.lower() in it[1].lower() for o in a.only)]
    items = [it for i, it in enumerate(items) if i % a.shard_count == a.shard_index]
    if a.limit:
        items = items[:a.limit]
    return items


def main():
    global LOG
    a = args()
    a.work, a.out = os.path.abspath(a.work), os.path.abspath(a.out)
    if not os.path.isdir(a.work):
        sys.exit(f"work tree not found: {a.work}  (run layout.py first)")
    LOG = open(os.path.join(a.work, "render.log"), "a", encoding="utf-8")
    log(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} Blender {bpy.app.version_string} engine={a.engine} gpu={a.gpu} work={a.work} out={a.out}")
    if not ensure_gltf_io():
        sys.exit("Blender's glTF add-on is not available (import_scene.gltf); enable it in Preferences > Add-ons")
    items = work_items(a)
    log(f"{len(items)} models to render" + (f" (shard {a.shard_index + 1}/{a.shard_count})" if a.shard_count > 1 else ""))
    if a.check:
        for c, k in items[:10]:
            log(f"  {c}/{k}")
        log("check ok" if items else "check: nothing to render (layout.py made no model.glb files)")
        return
    done, skipped, failed, start = 0, 0, [], time.time()
    report = []
    for n, (category, cls) in enumerate(items, 1):
        if a.resume and all(os.path.exists(image_path(a, category, cls, v)) and os.path.getsize(image_path(a, category, cls, v)) > 1024 for v in views(a)):
            skipped += 1
            log(f"[{n}/{len(items)}] skipped {category}/{cls}")
            continue
        t0 = time.time()
        try:
            out = render_item(os.path.join(a.work, category, cls), category, cls, a)
            done += 1
            log(f"[{n}/{len(items)}] {category}/{cls} -> {os.path.relpath(out, a.out)} ({time.time() - t0:.1f}s)")
            report.append({"class": cls, "category": category, "image": os.path.relpath(out, a.out)})
        except Exception as e:
            failed.append({"class": cls, "category": category, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
            log(f"[{n}/{len(items)}] FAILED {category}/{cls}: {type(e).__name__}: {e}")
    elapsed = time.time() - start
    log(f"done: {done} rendered, {skipped} skipped, {len(failed)} failed in {elapsed / 60:.1f} min")
    os.makedirs(os.path.join(a.out, "renders"), exist_ok=True)
    with open(os.path.join(a.out, "renders", "report.json" if a.shard_count == 1 else f"report.part{a.shard_index}.json"), "w", encoding="utf-8") as f:
        json.dump({"blender": bpy.app.version_string, "engine": a.engine, "rendered": done, "skipped": skipped,
                   "failed": failed, "items": report, "elapsed_s": round(elapsed, 1)}, f, indent=1)
    if failed:
        log("failures are listed in media/renders/report.json")


if __name__ == "__main__":
    main()
