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
2. **Convert models** into the work tree, reading textures from the render folder. Start with one gun:
   `python pipeline/layout.py --render F:\PureZ\PureZ-render --only AKM`
   then everything on the wiki: `python pipeline/layout.py --render F:\PureZ\PureZ-render` (every item in any
   mission's economy, about 2,000 classes; `--all` for every config class with a model). It prints one line per
   class and ends with the counts; `missing` lines mean the model is not in the render folder.
3. **Render**: `pipeline\render.bat` (everything, resumable) or `pipeline\render.bat AKM` (one class). It runs
   `blender --background --factory-startup --python pipeline/blender_render.py -- --work pipeline/work --out media --resume`.
   Set `BLENDER_EXE` at the top of the batch file if Blender is not at the Blender 5.1 default path.
   `pipeline\render.bat -- --check` only verifies Blender, the glTF add-on and the work tree.
   Progress goes to the console and to `pipeline\work\render.log`; failures with tracebacks to `media\renders\report.json`.
4. Commit `media/`. Pages show the render and 3D model automatically.

Useful options after `render.bat --`: `--only AKM` (repeatable, substring), `--engine CYCLES --gpu` for the slower
path-traced look (EEVEE is the default), `--samples 32` for drafts, `--size 1600x1200`, `--format png`,
`--limit 20`, `--shard-index 0 --shard-count 4` to run several Blenders at once, `--no-glb`.

If nothing renders, check in this order: `pipeline\work` has `<category>\<Class>\model.glb` folders (layout.py
step), `render.bat -- --check` reports the models, and `pipeline\work\render.log` names the failing class.

To get editable MLOD files (for Object Builder or the Blender Arma toolbox) run the decoder on its own:
`python pipeline/odol.py model.p3d` writes `model.mlod.p3d`; `python pipeline/odol.py --batch F:/PureZ/PureZ-render`
converts every ODOL file in place and keeps the original as `.odol`. Only the visual LODs are written
unless `--all-lods` is given. The batch run prints one line per file with its size, elapsed time and the
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
