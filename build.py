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
import tempfile
import tomllib
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
FILE_TYPES = {"pdf": "PDF", "dorico": "Dorico"}
# QR codes link to QR_BASE_URL + slug. Uppercase lets the QR use its compact
# alphanumeric mode; 404.html redirects uppercase paths to the lowercase pages.
QR_BASE_URL = "HTTPS://ZUA.AT/"
# Matches the score template: top-left corner on page 1, 14 mm wide
QR_DEFAULTS = {"page": 1, "x": 14.8, "y": 14.8, "size": 14.0}  # mm from the top-left page corner
MM = 72 / 25.4
# Piece pages render the full PDF in the browser with PDF.js (falls back to the thumbnail)
PDFJS_URL = "https://cdn.jsdelivr.net/npm/pdfjs-dist@6.5.438/build/"
TRANSLITERATIONS = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
})


class BuildError(Exception):
    pass


def show(path: Path) -> Path:
    """Path for messages: relative to the repo when inside it."""
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


@dataclass
class Version:
    date: date
    notes: str
    files: dict[str, Path]  # file type -> source path
    qr: dict | None  # QR placement for the PDF, None to leave the PDF untouched
    outputs: dict[str, str] = field(default_factory=dict)  # file type -> path relative to piece dir
    sizes: dict[str, int] = field(default_factory=dict)  # file type -> published size in bytes
    thumbnail: str = ""  # path relative to piece dir, only for current versions with a PDF


@dataclass
class Recording:
    url: str
    title: str
    notes: str


@dataclass
class Arrangement:
    id: str
    name: str
    versions: list[Version]  # newest first
    recordings: list[Recording] = field(default_factory=list)


@dataclass
class Piece:
    slug: str
    title: str
    composer: str
    arranger: str
    notes: str
    arrangements: list[Arrangement]
    recordings: list[Recording] = field(default_factory=list)

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


def resolve_qr(levels: list, fail) -> dict | None:
    """Merge `qr` settings from piece, arrangement and version; the most specific wins.

    Each level may be true/false or a table with page, x, y and size (mm).
    """
    qr = dict(QR_DEFAULTS)
    enabled = True
    for level in levels:
        if level is None:
            continue
        if isinstance(level, bool):
            enabled = level
        elif isinstance(level, dict):
            unknown = level.keys() - QR_DEFAULTS.keys()
            if unknown:
                fail(f"unknown qr setting(s): {', '.join(sorted(unknown))} (allowed: {', '.join(QR_DEFAULTS)})")
            enabled = True
            qr.update(level)
        else:
            fail("'qr' must be true, false or a table like { x = 15, y = 15 }")
    return qr if enabled else None


def load_recordings(raw, fail) -> list[Recording]:
    """[[recording]] tables with a url and an optional title and notes."""
    if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
        fail("recordings must be [[recording]] tables with a 'url'")
    recordings = []
    for r in raw:
        unknown = r.keys() - {"url", "title", "notes"}
        if unknown:
            fail(f"unknown recording setting(s): {', '.join(sorted(unknown))} (allowed: url, title, notes)")
        url = r.get("url", "")
        if not isinstance(url, str) or urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            fail(f"recording needs a 'url' starting with https:// (got {url!r})")
        recordings.append(Recording(url, str(r.get("title", "")), str(r.get("notes", ""))))
    return recordings


def load_piece(folder: Path) -> Piece:
    meta_path = folder / "meta.toml"
    with meta_path.open("rb") as f:
        meta = tomllib.load(f)

    def fail(msg: str):
        raise BuildError(f"{show(meta_path)}: {msg}")

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
            qr = resolve_qr([meta.get("qr"), raw.get("qr"), raw_version.get("qr")], fail)
            versions.append(Version(raw_version["date"], raw_version.get("notes", ""), files, qr))
        if not versions:
            fail(f"arrangement '{arr_id or i + 1}' has no [[arrangement.version]]")
        versions.sort(key=lambda v: v.date, reverse=True)
        arrangements.append(Arrangement(arr_id, raw.get("name", ""), versions,
                                        load_recordings(raw.get("recording", []), fail)))

    piece = Piece(
        slug=folder.name,
        title=meta["title"],
        composer=meta.get("composer", ""),
        arranger=meta.get("arranger", ""),
        notes=meta.get("notes", ""),
        arrangements=arrangements,
        recordings=load_recordings(meta.get("recording", []), fail),
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
            print(f"warning: {show(path)} is not listed in meta.toml", file=sys.stderr)


# --- HTML ---

def e(text) -> str:
    return html.escape(str(text))


def page(title: str, body: str, css_href: str, head: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="stylesheet" href="{css_href}">
{head}</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


# --- PDF processing ---

def qr_matrix(text: str) -> list[list[bool]]:
    out = subprocess.run(["qrencode", "-t", "ASCII", "-m", "0", "-o", "-", text],
                         check=True, capture_output=True, text=True).stdout
    # Each module is printed as two characters, "##" for dark
    return [[line[i] == "#" for i in range(0, len(line), 2)] for line in out.splitlines() if line]


def page_box(pdf: Path, page_no: int) -> tuple[float, float, float, float]:
    out = subprocess.run(["pdfinfo", "-box", "-f", str(page_no), "-l", str(page_no), str(pdf)],
                         check=True, capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()  # "Page    1 TrimBox:      0.00     0.00   595.00   842.00"
        if parts[:3] == ["Page", str(page_no), "TrimBox:"]:
            x0, y0, x1, y1 = map(float, parts[3:7])
            return x0, y0, x1, y1
    raise BuildError(f"{pdf.name} has no page {page_no}")


def qr_overlay_pdf(box: tuple[float, float, float, float], qr: dict, url: str, label: str) -> bytes:
    """A one-page PDF with the same page box, containing only the QR code and its label.

    Mirrors the template: QR without quiet zone, the label in Courier to its right,
    rotated to read top to bottom.
    """
    x0, y0, x1, y1 = box
    matrix = qr_matrix(url)
    module = qr["size"] * MM / len(matrix)
    left = x0 + qr["x"] * MM
    top = y1 - qr["y"] * MM
    ops = []
    for r, row in enumerate(matrix):
        c = 0
        while c < len(row):  # one rectangle per horizontal run of dark modules
            if not row[c]:
                c += 1
                continue
            start = c
            while c < len(row) and row[c]:
                c += 1
            ops.append(f"{left + start * module:.3f} {top - (r + 1) * module:.3f} {(c - start) * module:.3f} {module:.3f} re")
    font_size = qr["size"] * MM * 30 / 168  # same ratio as the 30pt label next to the 168px QR
    label_x = left + len(matrix) * module + 0.25 * font_size
    label_y = top - 0.2 * font_size
    content = (
        "q 0 g\n" + "\n".join(ops) + "\nf Q\n"
        f"BT /F1 {font_size:.3f} Tf 0 -1 1 0 {label_x:.3f} {label_y:.3f} Tm ({label}) Tj ET\n"
    ).encode()

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [{x0} {y0} {x1} {y1}] "
        f"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".encode(),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(pdf)


def stamp_qr(src: Path, dest: Path, qr: dict, slug: str) -> None:
    page_no = qr["page"]
    overlay = qr_overlay_pdf(page_box(src, page_no), qr, QR_BASE_URL + slug.upper(), "/" + slug)
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(overlay)
        f.flush()
        subprocess.run(["qpdf", str(src), "--overlay", f.name, f"--to={page_no}", "--from=1", "--", str(dest)],
                       check=True, capture_output=True)


def publish_pdf(src: Path, dest: Path, qr: dict | None, slug: str) -> None:
    if qr:
        try:
            stamp_qr(src, dest, qr, slug)
            return
        except (OSError, subprocess.CalledProcessError) as err:
            print(f"warning: no QR code added to {show(src)}: {err}", file=sys.stderr)
    shutil.copy2(src, dest)


def format_size(size: int) -> str:
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
        print(f"warning: no thumbnail for {pdf.name}: {err}", file=sys.stderr)
        return False


def pdf_viewer(version: Version, alt: str, collapsed: bool) -> str:
    """All pages of the PDF, drawn by viewer.js; until then (or without JS) the first-page thumbnail.

    Collapsed viewers sit in a closed <details> and only load once opened.
    """
    if not version.thumbnail:
        return ""
    viewer = (f'<div class="viewer" data-pdf="{e(version.outputs["pdf"])}" aria-label="{e(alt)}">'
              f'<a class="sheet" href="{e(version.outputs["pdf"])}">'
              f'<img src="{e(version.thumbnail)}" alt="First page of {e(alt)}" width="480"'
              + (' loading="lazy"' if collapsed else "") + '></a></div>')
    return f'<details class="score"><summary>Show score</summary>{viewer}</details>' if collapsed else viewer


def download_links(version: Version) -> str:
    links = []
    for ftype, out in version.outputs.items():
        filename = out.rsplit("/", 1)[-1]
        links.append(
            f'<a class="dl dl-{ftype}" href="{e(out)}" download="{e(filename)}">'
            f'{FILE_TYPES[ftype]} <small>{format_size(version.sizes[ftype])}</small></a>'
        )
    return " ".join(links)


def recording_links(recordings: list[Recording]) -> str:
    """Links to recordings, labelled with their title or else the site's name (youtube.com)."""
    if not recordings:
        return ""
    items = []
    for r in recordings:
        host = urllib.parse.urlsplit(r.url).hostname or r.url
        host = host.removeprefix("www.").removeprefix("m.")
        label = f'<span>{e(r.title)}</span><small>{e(host)}</small>' if r.title else f'<span>{e(host)}</span>'
        notes = f'<span class="notes">{e(r.notes)}</span>' if r.notes else ""
        items.append(f'<li><a href="{e(r.url)}" rel="noopener">{label}</a>{notes}</li>')
    return f'<div class="recordings"><h3>Recordings</h3><ul>{"".join(items)}</ul></div>'


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
        # With several arrangements, the scores are collapsed so all arrangements fit on screen
        collapsed = len(piece.arrangements) > 1
        viewer = pdf_viewer(current, piece.title + (f" ({arr.name})" if arr.name else ""), collapsed)
        above, below = (viewer, "") if collapsed else ("", viewer)
        sections.append(f"""<section>
{heading}
<p class="meta">Current version · <time datetime="{current.date}">{current.date}</time></p>
<p class="downloads">{download_links(current)}</p>
{notes}
{recording_links(arr.recordings)}
{above}
{history}
{below}
</section>""")

    piece_notes = f'<p class="notes">{e(piece.notes)}</p>' if piece.notes else ""
    body = f"""<nav><a href="../">← All scores</a></nav>
<h1>{e(piece.title)}</h1>
{credits(piece)}
{piece_notes}
{recording_links(piece.recordings)}
{chr(10).join(sections)}"""
    head = f'<script type="module" src="../viewer.js" data-pdfjs="{e(PDFJS_URL)}"></script>\n'
    return page(piece.title, body, "../style.css", head)


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
.recordings { margin-top: 1rem; }
.recordings h3 { font-size: .9rem; font-weight: normal; color: var(--muted); margin: 0 0 .25rem; }
.recordings ul { list-style: none; padding: 0; margin: 0; }
.recordings li { padding: .15rem 0 .15rem 1.1rem; text-indent: -1.1rem; }
.recordings li .notes { text-indent: 0; margin: 0; font-size: .9rem; }
.recordings a { text-decoration: none; }
.recordings a span { text-decoration: underline; }
.recordings li::before { content: "▶"; display: inline-block; width: 1.1rem; text-indent: 0;
      font-size: .7em; color: var(--muted); vertical-align: .15em; }
.recordings small { margin-left: .35rem; font-family: system-ui, sans-serif; font-size: .8rem; }
details { margin-top: 1rem; }
summary { cursor: pointer; color: var(--muted); font-size: .9rem; }
details.score > summary { color: var(--accent); font-size: 1rem; }
.history { list-style: none; padding: 0; margin: .75rem 0 0; }
.history li { padding: .5rem 0; border-bottom: 1px solid var(--line); }
.history time { margin-right: .5rem; font-size: .9rem; }
.history .dl { padding: .2rem .6rem; font-size: .85rem; }
.viewer { display: grid; grid-template-columns: minmax(0, 1fr); gap: 1rem; margin-top: 1.25rem; }
.viewer img, .viewer canvas, .pieces img { display: block; width: 100%; height: auto; background: #fff;
      border: 1px solid var(--line); border-radius: 3px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
.pieces { list-style: none; padding: 0; margin: 1.5rem 0 0; display: grid;
          grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: 2rem 1.5rem; }
.pieces li { display: flex; flex-direction: column; gap: .15rem; }
.pieces a { text-decoration: none; display: flex; flex-direction: column; gap: .6rem; }
.pieces .noimg { display: block; aspect-ratio: 1 / 1.414; border: 1px dashed var(--line); border-radius: 3px; }
.pieces .title { font-size: 1.05rem; line-height: 1.3; }
.pieces .credits, .pieces time { font-size: .85rem; }
"""

VIEWER_JS = """\
// Replaces each .viewer's thumbnail with every page of its PDF, drawn by PDF.js.
// Viewers inside a closed <details> wait until it is opened.
// On any failure the thumbnail (a link to the PDF) simply stays.
const base = document.querySelector("script[data-pdfjs]").dataset.pdfjs;
const viewers = [...document.querySelectorAll(".viewer[data-pdf]")];

let pdfjsLoaded;
const loadPdfjs = () => pdfjsLoaded ??= import(base + "pdf.min.mjs").then(pdfjs => {
  pdfjs.GlobalWorkerOptions.workerSrc = base + "pdf.worker.min.mjs";
  return pdfjs;
});

{
  const draw = async (viewer, doc) => {
    const width = viewer.clientWidth;
    const scale = window.devicePixelRatio || 1;
    const canvases = [];
    for (let n = 1; n <= doc.numPages; n++) {
      const page = await doc.getPage(n);
      const vp = page.getViewport({ scale: width / page.getViewport({ scale: 1 }).width * scale });
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(vp.width);
      canvas.height = Math.round(vp.height);
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", `Page ${n} of ${doc.numPages}`);
      await page.render({ canvas, viewport: vp }).promise;
      canvases.push(canvas);
    }
    viewer.replaceChildren(...canvases);
    return width;
  };

  const show = async viewer => {
    try {
      const pdfjs = await loadPdfjs();
      const doc = await pdfjs.getDocument({ url: new URL(viewer.dataset.pdf, location.href).href }).promise;
      let drawnWidth = await draw(viewer, doc);
      // Redraw when the viewer gets wider (e.g. phone rotated), so pages stay sharp
      let timer;
      new ResizeObserver(() => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          if (viewer.clientWidth > drawnWidth * 1.1) drawnWidth = await draw(viewer, doc);
        }, 300);
      }).observe(viewer);
    } catch (err) {
      console.warn("Could not render", viewer.dataset.pdf, err);
    }
  };

  for (const viewer of viewers) {
    const details = viewer.closest("details");
    if (details && !details.open) details.addEventListener("toggle", () => show(viewer), { once: true });
    else await show(viewer);
  }
}
"""


def build(scores_dir: Path, out_dir: Path, site_title: str) -> None:
    folders = sorted(p for p in scores_dir.iterdir() if p.is_dir())
    pieces = []
    for folder in folders:
        if not (folder / "meta.toml").is_file():
            print(f"warning: skipping {show(folder)} (no meta.toml)", file=sys.stderr)
            continue
        if folder.name != folder.name.lower() or slugify(folder.name) != folder.name:
            raise BuildError(f"{show(folder)}: folder name must be lowercase a-z, 0-9 and '-' (it becomes the URL)")
        pieces.append(load_piece(folder))
    pieces.sort(key=lambda p: slugify(p.title))

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    (out_dir / "style.css").write_text(CSS)
    (out_dir / "viewer.js").write_text(VIEWER_JS)
    (out_dir / "index.html").write_text(render_index(pieces, site_title))
    (out_dir / "404.html").write_text(render_404())
    for piece in pieces:
        piece_dir = out_dir / piece.slug
        for arr in piece.arrangements:
            for version in arr.versions:
                for ftype, out in version.outputs.items():
                    dest = piece_dir / out
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if ftype == "pdf":
                        publish_pdf(version.files[ftype], dest, version.qr, piece.slug)
                    else:
                        shutil.copy2(version.files[ftype], dest)
                    version.sizes[ftype] = dest.stat().st_size
                if version.thumbnail and not render_thumbnail(piece_dir / version.outputs["pdf"], piece_dir / version.thumbnail):
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
