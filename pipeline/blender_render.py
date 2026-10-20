"""Batch-render converted models to transparent WebP images and web GLBs.

    blender -b -P pipeline/blender_render.py -- --work pipeline/work --out media

Reads work/<category>/<Class>/model.glb (made by layout.py) and writes
media/renders/<category>/<Class>.webp and media/models/<Class>.glb.
Options: --only <Class>, --size 1600x1200, --samples 96, --engine CYCLES|EEVEE, --gpu, --no-glb.
"""
import argparse, json, math, os, sys

import bpy
from mathutils import Vector

# Camera presets per wiki category: (azimuth degrees around +Y from the model's front, elevation, lens mm)
# Models face glTF +Z. A model whose long axis is X instead (DayZ guns) gets the azimuth turned by 90.
PRESETS = {
    "weapons": (90, 4, 85), "attachments": (90, 10, 85), "ammunition": (35, 22, 85),
    "clothing": (25, 8, 70), "gear": (35, 22, 70), "medical": (35, 25, 85), "food": (35, 25, 85),
    "vehicles": (40, 14, 50), "default": (35, 18, 70),
}


FILL = 0.88  # share of the frame the model may cover


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    ap.add_argument("--size", default="1600x1200")
    ap.add_argument("--samples", type=int, default=96)
    ap.add_argument("--engine", default="CYCLES", choices=["CYCLES", "EEVEE"])
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--no-glb", action="store_true")
    return ap.parse_args(argv)


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def setup_render(a):
    sc = bpy.context.scene
    w, h = (int(x) for x in a.size.lower().split("x"))
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = w, h, 100
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = "WEBP"
    sc.render.image_settings.color_mode = "RGBA"
    sc.render.image_settings.quality = 90
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Medium High Contrast"
    if a.engine == "CYCLES":
        sc.render.engine = "CYCLES"
        sc.cycles.samples = a.samples
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
        sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items} else "BLENDER_EEVEE"
    world = bpy.data.worlds.new("studio")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.045, 0.045, 0.05, 1)  # soft dark fill for reflections; hidden by film transparency
    bg.inputs[1].default_value = 1.0
    sc.world = world


def bounds(objs):
    lo, hi = Vector((1e9, 1e9, 1e9)), Vector((-1e9, -1e9, -1e9))
    for o in objs:
        for c in o.bound_box:
            p = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    return lo, hi


def add_light(name, kind, loc, target, energy, size):
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    if kind == "AREA":
        data.size = size
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = loc
    obj.rotation_euler = (target - loc).to_track_quat("-Z", "Y").to_euler()
    return obj


def frame(objs, category, aspect):
    az, el, lens = PRESETS.get(category, PRESETS["default"])
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

    # three-point studio lighting scaled to the model
    r = radius * 4
    side = Vector((direction.y, -direction.x, 0)).normalized()
    add_light("key", "AREA", center + (direction + side * 0.9 + Vector((0, 0, 0.9))).normalized() * r, center, 900 * radius ** 2, radius * 2.5)
    add_light("fill", "AREA", center + (direction - side * 1.1 + Vector((0, 0, 0.2))).normalized() * r, center, 260 * radius ** 2, radius * 3)
    add_light("rim", "AREA", center + (-direction + Vector((0, 0, 0.8))).normalized() * r, center, 700 * radius ** 2, radius * 2)


def render_item(item_dir, category, cls, a):
    reset_scene()
    setup_render(a)
    bpy.ops.import_scene.gltf(filepath=os.path.join(item_dir, "model.glb"))
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not objs:
        raise RuntimeError("no meshes imported")
    for o in objs:
        for poly in o.data.polygons:
            poly.use_smooth = True
    sc = bpy.context.scene
    frame(objs, category, sc.render.resolution_x / sc.render.resolution_y)
    img_out = os.path.join(a.out, "renders", category, f"{cls}.webp")
    os.makedirs(os.path.dirname(img_out), exist_ok=True)
    sc.render.filepath = img_out
    bpy.ops.render.render(write_still=True)
    if not a.no_glb:
        glb_out = os.path.join(a.out, "models", f"{cls}.glb")
        os.makedirs(os.path.dirname(glb_out), exist_ok=True)
        for o in bpy.context.scene.objects:
            o.select_set(o.type == "MESH")
        kw = dict(filepath=glb_out, export_format="GLB", use_selection=True, export_image_format="WEBP")
        try:
            bpy.ops.export_scene.gltf(**kw, export_draco_mesh_compression_enable=True)
        except Exception:
            bpy.ops.export_scene.gltf(**kw)
    return img_out


def main():
    a = args()
    done, failed = 0, []
    for category in sorted(os.listdir(a.work)):
        cdir = os.path.join(a.work, category)
        if not os.path.isdir(cdir):
            continue
        for cls in sorted(os.listdir(cdir)):
            item = os.path.join(cdir, cls)
            if a.only and cls != a.only:
                continue
            if not os.path.exists(os.path.join(item, "model.glb")):
                continue
            try:
                out = render_item(item, category, cls, a)
                done += 1
                print(f"rendered {category}/{cls} -> {out}", flush=True)
            except Exception as e:
                failed.append((cls, str(e)))
                print(f"FAILED {category}/{cls}: {e}", flush=True)
    print(f"done: {done} rendered, {len(failed)} failed")
    if failed:
        with open(os.path.join(a.work, "render_failures.json"), "w") as f:
            json.dump(failed, f, indent=1)


if __name__ == "__main__":
    main()
