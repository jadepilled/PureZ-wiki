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

The game ships binarised ODOL models. `pipeline/odol.py` reads DayZ's ODOL version 56 directly
(worked out from the game files: LZO-compressed arrays, DayZ's extended material block, 16-bit
indices), so nothing needs debinarising first. From the wiki repo on your PC:

1. **Extract configs** (done whenever the game data changes):
   `python extract/configs.py --dz ../PureZ --out data/configs.json`
2. **Convert and render in one go**: `pipeline\pipeline.bat` (everything the wiki needs) or
   `pipeline\pipeline.bat AKM` (one class). It shows a progress bar per stage, resumes where it left off, and
   keeps the full logs in `pipeline\work\layout.log` and `render.log`. Edit `RENDER_ROOT` and `BLENDER_EXE`
   at the top of the batch file if the render folder or Blender live elsewhere. Options go after `--`
   (`pipeline.bat -- --fresh` re-renders everything; `pipeline.bat -- --skip-convert` only renders;
   a second `--` passes Blender options, e.g. `pipeline.bat -- -- --engine CYCLES --gpu`).
   The two stages can also be run on their own:
   - `python pipeline/layout.py --render F:/PureZ/PureZ-render [--only AKM]` makes `pipeline/work/<category>/<Class>/`
     with `model.glb` (for Blender), `web.glb` (what the wiki serves: the single most detailed LOD, lossless WebP
     textures stored once each, quantised normals and indices) and `item.json`. `--web-tex 1024` or `--web-lossy 85`
     shrink the web model further at some loss of detail.
   - `pipeline\render.bat [AKM]` or `pipeline\render.bat -- --check` runs Blender alone (see blender_render.py).
3. Commit `media/`. Pages show the render and 3D model automatically.

Renders are cropped to the model plus a 4% margin out of a 2400x1800 frame (`--size`, `--no-crop`), WebP
quality 92 (`--quality 100` for lossless, `--format png`), EEVEE by default (`--engine CYCLES --gpu` for the
slower path-traced look), exposure -0.3 with the AgX base look (`--exposure`). Progress and failures: console,
`pipeline\work\render.log`, and `media\renders\report.json` with tracebacks.

If nothing renders, check in this order: `pipeline\work` has `<category>\<Class>\model.glb` folders (convert
stage), `render.bat -- --check` reports the models, and `pipeline\work\render.log` names the failing class.

To get editable MLOD files (for Object Builder or the Blender Arma toolbox) run the decoder on its own:
`python pipeline/odol.py model.p3d` writes `model.mlod.p3d`; `python pipeline/odol.py --batch F:/PureZ/PureZ-render`
converts every ODOL file in place and keeps the original as `.odol`. Only the most detailed visual LOD is
written (`--lods visual` for every graphical LOD, `--lods all` for everything), which halves the time and the file size. The batch run prints one line per file with its size, elapsed time and the
time left, runs at below-normal priority, and skips files over 160 MB and single LODs over 64 MB
(`--max-size` / `--max-lod` to change). `--limit 20` does a trial run. Results go to `odol.log` in the
folder, and converted files are skipped on a rerun. `pip install python-lzo` makes decoding several
times faster; the script says which decoder it is using. In Git Bash, quote Windows paths or use
forward slashes. DayZ ships ODOL versions 53 to 56; 56 is verified, the others use the same layout until
proven otherwise. If any fail, run with `--collect-samples F:/PureZ/PureZ-wiki/samples/odol` and commit
that folder so the decoder can be extended from real files.
`deodol.py` (the DeODOL driver) is kept as an alternative but is no longer needed.

## Weapon attachments

A weapon model carries one proxy triangle per attachment slot (named `proxy:\dz\weapons\attachments\...`).
`layout.py` reads them, works out the slot from the placeholder model's config class, and merges in
the attachment PureZ spawns there most often (`cfgspawnabletypes`): buttstock and handguard, which
give the gun its shape, and the magazine. `--slots buttstock,handguard` leaves magazines out;
`--slots ""` renders bare receivers. `item.json` lists what was fitted (`fitted`) with its spawn chance.

Each proxy carries a full transform in the ODOL file (the magazine on the KA-M, for instance, is tilted
about 20 degrees). The converter writes it into the MLOD proxy triangle and `layout.py` applies it, so
attachments land where the game puts them.

## Notes

- Camera angles are set per category in `PRESETS` at the top of `blender_render.py`.
- Colour variants share a model and swap textures via the config's `hiddenSelectionsTextures`. `layout.py` applies these, so `BaseballCap_Red` renders red.
- ODOL files are decoded on the fly; a model that fails to decode is reported as `failed` in the layout summary with the reason.
- Textures: `_co`/`_ca` colour, `_nohq` normal map, `_smdi` gloss (converted to glTF roughness).
