"""
Bulk version of ingest_variant_shapes.py for dumps covering many base items
in one file, one variant per line, no per-item command needed:

    Moon Deity - Myriad Soul Staff fervor r2 g1
    Moon Deity - Myriad Soul Staff fervid g2 r1
    Moon Deity - Myriad Soul Staff r2 b1 g1
    Focus Staff fervor
    Focus Staff g1

Each line is: "<base item name> [<affix name>] [<shape token> ...]" --
space-separated (not comma-separated), matched against known base item
names (longest prefix match) to figure out where the name ends and the
affix/shapes begin. Affix names with spaces are written hyphenated
("iron-helmet", "sky-piercer") and matched case-insensitively either way.

A line with no affix token (first remaining token isn't a known affix name)
is treated as a "Base roll" (no-affix) variant. A line with an affix and no
shape tokens confirms zero sockets for that variant (written as "none").

Usage:
    python scraper/ingest_bulk_shapes.py data/processed/sorc.txt
    python scraper/ingest_bulk_shapes.py data/processed/sorc.txt --class Sorcerer
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_variant_shapes import resolve_shape_token  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
SOCKETS_PATH = OUT_DIR / "variant_sockets.csv"

FUZZY_MATCH_THRESHOLD = 0.82


def load_affix_names() -> set[str]:
    affixes = json.loads((OUT_DIR / "affixes.json").read_text(encoding="utf-8"))
    return {a["name"].strip().lower() for a in affixes}


def match_base_name(line: str, names_by_length: list[str]) -> tuple[str | None, bool]:
    """Returns (matched_name, was_fuzzy). Exact prefix match first; if none,
    falls back to a same-length-slice fuzzy match (catches typos like
    'Elser' for 'Elder') above FUZZY_MATCH_THRESHOLD."""
    low = line.lower()
    for name in names_by_length:
        if low.startswith(name.lower()):
            return name, False

    best_name, best_ratio = None, 0.0
    for name in names_by_length:
        slice_ = low[: len(name)]
        ratio = difflib.SequenceMatcher(None, slice_, name.lower()).ratio()
        if ratio > best_ratio:
            best_name, best_ratio = name, ratio
    if best_ratio >= FUZZY_MATCH_THRESHOLD:
        return best_name, True
    return None, False


def parse_line(line: str, affix_names: set[str]) -> tuple[str | None, list[str]]:
    """Given the remainder of a line after the base item name is stripped,
    return (affix_name_or_None, [canonical_shape_token, ...])."""
    tokens = line.split()
    if not tokens:
        return None, []
    first_normalized = tokens[0].replace("-", " ").lower()
    if first_normalized in affix_names:
        # keep the hyphen->space normalization so it matches CSV affix names
        # like "Iron Helmet" even though the source writes "iron-helmet"
        affix_name, rest = tokens[0].replace("-", " "), tokens[1:]
    else:
        affix_name, rest = None, tokens
    shapes = []
    for t in rest:
        resolved = resolve_shape_token(t)
        shapes.append(resolved if resolved else f"?{t}")
    return affix_name, shapes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--class", dest="class_filter", default=None, help="restrict matching to this class, e.g. Sorcerer")
    args = ap.parse_args()

    with SOCKETS_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    candidate_rows = rows
    if args.class_filter:
        candidate_rows = [r for r in rows if args.class_filter.lower() in r["class"].lower()]

    names_by_length = sorted({r["base_name"] for r in candidate_rows}, key=len, reverse=True)
    affix_names = load_affix_names()

    rows_by_base_name: dict[str, list[dict]] = {}
    for r in candidate_rows:
        rows_by_base_name.setdefault(r["base_name"], []).append(r)

    matched = 0
    skipped: list[str] = []

    for raw_line in args.file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        base_name, was_fuzzy = match_base_name(line, names_by_length)
        if was_fuzzy:
            print(f"NOTE: fuzzy-matched {line[:len(base_name)+5]!r}... -> {base_name!r}")
        if base_name is None:
            print(f"WARNING: no matching base item name for line: {line!r} -- skipped")
            skipped.append(line)
            continue
        remainder = line[len(base_name):].strip()
        affix_name, shapes = parse_line(remainder, affix_names)

        bad = [s for s in shapes if s.startswith("?")]
        if bad:
            print(f"WARNING: unrecognized shape(s) {bad} in line: {line!r} -- skipped")
            skipped.append(line)
            continue

        computed = ",".join(shapes) if shapes else "none"
        item_rows = rows_by_base_name[base_name]
        if affix_name is not None:
            target = next(
                (r for r in item_rows if r["affix"].lower() == affix_name.lower() and not r["socket_shapes"]),
                None,
            )
            if target is None:
                already = next(
                    (r for r in item_rows if r["affix"].lower() == affix_name.lower() and r["socket_shapes"]),
                    None,
                )
                if already and already["socket_shapes"] == computed:
                    print(f"NOTE: duplicate line, already recorded and consistent: {line!r}")
                elif already:
                    print(
                        f"CONFLICT: {base_name!r} affix {affix_name!r} already recorded as "
                        f"{already['socket_shapes']!r} but this line says {computed!r} -- {line!r}"
                    )
                else:
                    print(f"WARNING: no un-filled row for {base_name!r} affix {affix_name!r} -- skipped: {line!r}")
                skipped.append(line)
                continue
        else:
            target = next(
                (r for r in item_rows if r["affix"] == "Base roll" and not r["socket_shapes"]),
                None,
            )
            if target is None:
                dupe = any(
                    r["affix"] == "Base roll" and r["socket_shapes"] == computed for r in item_rows
                )
                if dupe:
                    print(f"NOTE: duplicate line, already recorded and consistent: {line!r}")
                else:
                    print(f"WARNING: no remaining un-filled 'Base roll' row for {base_name!r} -- skipped: {line!r}")
                skipped.append(line)
                continue

        target["socket_shapes"] = computed
        expected = target.get("expected_socket_count")
        if expected and expected.isdigit() and int(expected) != len(shapes):
            print(
                f"WARNING: {target['variant_slug']} ({target['affix']}) got {len(shapes)} shape(s) "
                f"but expected_socket_count says {expected} -- double check: {line!r}"
            )
        matched += 1

    with SOCKETS_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nmatched {matched} lines, skipped {len(skipped)}")


if __name__ == "__main__":
    main()
