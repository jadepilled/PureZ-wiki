# PureZ Wiki: plan for full coverage

The goal is a page for every item a PureZ player can find, built from the game files and the live PureZ economy, with real renders and numbers other wikis don't have. Pages stay short. Each section appears only when it has data.

## 1. Data sources

| Source | Extractor | Gives | Status |
|---|---|---|---|
| PureZ mission (`PureZ.nasdara`) | `extract/mission.py` | Spawn counts, despawn and restock times, locations, tiers, spawn condition, fill levels, spawn attachments, what infected wear and carry, vehicles, infected areas, gameplay settings | Done |
| DayZ scripts (1.30) | `extract/scripts.py` | Diseases and agents, drug effects per dose, recoil patterns, crafting recipes, item → effect links | Done on the 1.30 scripts |
| DayZ configs (1.30) | `extract/configs.py` | Names and descriptions, weight, size, capacity, fire modes and RPM, dispersion, magazines, ammo damage (health, blood, shock), muzzle velocity, ballistic tables, armour, insulation, nutrition, melee damage, optics zoom and zeroing, attachment compatibility, repair kits, vehicle parts and fuel | Done |
| Models and textures | `pipeline/` | Transparent renders, web 3D models | MLOD → GLB → Blender tested. Game models are ODOL v56: `pipeline/odol.py` decodes them (own decoder, verified on the AKM and Ada 4x4 samples), and `layout.py` assembles weapons with their default buttstock, handguard and magazine at the proxy transforms (see `pipeline/README.md`) |
| Sounds | config sound sets → `media/sounds/` | Gunshot playback per fire mode | Paths resolved from configs. Conversion runs with the media step |

## 2. Which items get pages

- Every class with `scope = 2` in config that either spawns on PureZ (`nominal > 0`), is fitted by a spawn, is worn or carried by infected, or is a crafting result.
- **Colour variants share one page.** `BaseballCap_Red`, `_Blue` and so on become "Baseball cap" with a row of variant swatches. This turns about 2,400 loot types into roughly 700 pages.
- Skipped: test, debug and admin classes, and ruined or "dummy" helpers.
- Estimated pages: weapons ~70, ammunition ~45, attachments ~140, clothing ~260, gear and tools ~150, medical ~25, food and drink ~80, vehicles ~10, health ~15, plus server and crafting.

Pages are generated automatically. A Markdown file in `content/` is only needed to add notes or override a name.

## 3. Page contents by category

**Weapons:** render or 3D model, gunshot audio, rate of fire per mode, dispersion (MOA), recoil (kick and muzzle path), magazines, compatible attachments by slot (optics, muzzle, stock, handguard, light), ammunition with damage and ballistic table, what it spawns with on PureZ and how often, condition range, locations, durability, repair kits.

**Ammunition:** damage to health, blood and shock, muzzle velocity per weapon, ballistic table (velocity, drop and flight time to 1,000 m), pile size on PureZ, which weapons fire it.

**Attachments:** slot, fits which weapons, zoom levels and zeroing for optics, magazine capacity, PureZ spawn chance on weapons.

**Clothing:** slot, storage grid, insulation, waterproofing, armour against projectiles, melee and fragments, worn by which infected, colour variants.

**Gear and tools:** storage, quantity and liquid capacity in ml, uses and actions, recipes.

**Medical:** effect duration, what it treats and how many agents one dose clears, side effects, recipes.

**Food and drink:** energy (kcal), water (ml), volume, toxicity, food stages (raw, baked, boiled, dried, burnt) with their values, diseases it can carry.

**Vehicles:** spawn count and points, fitted parts, fuel and fluid capacities, seats, cargo, top speed, parts list with links.

**Health:** growth and die-off rates, when symptoms start and stop, immunity level that stops it, how it spreads, treatments with doses, effects in real units.

## 4. Features not on other wikis

- PureZ spawn data per item: counts, locations, condition and attachment odds on this server.
- "Worn or carried by infected" with odds per infected type.
- Drug effectiveness per dose against each disease, from the scripts.
- Recoil shown as kick direction and muzzle path, from the recoil scripts.
- Ballistic tables computed from config air friction and muzzle velocity.
- Gunshot audio from the game's own sound sets.
- Transparent renders and rotatable 3D models of the real game assets.

## 5. Media standards

| Kind | Spec |
|---|---|
| Render | 1600×1200 WebP, transparent, per-category camera preset, about 60–150 KB |
| 3D model | GLB with Draco geometry and WebP textures, loaded only when opened, aim for under 2 MB |
| Sound | Ogg Vorbis, 96 kbps, close-range shot plus tail |

## 6. Checks before trusting a number

These are computed rather than read directly, so each will be spot-checked in game once:

1. Dispersion → MOA conversion (radians × 3,437.75).
2. Ballistic model (point mass with `airFriction`) against in-game drop at 300 and 500 m for two calibres.
3. Recoil labels: kick strength and angle compared across the AKM, M4-A1 and Mosin.
4. Drug per-dose figures against an infected character on a test server.

## 7. Order of work

1. **Now:** framework, design, extractors, demo pages, render pipeline (done).
2. **When the 1.30 upload lands:** run the config extractor, switch pages to config names, turn on automatic pages for all items, group colour variants.
3. **Renders:** `python pipeline/layout.py --render F:\PureZ\PureZ-render --only AKM` and the Blender batch to check one gun, then the full set; commit `media/`.
4. **Sounds:** convert weapon sound sets and add players.
5. **Polish:** spot-checks from section 6, notes on the most-viewed pages, search tuning.

## 8. Keeping it current

- PureZ economy changes: re-run `extract/mission.py` and push. Pages update on deploy.
- Game updates: re-run the trim tool and all extractors, re-render changed models only (`layout.py --only`).
- Your notes in `content/` are never overwritten.
