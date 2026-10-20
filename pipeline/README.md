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

The game ships binarised ODOL models, which Blender can't read. Convert them to MLOD first, then
build the work tree and render. From the wiki repo on your PC:

1. **Debinarise** the render set (skips files that are already MLOD, so it can be re-run):
   `python pipeline/deodol.py F:\PureZ\PureZ-render`
   This runs `C:\Users\Jade\Desktop\Deodol54.exe` on every ODOL file and falls back to
   `DeODOL53.exe` for any file v54 leaves binarised. Failures are listed in `PureZ-render\deodol.log`.
   `pipeline\deodol.bat` does the same from a double-click. Pass `--exe`/`--fallback` if the tools move.
2. **Extract configs** (done whenever the game data changes):
   `python extract/configs.py --dz ../PureZ --out data/configs.json`
3. **Convert models** into the work tree, reading textures from the render folder. Start with one gun:
   `python pipeline/layout.py --render F:\PureZ\PureZ-render --only AKM`
   then everything on the wiki: `python pipeline/layout.py --render F:\PureZ\PureZ-render` (`--all` for every item).
4. **Render** (Windows example):
   `"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe" -b -P pipeline/blender_render.py -- --work pipeline/work --out media --gpu --only AKM`
5. Commit `media/`. Pages show the render and 3D model automatically.

Useful options: `--only AKM` to redo one item, `--samples 64` for faster drafts, `--size 1600x1200`.

## Weapon attachments

A weapon model carries one proxy triangle per attachment slot (named `proxy:\dz\weapons\attachments\...`).
`layout.py` reads them, works out the slot from the placeholder model's config class, and merges in
the attachment PureZ spawns there most often (`cfgspawnabletypes`): buttstock and handguard, which
give the gun its shape, and the magazine. `--slots buttstock,handguard` leaves magazines out;
`--slots ""` renders bare receivers. `item.json` lists what was fitted (`fitted`) with its spawn chance.

DayZ attachments are modelled in the weapon's own space, so they are placed at the proxy's origin
with no rotation. If a render shows an attachment turned the wrong way, re-run with
`--proxy-frame triangle`, which also applies the orientation encoded in the proxy triangle.

## Notes

- Camera angles are set per category in `PRESETS` at the top of `blender_render.py`.
- Colour variants share a model and swap textures via the config's `hiddenSelectionsTextures`. `layout.py` applies these, so `BaseballCap_Red` renders red.
- Models must be MLOD. ODOL files (the game's binarised format) are reported as `odol` in the layout summary and skipped; run `deodol.py` first.
- Textures: `_co`/`_ca` colour, `_nohq` normal map, `_smdi` gloss (converted to glTF roughness).
