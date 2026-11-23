#!/usr/bin/env python3
"""Build the static score library from scores/*/meta.toml into _site/.

Each subfolder of scores/ is one piece; its folder name is the URL slug
(scores/ros -> /ros/), which is printed in QR codes and must never change.
"""

import argparse
import html
import shutil
import subprocess
import sys
import tomllib
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
FILE_TYPES = {"pdf": "PDF", "dorico": "Dorico"}
TRANSLITERATIONS = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
})


class BuildError(Exception):
    pass


@dataclass
class Version:
    date: date
    notes: str
    files: dict[str, Path]  # file type -> source path
    outputs: dict[str, str] = field(default_factory=dict)  # file type -> path relative to piece dir
    thumbnail: str = ""  # path relative to piece dir, only for current versions with a PDF


@dataclass
class Arrangement:
    id: str
    name: str
    versions: list[Version]  # newest first


@dataclass
class Piece:
    slug: str
    title: str
    composer: str
    arranger: str
    notes: str
    arrangements: list[Arrangement]

    @property
    def thumbnail(self) -> str:
        return self.arrangements[0].versions[0].thumbnail

    @property
    def last_updated(self) -> date:
        return max(a.versions[0].date for a in self.arrangements)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(TRANSLITERATIONS)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = "".join(c if c.isalnum() else "-" for c in text.lower())
    return "-".join(part for part in slug.split("-") if part)


def load_piece(folder: Path) -> Piece:
    meta_path = folder / "meta.toml"
    with meta_path.open("rb") as f:
        meta = tomllib.load(f)

    def fail(msg: str):
        raise BuildError(f"{meta_path.relative_to(ROOT)}: {msg}")

    if "title" not in meta:
        fail("missing 'title'")
    raw_arrangements = meta.get("arrangement", [])
    if not raw_arrangements:
        fail("needs at least one [[arrangement]]")

    arrangements = []
    for i, raw in enumerate(raw_arrangements):
        arr_id = raw.get("id", "")
        if i > 0 and not arr_id:
            fail(f"arrangement #{i + 1} needs an 'id' (only the first one may omit it)")
        versions = []
        for raw_version in raw.get("version", []):
            if not isinstance(raw_version.get("date"), date):
                fail(f"every version needs a 'date' like 2026-11-23 (arrangement '{arr_id or i + 1}')")
            files = {}
            for ftype in FILE_TYPES:
                if ftype in raw_version:
                    src = folder / raw_version[ftype]
                    if not src.is_file():
                        fail(f"file not found: {raw_version[ftype]}")
                    files[ftype] = src
            if not files:
                fail(f"version {raw_version['date']} lists no files (use keys: {', '.join(FILE_TYPES)})")
            versions.append(Version(raw_version["date"], raw_version.get("notes", ""), files))
        if not versions:
            fail(f"arrangement '{arr_id or i + 1}' has no [[arrangement.version]]")
        versions.sort(key=lambda v: v.date, reverse=True)
        arrangements.append(Arrangement(arr_id, raw.get("name", ""), versions))

    piece = Piece(
        slug=folder.name,
        title=meta["title"],
        composer=meta.get("composer", ""),
        arranger=meta.get("arranger", ""),
        notes=meta.get("notes", ""),
        arrangements=arrangements,
    )
    assign_output_names(piece, fail)
    warn_unreferenced(piece, folder)
    return piece


def assign_output_names(piece: Piece, fail) -> None:
    """Newest version of each arrangement gets the plain name, older ones go to archive/."""
    base = slugify(piece.title)
    taken = set()
    for i, arr in enumerate(piece.arrangements):
        stem = base if i == 0 else f"{base}-{slugify(arr.id)}"
        for j, version in enumerate(arr.versions):
            for ftype, src in version.files.items():
                ext = src.suffix.lower()
                out = f"{stem}{ext}" if j == 0 else f"archive/{stem}-{version.date}{ext}"
                if out in taken:
                    fail(f"two files would both be published as '{out}' (same date twice?)")
                taken.add(out)
                version.outputs[ftype] = out
            if j == 0 and "pdf" in version.files:
                version.thumbnail = f"{stem}.png"


def warn_unreferenced(piece: Piece, folder: Path) -> None:
    # macOS may store umlauts decomposed (NFD) while meta.toml has them composed (NFC)
    def key(path: Path) -> str:
        return unicodedata.normalize("NFC", str(path.resolve()))

    referenced = {key(src) for a in piece.arrangements for v in a.versions for src in v.files.values()}
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower().lstrip(".") in FILE_TYPES and key(path) not in referenced:
            print(f"warning: {path.relative_to(ROOT)} is not listed in meta.toml", file=sys.stderr)


# --- HTML ---

def e(text) -> str:
    return html.escape(str(text))


def page(title: str, body: str, css_href: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="stylesheet" href="{css_href}">
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


def format_size(path: Path) -> str:
    size = path.stat().st_size
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"


def render_thumbnail(pdf: Path, dest: Path) -> bool:
    """Render the first page of the PDF as a PNG (480px wide, shown at 240px for sharp HiDPI)."""
    try:
        subprocess.run(
            ["pdftoppm", "-png", "-f", "1", "-l", "1", "-singlefile",
             "-scale-to-x", "480", "-scale-to-y", "-1", str(pdf), str(dest.with_suffix(""))],
            check=True, capture_output=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError) as err:
        print(f"warning: no thumbnail for {pdf.relative_to(ROOT)}: {err}", file=sys.stderr)
        return False


def thumbnail_img(version: Version, alt: str) -> str:
    if not version.thumbnail:
        return ""
    return (f'<a class="thumb" href="{e(version.outputs["pdf"])}">'
            f'<img src="{e(version.thumbnail)}" alt="{e(alt)}" width="240" loading="lazy"></a>')


def download_links(version: Version) -> str:
    links = []
    for ftype, out in version.outputs.items():
        filename = out.rsplit("/", 1)[-1]
        links.append(
            f'<a class="dl dl-{ftype}" href="{e(out)}" download="{e(filename)}">'
            f'{FILE_TYPES[ftype]} <small>{format_size(version.files[ftype])}</small></a>'
        )
    return " ".join(links)


def credits(piece: Piece) -> str:
    parts = []
    if piece.composer:
        parts.append(e(piece.composer))
    if piece.arranger:
        parts.append(f"arr. {e(piece.arranger)}")
    return f'<p class="credits">{" · ".join(parts)}</p>' if parts else ""


def render_piece(piece: Piece) -> str:
    sections = []
    for arr in piece.arrangements:
        current, older = arr.versions[0], arr.versions[1:]
        heading = f"<h2>{e(arr.name)}</h2>" if arr.name else ""
        notes = f'<p class="notes">{e(current.notes)}</p>' if current.notes else ""
        history = ""
        if older:
            rows = "\n".join(
                f'<li><time datetime="{v.date}">{v.date}</time> {download_links(v)}'
                + (f'<span class="notes">{e(v.notes)}</span>' if v.notes else "")
                + "</li>"
                for v in older
            )
            history = f'<details><summary>Older versions ({len(older)})</summary><ul class="history">\n{rows}\n</ul></details>'
        thumb = thumbnail_img(current, f"First page of {piece.title}" + (f" ({arr.name})" if arr.name else ""))
        sections.append(f"""<section>
{heading}
<div class="current">
{thumb}
<div>
<p class="meta">Current version · <time datetime="{current.date}">{current.date}</time></p>
<p class="downloads">{download_links(current)}</p>
{notes}
</div>
</div>
{history}
</section>""")

    piece_notes = f'<p class="notes">{e(piece.notes)}</p>' if piece.notes else ""
    body = f"""<nav><a href="../">← All scores</a></nav>
<h1>{e(piece.title)}</h1>
{credits(piece)}
{piece_notes}
{chr(10).join(sections)}"""
    return page(piece.title, body, "../style.css")


def render_index(pieces: list[Piece], site_title: str) -> str:
    items = "\n".join(
        f'<li><a href="{e(p.slug)}/">'
        + (f'<img src="{e(p.slug)}/{e(p.thumbnail)}" alt="" width="240" loading="lazy">' if p.thumbnail else '<span class="noimg"></span>')
        + f'<span class="title">{e(p.title)}</span></a>'
        + (f'<span class="credits">{e(p.composer)}</span>' if p.composer else "")
        + f'<time datetime="{p.last_updated}">Updated {p.last_updated}</time></li>'
        for p in pieces
    )
    body = f"""<h1>{e(site_title)}</h1>
<ul class="pieces">
{items}
</ul>"""
    return page(site_title, body, "style.css")


def render_404() -> str:
    # GitHub Pages serves this for every missing path. All published paths are
    # lowercase, so redirect e.g. /ROS (uppercase keeps QR codes small) to /ros.
    # Links are root-relative because this page is served under arbitrary paths.
    body = """<script>
const lower = location.pathname.toLowerCase();
if (lower !== location.pathname) location.replace(lower + location.search + location.hash);
</script>
<h1>Not found</h1>
<p>This page doesn't exist. <a href="/">Browse all scores</a></p>"""
    return page("Not found", body, "/style.css")


CSS = """\
:root {
  --bg: #fdfcf9; --fg: #1d1b18; --muted: #6b665e; --line: #e4e0d8; --accent: #7a2e1f;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #1a1917; --fg: #ece9e3; --muted: #9c968c; --line: #34312c; --accent: #e8a089; color-scheme: dark; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 17px/1.5 Georgia, "Times New Roman", serif; }
main { max-width: 40rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
h1 { font-size: 1.9rem; line-height: 1.2; margin: 0 0 .25rem; }
h2 { font-size: 1.2rem; margin: 0 0 .5rem; }
a { color: var(--accent); }
nav { margin-bottom: 1.5rem; font-size: .9rem; }
nav a { text-decoration: none; }
.credits, .meta, time, small { color: var(--muted); }
.meta { font-size: .9rem; margin: 0 0 .5rem; }
.notes { display: block; font-style: italic; color: var(--muted); margin: .25rem 0 0; }
section { border-top: 1px solid var(--line); padding: 1.25rem 0; margin-top: 1.25rem; }
.downloads { margin: 0; display: flex; flex-wrap: wrap; gap: .5rem; }
.dl { display: inline-block; padding: .45rem .9rem; border: 1px solid var(--accent); border-radius: 6px;
      text-decoration: none; font-family: system-ui, sans-serif; font-size: .95rem; }
.dl-pdf { background: var(--accent); color: var(--bg); }
.dl-pdf small { color: inherit; opacity: .8; }
details { margin-top: 1rem; }
summary { cursor: pointer; color: var(--muted); font-size: .9rem; }
.history { list-style: none; padding: 0; margin: .75rem 0 0; }
.history li { padding: .5rem 0; border-bottom: 1px solid var(--line); }
.history time { margin-right: .5rem; font-size: .9rem; }
.history .dl { padding: .2rem .6rem; font-size: .85rem; }
.current { display: flex; flex-wrap: wrap; gap: 1.25rem; align-items: flex-start; }
.thumb img, .pieces img { display: block; width: 100%; height: auto; background: #fff;
      border: 1px solid var(--line); border-radius: 3px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
.thumb { flex: 0 0 10rem; max-width: 100%; }
.pieces { list-style: none; padding: 0; margin: 1.5rem 0 0; display: grid;
          grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: 2rem 1.5rem; }
.pieces li { display: flex; flex-direction: column; gap: .15rem; }
.pieces a { text-decoration: none; display: flex; flex-direction: column; gap: .6rem; }
.pieces .noimg { display: block; aspect-ratio: 1 / 1.414; border: 1px dashed var(--line); border-radius: 3px; }
.pieces .title { font-size: 1.05rem; line-height: 1.3; }
.pieces .credits, .pieces time { font-size: .85rem; }
"""


def build(scores_dir: Path, out_dir: Path, site_title: str) -> None:
    folders = sorted(p for p in scores_dir.iterdir() if p.is_dir())
    pieces = []
    for folder in folders:
        if not (folder / "meta.toml").is_file():
            print(f"warning: skipping {folder.relative_to(ROOT)} (no meta.toml)", file=sys.stderr)
            continue
        if folder.name != folder.name.lower() or slugify(folder.name) != folder.name:
            raise BuildError(f"{folder.relative_to(ROOT)}: folder name must be lowercase a-z, 0-9 and '-' (it becomes the URL)")
        pieces.append(load_piece(folder))
    pieces.sort(key=lambda p: slugify(p.title))

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    (out_dir / "style.css").write_text(CSS)
    (out_dir / "index.html").write_text(render_index(pieces, site_title))
    (out_dir / "404.html").write_text(render_404())
    for piece in pieces:
        piece_dir = out_dir / piece.slug
        for arr in piece.arrangements:
            for version in arr.versions:
                for ftype, out in version.outputs.items():
                    dest = piece_dir / out
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(version.files[ftype], dest)
                if version.thumbnail and not render_thumbnail(version.files["pdf"], piece_dir / version.thumbnail):
                    version.thumbnail = ""
        (piece_dir / "index.html").write_text(render_piece(piece))
    print(f"Built {len(pieces)} piece(s) into {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scores", type=Path, default=ROOT / "scores")
    parser.add_argument("--out", type=Path, default=ROOT / "_site")
    parser.add_argument("--title", default="Sheet Music Library")
    args = parser.parse_args()
    try:
        build(args.scores, args.out, args.title)
    except BuildError as err:
        sys.exit(f"error: {err}")


if __name__ == "__main__":
    main()
