# compactscores

Static site for a sheet music library. `python3 build.py` (Python 3.11+; needs `pdftoppm`, `qrencode`
and `qpdf` on the PATH, e.g. `brew install poppler qrencode qpdf`)
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

## QR codes

The build stamps a QR code linking to `HTTPS://ZUA.AT/<SLUG>` onto every published PDF,
top-left on page 1, 14 mm wide, with the `/slug` label next to it. So export PDFs from
Dorico *without* a QR code. Adjust or disable it per piece, arrangement or version
(the most specific setting wins):

```toml
qr = false                                # no QR code (e.g. the PDF already has one)
qr = { x = 180, y = 14.8 }                # move it: mm from the top-left page corner
qr = { page = 2, x = 15, y = 260, size = 12 }
```

## Scanning for new saves

`python3 scan.py` (add `--dry-run` to preview) updates the `meta.toml` files:

- New folder without `meta.toml`: creates one (check the title, it comes from the filename).
- A listed file that differs from the last commit (re-exported over `<title>.pdf`): the
  committed copy moves to `old/` and a new version dated by the save time is added.
  Saving again on the same day just updates the file.
- New file names in an existing folder: added as a new arrangement.

Commit after each round of updates. The script relies on git to recover the previous file.

## Updating a score

Add a new `[[arrangement.version]]` with a later date and point it at the new file.
Keep the old entry and file (e.g. move it to `old/`). The newest version is published
as `es-ist-ein-ros-entsprungen.pdf`; older ones end up in `archive/` with their date.

## Setup

In the repository settings under *Pages*, set *Source* to **GitHub Actions**.
