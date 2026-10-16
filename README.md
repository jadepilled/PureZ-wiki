# PureZ Wiki

Source for [wiki.purez.gg](https://wiki.purez.gg). Every push to `main` rebuilds the site and deploys it through GitHub Actions; the built site is also kept on the `gh-pages` branch. In the repository's Pages settings, set Source to **GitHub Actions** (or to the `gh-pages` branch). The `main` branch holds the generator, not the site.

## Editing

Pages live in `content/`. Each item is a small Markdown file:

```markdown
---
class: AKM
title: AKM
---
Optional notes. They appear at the bottom of the page.
```

- `class` links the page to the game data. Stats, loot, recoil, crafting and treatments fill in automatically.
- `title` and `summary` override the in-game name and description.
- `order` sorts pages within a category (lower first).
- A file with no `class` is a plain page.

Edit files on GitHub (pencil icon) and commit. The site updates in about a minute.

## Data

`data/` is generated. Don't edit it by hand. Re-run the extractors after a game update or an economy change:

```sh
python extract/mission.py --purez ../PureZ/PureZ.nasdara --out data/mission.json
python extract/scripts.py --scripts <DayZ data>/scripts --out data/scripts.json
python extract/configs.py --dz <DayZ data> --out data/configs.json
```

## Media

| Kind | Path | Format |
|---|---|---|
| Render | `media/renders/<category>/<Class>.webp` | 1600×1200, transparent |
| 3D model | `media/models/<Class>.glb` | Draco-compressed GLB |
| Sound | `media/sounds/<Class>/<sample>.ogg` | Ogg Vorbis |

Pages pick these up automatically when the file exists.

## Local preview

```sh
pip install -r requirements.txt
python build.py --preview
python -m http.server -d _site
```

`--preview` shows placeholders where renders and config stats are still missing.
