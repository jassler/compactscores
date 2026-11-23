# compactscores

Static site for a sheet music library. `python3 build.py` (Python 3.11+, no dependencies)
reads `scores/*/meta.toml` and writes the site to `_site/`. On every push to `main`,
the GitHub Action builds and deploys it to GitHub Pages.

## Adding a piece

Create `scores/<slug>/` with the files and a `meta.toml`. The slug becomes the URL
(`/ros/`) that the QR codes point to, so it must be lowercase and never change.

```toml
title = "Es ist ein Ros entsprungen"
composer = "…"        # optional
arranger = "…"        # optional

[[arrangement]]       # the first arrangement gets the plain <title>.pdf name
name = "SATB"         # optional heading

  [[arrangement.version]]
  date = 2026-11-23
  pdf = "Es ist ein Ros entsprungen.pdf"
  dorico = "Es ist ein Ros entsprungen.dorico"
  notes = "Fixed alto in bar 12"   # optional

[[arrangement]]
id = "ssaatb"         # required for every arrangement after the first; used as filename suffix
name = "SSAATB"

  [[arrangement.version]]
  date = 2026-11-23
  pdf = "Ros SSAATB.pdf"
```

## Updating a score

Add a new `[[arrangement.version]]` with a later date and point it at the new file.
Keep the old entry and file (e.g. move it to `old/`). The newest version is published
as `es-ist-ein-ros-entsprungen.pdf`; older ones end up in `archive/` with their date.

## Setup

In the repository settings under *Pages*, set *Source* to **GitHub Actions**.
