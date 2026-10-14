# Asset Lab

A local visual editor and theme maker for **Mitos.is: The Game** (Steam). Repaint any sprite, theme the whole game from
a prompt, or redraw art with a local image model, then install your mod and restore the originals with one click.
Everything runs on your own PC.

## Get started

1. Download **AssetLab.exe** from the latest [release](../../releases/latest) and run it.
2. The first start sets itself up in `%LOCALAPPDATA%\AssetLab` (about 30 MB: a portable Python with numpy and Pillow).
3. It offers the optional local AI (about 6.3 GB): the smart Theme AI planner and image redesigns. You can skip it and
   download it later. It needs 16 GB of RAM; a graphics card with 6 GB or more makes an image take about 35 seconds.
4. Asset Lab opens in your browser. Keep the small Asset Lab window open while you work; closing it stops the lab.

Asset Lab finds your Mitos.is installation through Steam. It never needs administrator rights.

## What it does

- **Library:** every sprite, sheet, font glyph and background in the game, with search, categories, favorites, your
  own names for assets, image search ("find similar"), and a removed-assets pool for things you never want to see.
- **Editor:** paint, erase, import a replacement, adjust colors, and preview as a texture, tile, cell or button.
- **Theme AI:** describe a look ("rainbow neon outlines with a subtle glow", "holographic glass panels", "dark ocean
  theme, keep the logo") and review a plan with before/after samples before anything is saved. Check assets in the
  library to theme only those.
- **Redesign (local AI):** "make the title spooky with dripping blood and bats", "put a party hat on the logo". Pick a
  variant, adjust its size and position, refine it with a follow-up, then apply.
- **Review and install:** see every change before and after, take any item out, install into the game, and update an
  installed mod without restoring first. **Restore original** puts the exact original files back.

## Updates and your data

Asset Lab checks for a newer release when it starts and asks before updating. Your work lives in your user folder
(`%USERPROFILE%\AssetLab`), separate from the app, and is never replaced by an update; the settings files are backed up
to `%LOCALAPPDATA%\AssetLab\backups` before each update.

## Good to know

- Close Mitos.is before installing or restoring; Asset Lab offers to close it for you.
- Mitos.is checks its own `Mitosis.exe` and closes if the file was changed, so Asset Lab only edits art files.
- Steam may replace modded files when the game updates. Use **Restore original** before an update, or **Clear stale
  install** if Steam already replaced them.
- Share mods only from people you trust; never run downloaded executables you have not checked.

## Building from source

Requires Windows with the .NET Framework 4.8 compiler (included with Windows) and Python 3.13 with numpy and Pillow.

```powershell
python -m pip install numpy==2.5.4 pillow==12.3.0
$env:PYTHONPATH = 'src;tests'; python -B -X utf8 -m unittest discover -s tests
./tools/build_release.ps1          # dist\AssetLab.exe and dist\asset-lab-app.zip
```

Every push to `main` builds a release through GitHub Actions (`.github/workflows/release.yml`), numbered
`<VERSION>.<run number>`.

Asset Lab is a fan-made tool and is not affiliated with the developers of Mitos.is. Licensed under the MIT License;
the downloaded components keep their own licences (see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)).
