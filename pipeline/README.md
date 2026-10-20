# Model and render pipeline

Turns DayZ models into transparent product renders and web 3D models for the wiki.

```
render folder (from dayz_trim.py)          pipeline/work/<category>/<Class>/       media/
  DZ/weapons/firearms/akm/akm.p3d   ──►      model.glb  (textures embedded)   ──►    renders/<category>/<Class>.webp
  DZ/.../data/akm_co.paa                     item.json                               models/<Class>.glb
           layout.py (+ p3d.py, paa.py)                 blender_render.py
```

## Requirements

- Python 3.10+ with `pip install pillow numpy` (`python-lzo` optional, for speed)
- Blender 4.2 or newer

## Steps

The game ships binarised ODOL models, which Blender can't read. Convert them to MLOD first with
Mikero's DeP3d (part of the DePbo tools): run it over the whole `PureZ-render` folder so every
`.p3d` is rewritten as MLOD in place. Then, from the wiki repo:

1. **Extract configs** (done whenever the game data changes):
   `python extract/configs.py --dz ../PureZ --out data/configs.json`
2. **Convert models** into the work tree, reading textures from the render folder:
   `python pipeline/layout.py --render F:\PureZ\PureZ-render` (wiki pages only; add `--all` for every item)
3. **Render** (Windows example):
   `"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe" -b -P pipeline/blender_render.py -- --work pipeline/work --out media --gpu`
4. Commit `media/`. Pages show the render and 3D model automatically.

Useful options: `--only AKM` to redo one item, `--samples 64` for faster drafts, `--size 1600x1200`.

## Notes

- Camera angles are set per category in `PRESETS` at the top of `blender_render.py`.
- Colour variants share a model and swap textures via the config's `hiddenSelectionsTextures`. `layout.py` applies these, so `BaseballCap_Red` renders red.
- Models must be MLOD. ODOL files (the game's binarised format) are reported as `odol` in the layout summary and skipped; debinarise them with DeP3d first.
- Textures: `_co`/`_ca` colour, `_nohq` normal map, `_smdi` gloss (converted to glTF roughness).
