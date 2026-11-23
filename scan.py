#!/usr/bin/env python3
"""Update scores/*/meta.toml after saving new scores.

- Folder without meta.toml: creates one from the files in it.
- A listed file that changed since the last commit (a new export over "<title>.pdf"):
  the committed copy moves to old/ and a new version, dated by the file's
  modification time, is added. Saving again on the same day just updates the file.
- New file names in a folder that has a meta.toml: added as a new arrangement.

Edits meta.toml as text, so comments are kept. Review with `git diff` before committing.
"""

import argparse
import json
import subprocess
import sys
import tomllib
import unicodedata
from datetime import date
from pathlib import Path

from build import FILE_TYPES, ROOT, slugify

SUFFIXES = {f".{t}": t for t in FILE_TYPES}


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def git(*args: str, binary: bool = False):
    result = subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True)
    return result.stdout if binary else result.stdout.decode()


def rel(path: Path) -> str:
    return nfc(path.relative_to(ROOT).as_posix())


def changed_since_commit() -> set[str]:
    """Tracked files under scores/ whose content differs from HEAD (staged or not)."""
    out = git("diff", "--name-only", "--diff-filter=M", "-z", "HEAD", "--", "scores")
    return {nfc(p) for p in out.split("\0") if p}


def head_blob(path: Path) -> str:
    return git("rev-parse", f"HEAD:{rel(path)}").strip()


def mtime_date(paths) -> date:
    return date.fromtimestamp(max(p.stat().st_mtime for p in paths))


def toml_str(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def score_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)


def group_by_stem(paths) -> dict[str, dict[str, Path]]:
    """{"Ros SSAATB": {"pdf": Path, "dorico": Path}}"""
    groups = {}
    for p in paths:
        groups.setdefault(nfc(p.stem), {})[SUFFIXES[p.suffix.lower()]] = p
    return groups


def version_block(indent: str, when: date, files: dict[str, str]) -> list[str]:
    lines = [f"{indent}[[arrangement.version]]", f"{indent}date = {when}"]
    lines += [f"{indent}{ftype} = {toml_str(files[ftype])}" for ftype in FILE_TYPES if ftype in files]
    return lines + [f'{indent}# notes = ""']


def arrangement_block(arr_id: str, when: date, files: dict[str, str]) -> list[str]:
    head = ["[[arrangement]]"] + ([f"id = {toml_str(arr_id)}"] if arr_id else []) + ['# name = ""', ""]
    return head + version_block("  ", when, files)


def arrangement_id(stem: str, main_stem: str, taken: set[str]) -> str:
    base = slugify(stem).removeprefix(slugify(main_stem)).strip("-") or "alt"
    arr_id, n = base, 2
    while arr_id in taken:
        arr_id, n = f"{base}-{n}", n + 1
    return arr_id


def create_meta(folder: Path, dry_run: bool) -> None:
    groups = group_by_stem(p for p in score_files(folder) if p.parent == folder)
    if not groups:
        return
    stems = sorted(groups, key=len)  # shortest name is most likely the plain title
    title = stems[0]
    lines = [f"title = {toml_str(title)}", '# composer = ""', '# arranger = ""', ""]
    taken = set()
    for i, stem in enumerate(stems):
        arr_id = "" if i == 0 else arrangement_id(stem, title, taken)
        taken.add(arr_id)
        files = {t: nfc(p.name) for t, p in groups[stem].items()}
        lines += arrangement_block(arr_id, mtime_date(groups[stem].values()), files) + [""]
    print(f"{rel(folder)}: new meta.toml with {len(stems)} arrangement(s), title {title!r} (check it)")
    if not dry_run:
        (folder / "meta.toml").write_text("\n".join(lines).rstrip() + "\n")


def find_blocks(lines: list[str]) -> tuple[dict[tuple[int, int], tuple[int, int]], list[int]]:
    """Line ranges of each version block, keyed by (arrangement index, version index)."""
    blocks, arrangement_starts = {}, []
    current, start = None, 0
    for n, line in enumerate(lines + ["[[arrangement]]"]):  # sentinel closes the last block
        header = line.split("#", 1)[0].strip()
        if header in ("[[arrangement]]", "[[arrangement.version]]"):
            if current:
                blocks[current] = (start, n)
                current = None
            if header == "[[arrangement]]":
                arrangement_starts.append(n)
            else:
                i = len(arrangement_starts) - 1
                current = (i, sum(1 for k in blocks if k[0] == i))
                start = n
    return blocks, arrangement_starts[:-1]


def update_meta(folder: Path, changed: set[str], dry_run: bool) -> None:
    meta_path = folder / "meta.toml"
    text = meta_path.read_text()
    meta = tomllib.loads(text)
    lines = text.splitlines()
    blocks, _ = find_blocks(lines)
    arrangements = meta.get("arrangement", [])

    referenced = {}  # NFC path relative to ROOT -> (i, j)
    for i, arr in enumerate(arrangements):
        for j, version in enumerate(arr.get("version", [])):
            for ftype in FILE_TYPES:
                if ftype in version:
                    referenced[rel(folder / version[ftype])] = (i, j)

    edits = []  # (line index, lines to remove, new lines), applied bottom-up
    for i, arr in enumerate(arrangements):
        versions = arr.get("version", [])
        if not versions:
            continue
        j = max(range(len(versions)), key=lambda k: versions[k]["date"])
        current = versions[j]
        files = {t: folder / current[t] for t in FILE_TYPES if t in current}
        modified = {t: p for t, p in files.items() if rel(p) in changed}
        if not modified:
            continue
        label = f"{rel(folder)} ({arr.get('name') or arr.get('id') or 'main'})"
        new_date = mtime_date(modified.values())
        if new_date <= current["date"]:
            print(f"{label}: saved on the same day as the current version ({current['date']}), no new version")
            continue
        # Already archived by an earlier run, with nothing committed since?
        older_blobs = {git("hash-object", "--", str(folder / v[t])).strip()
                       for k, v in enumerate(versions) if k != j for t in FILE_TYPES if t in v}
        if all(head_blob(p) in older_blobs for p in modified.values()):
            continue

        start, end = blocks[(i, j)]
        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        archived = []
        for n in range(start, end):
            try:
                entry = tomllib.loads(lines[n].strip())
            except tomllib.TOMLDecodeError:
                continue
            ftype = next((k for k in entry if k in FILE_TYPES), None)
            if not ftype:
                continue
            src = folder / entry[ftype]
            dest = folder / "old" / f"{src.stem} {current['date']}{src.suffix}"
            if dest.exists():
                sys.exit(f"error: {rel(dest)} already exists")
            archived.append((src, dest, ftype in modified))
            referenced[rel(dest)] = (i, j)
            edits.append((n, 1, [f"{indent}{ftype} = {toml_str(dest.relative_to(folder).as_posix())}"]))
        new_files = {t: nfc(p.relative_to(folder).as_posix()) for t, p in files.items()}
        edits.append((start, 0, version_block(indent, new_date, new_files) + [""]))

        print(f"{label}: new version {new_date}; previous version {current['date']} moved to old/")
        if not dry_run:
            for src, dest, was_modified in archived:
                dest.parent.mkdir(exist_ok=True)
                # A modified file's previous content only exists in git now
                content = git("show", f"HEAD:{rel(src)}", binary=True) if was_modified else src.read_bytes()
                dest.write_bytes(content)

    new_groups = group_by_stem(p for p in score_files(folder) if rel(p) not in referenced)
    if new_groups:
        main_stem = Path(arrangements[0]["version"][0]["pdf"]).stem if arrangements else meta.get("title", "")
        taken = {a.get("id", "") for a in arrangements}
        tail = []
        for stem, group in new_groups.items():
            arr_id = arrangement_id(stem, main_stem, taken)
            taken.add(arr_id)
            files = {t: nfc(p.relative_to(folder).as_posix()) for t, p in group.items()}
            tail += [""] + arrangement_block(arr_id, mtime_date(group.values()), files)
            print(f"{rel(folder)}: new arrangement {arr_id!r} from {stem!r} (check id and name)")
        edits.append((len(lines), 0, tail))

    if edits and not dry_run:
        for n, remove, new in sorted(edits, key=lambda e: e[0], reverse=True):
            lines[n:n + remove] = new
        meta_path.write_text("\n".join(lines).rstrip() + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="only print what would change")
    args = parser.parse_args()
    changed = changed_since_commit()
    for folder in sorted(p for p in (ROOT / "scores").iterdir() if p.is_dir()):
        if folder.name != slugify(folder.name):
            # The folder name is the permanent URL in the QR code, so leave naming to a human
            print(f"{rel(folder)}: skipped, folder name must be a URL slug (e.g. {slugify(folder.name)!r})")
        elif (folder / "meta.toml").is_file():
            update_meta(folder, changed, args.dry_run)
        else:
            create_meta(folder, args.dry_run)


if __name__ == "__main__":
    main()
