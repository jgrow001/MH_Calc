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

A line consisting of just "(Category Name)" (e.g. "(Dagger)", "(Dual Blade)")
tags every subsequent weapon item as belonging to that category, until the
next such line or a blank line (which resets to no category) -- for classes
like Shadowstrix where multiple weapon *types* fill the same Weapon slot but
aren't interchangeable in-game (confirmed 2026-08-09: only one of the two
equipped weapons actually grants its affixes, so a build has to pick one
type, not mix-and-match). Written to data/processed/weapon_types.json
(base_slug -> category), merged with any existing content.

Usage:
    python scraper/ingest_bulk_shapes.py data/processed/sorc.txt
    python scraper/ingest_bulk_shapes.py data/processed/sorc.txt --class Sorcerer
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_variant_shapes import resolve_shape_token  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
SOCKETS_PATH = OUT_DIR / "variant_sockets.csv"
WEAPON_TYPES_PATH = OUT_DIR / "weapon_types.json"

FUZZY_MATCH_THRESHOLD = 0.82
CATEGORY_HEADER_RE = re.compile(r"^\((.+)\)$")


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


MAX_AFFIX_WORDS = 3  # longest real affix name is 2 words (e.g. "Iron Helmet"), +1 margin


def _resolve_affix_prefix(tokens: list[str], affix_names: set[str]) -> tuple[str | None, int, bool]:
    """Try to match a (possibly multi-word, possibly hyphenated) affix name
    at the start of tokens -- some dumps write "iron-helmet" as one token,
    others "Iron Helmet" as two. Returns (affix_name, tokens_consumed,
    was_fuzzy); (None, 0, False) if nothing matched well enough."""
    n_max = min(MAX_AFFIX_WORDS, len(tokens))
    for n in range(n_max, 0, -1):
        candidate = " ".join(tokens[:n]).replace("-", " ")
        if candidate.lower() in affix_names:
            return candidate, n, False

    best_name, best_n, best_ratio = None, 0, 0.0
    for n in range(n_max, 0, -1):
        candidate = " ".join(tokens[:n]).replace("-", " ")
        for name in affix_names:
            ratio = difflib.SequenceMatcher(None, candidate.lower(), name).ratio()
            if ratio > best_ratio:
                best_name, best_n, best_ratio = name, n, ratio  # canonical matched name, not the typo'd text
    if best_ratio >= FUZZY_MATCH_THRESHOLD:
        return best_name, best_n, True
    return None, 0, False


def parse_line(line: str, affix_names: set[str]) -> tuple[str | None, list[str], bool]:
    """Given the remainder of a line after the base item name is stripped,
    return (affix_name_or_None, [canonical_shape_token, ...], affix_was_fuzzy).
    Standalone "-" separator tokens (some dumps write "Item - Affix shape",
    others "Item Affix shape") are dropped before matching."""
    tokens = [t for t in line.split() if t != "-"]
    if not tokens:
        return None, [], False
    affix_name, consumed, was_fuzzy = _resolve_affix_prefix(tokens, affix_names)
    rest = tokens[consumed:] if affix_name is not None else tokens
    shapes = []
    for t in rest:
        resolved = resolve_shape_token(t)
        shapes.append(resolved if resolved else f"?{t}")
    return affix_name, shapes, was_fuzzy


def resolve_affix_cell(text: str, affix_names: set[str]) -> tuple[str | None, bool]:
    """Exact-or-fuzzy match a single cell/token against known affix names --
    for formats (like CSV) that keep the whole affix name in one field, so
    no multi-word prefix search is needed. Returns (canonical_name, was_fuzzy)."""
    normalized = text.replace("-", " ").strip()
    if normalized.lower() in affix_names:
        return normalized, False
    best_name, best_ratio = None, 0.0
    for name in affix_names:
        ratio = difflib.SequenceMatcher(None, normalized.lower(), name).ratio()
        if ratio > best_ratio:
            best_name, best_ratio = name, ratio
    if best_ratio >= FUZZY_MATCH_THRESHOLD:
        return best_name, True
    return None, False


def load_sockets_rows() -> tuple[list[dict], list[str]]:
    with SOCKETS_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames)


def write_sockets_rows(rows: list[dict], fieldnames: list[str]) -> None:
    with SOCKETS_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_weapon_types() -> dict[str, str]:
    if WEAPON_TYPES_PATH.exists():
        return json.loads(WEAPON_TYPES_PATH.read_text(encoding="utf-8"))
    return {}


def save_weapon_types(weapon_types: dict[str, str]) -> None:
    if weapon_types:
        WEAPON_TYPES_PATH.write_text(json.dumps(weapon_types, indent=2, sort_keys=True), encoding="utf-8")
        print(f"weapon_types.json: {len(weapon_types)} weapon(s) categorized -> {WEAPON_TYPES_PATH}")


def apply_entry(
    rows_by_base_name: dict[str, list[dict]],
    base_name: str,
    affix_name: str | None,
    shapes: list[str],
    entry_repr: str,
) -> bool:
    """Match one parsed (affix, shapes) entry to an un-filled variant row
    for base_name and write its socket_shapes. Prints its own NOTE/WARNING/
    CONFLICT diagnostics. Returns True iff it counted as newly matched --
    caller is responsible for calling write_sockets_rows() afterwards."""
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
                print(f"NOTE: duplicate entry, already recorded and consistent: {entry_repr}")
            elif already:
                print(
                    f"CONFLICT: {base_name!r} affix {affix_name!r} already recorded as "
                    f"{already['socket_shapes']!r} but this entry says {computed!r} -- {entry_repr}"
                )
            else:
                print(f"WARNING: no un-filled row for {base_name!r} affix {affix_name!r} -- skipped: {entry_repr}")
            return False
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
                print(f"NOTE: duplicate entry, already recorded and consistent: {entry_repr}")
            else:
                print(f"WARNING: no remaining un-filled 'Base roll' row for {base_name!r} -- skipped: {entry_repr}")
            return False

    target["socket_shapes"] = computed
    expected = target.get("expected_socket_count")
    if expected and expected.isdigit() and int(expected) != len(shapes):
        print(
            f"WARNING: {target['variant_slug']} ({target['affix']}) got {len(shapes)} shape(s) "
            f"but expected_socket_count says {expected} -- double check: {entry_repr}"
        )
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--class", dest="class_filter", default=None, help="restrict matching to this class, e.g. Sorcerer")
    args = ap.parse_args()

    rows, fieldnames = load_sockets_rows()

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
    weapon_types = load_weapon_types()
    current_category: str | None = None

    for raw_line in args.file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            current_category = None
            continue
        header = CATEGORY_HEADER_RE.match(line)
        if header:
            current_category = header.group(1).strip()
            continue
        base_name, was_fuzzy = match_base_name(line, names_by_length)
        if base_name and current_category:
            slug = rows_by_base_name[base_name][0]["base_slug"]
            if rows_by_base_name[base_name][0]["slot"] == "Weapon":
                weapon_types[slug] = current_category
        if was_fuzzy:
            print(f"NOTE: fuzzy-matched {line[:len(base_name)+5]!r}... -> {base_name!r}")
        if base_name is None:
            print(f"WARNING: no matching base item name for line: {line!r} -- skipped")
            skipped.append(line)
            continue
        remainder = line[len(base_name):].strip()
        affix_name, shapes, affix_was_fuzzy = parse_line(remainder, affix_names)
        if affix_was_fuzzy:
            print(f"NOTE: fuzzy-matched affix {remainder!r} -> {affix_name!r} in line: {line!r}")

        bad = [s for s in shapes if s.startswith("?")]
        if bad:
            print(f"WARNING: unrecognized shape(s) {bad} in line: {line!r} -- skipped")
            skipped.append(line)
            continue

        if apply_entry(rows_by_base_name, base_name, affix_name, shapes, line):
            matched += 1
        else:
            skipped.append(line)

    write_sockets_rows(rows, fieldnames)
    save_weapon_types(weapon_types)
    print(f"\nmatched {matched} lines, skipped {len(skipped)}")


if __name__ == "__main__":
    main()
