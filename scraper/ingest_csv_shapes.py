"""
CSV version of ingest_bulk_shapes.py -- the preferred format going forward,
since comma-delimited fields remove all the ambiguity space-separated dumps
needed heuristics for (dash separators, multi-word affix names, prefix-
matching item names). One row per variant, up to 4 columns:

    item_name,affix_or_shape,shape,shape

    Insatiable Heart - Soulseeker Bow,Sky Piercer,p2,g1
    Insatiable Heart - Soulseeker Bow,g2,r1,b1
    Oil-soaked Wooden Bow,Sky Piercer,,

- Column 1 is the exact base item name (no prefix-matching needed, though a
  fuzzy fallback still catches typos).
- Column 2 is either a real affix name (named-affix roll) or the first
  socket shape (a "Base roll" variant, no affix) -- same rule as
  ingest_bulk_shapes.py, matched via resolve_affix_cell().
- Remaining non-empty columns are shape tokens (full name or letter
  shorthand, see ingest_variant_shapes.py). Empty trailing cells are fine
  (fewer sockets) -- including an entirely-empty remainder for a confirmed
  zero-socket variant.

A row consisting of just a "(Category Name)" first cell (rest empty) tags
subsequent weapon items with that category, same as the "(Dagger)" /
"(Dual Blade)" section headers in the space-separated format.

Usage:
    python scraper/ingest_csv_shapes.py data/processed/BA.csv --class Blackarrow
"""
from __future__ import annotations

import argparse
import csv
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_variant_shapes import resolve_shape_token  # noqa: E402
from ingest_bulk_shapes import (  # noqa: E402
    CATEGORY_HEADER_RE,
    apply_entry,
    load_affix_names,
    load_sockets_rows,
    load_weapon_types,
    resolve_affix_cell,
    save_weapon_types,
    write_sockets_rows,
    FUZZY_MATCH_THRESHOLD,
)


def match_item_name(name: str, names_lower: dict[str, str]) -> tuple[str | None, bool]:
    """Exact-or-fuzzy match against known base item names. No prefix search
    needed -- the CSV's own comma delimiting already isolates the name."""
    key = name.strip().lower()
    if key in names_lower:
        return names_lower[key], False
    best_name, best_ratio = None, 0.0
    for lower, original in names_lower.items():
        ratio = difflib.SequenceMatcher(None, key, lower).ratio()
        if ratio > best_ratio:
            best_name, best_ratio = original, ratio
    if best_ratio >= FUZZY_MATCH_THRESHOLD:
        return best_name, True
    return None, False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--class", dest="class_filter", default=None, help="restrict matching to this class, e.g. Blackarrow")
    args = ap.parse_args()

    rows, fieldnames = load_sockets_rows()

    candidate_rows = rows
    if args.class_filter:
        candidate_rows = [r for r in rows if args.class_filter.lower() in r["class"].lower()]

    names_lower = {r["base_name"].lower(): r["base_name"] for r in candidate_rows}
    affix_names = load_affix_names()

    rows_by_base_name: dict[str, list[dict]] = {}
    for r in candidate_rows:
        rows_by_base_name.setdefault(r["base_name"], []).append(r)

    matched = 0
    skipped: list[str] = []
    weapon_types = load_weapon_types()
    current_category: str | None = None

    with args.file.open(newline="", encoding="utf-8") as f:
        for raw_row in csv.reader(f):
            cells = [c.strip() for c in raw_row]
            if not cells or not cells[0]:
                current_category = None
                continue
            entry_repr = ",".join(cells)

            header = CATEGORY_HEADER_RE.match(cells[0]) if not any(cells[1:]) else None
            if header:
                current_category = header.group(1).strip()
                continue

            item_name_raw = cells[0]
            rest = [c for c in cells[1:] if c]

            base_name, was_fuzzy = match_item_name(item_name_raw, names_lower)
            if base_name and current_category:
                slug = rows_by_base_name[base_name][0]["base_slug"]
                if rows_by_base_name[base_name][0]["slot"] == "Weapon":
                    weapon_types[slug] = current_category
            if was_fuzzy:
                print(f"NOTE: fuzzy-matched item {item_name_raw!r} -> {base_name!r}")
            if base_name is None:
                print(f"WARNING: no matching base item name for row: {entry_repr!r} -- skipped")
                skipped.append(entry_repr)
                continue

            if not rest:
                print(f"WARNING: no affix/shape data in row: {entry_repr!r} -- skipped")
                skipped.append(entry_repr)
                continue

            affix_name, was_affix_fuzzy = resolve_affix_cell(rest[0], affix_names)
            if affix_name is not None:
                shape_cells = rest[1:]
                if was_affix_fuzzy:
                    print(f"NOTE: fuzzy-matched affix {rest[0]!r} -> {affix_name!r} in row: {entry_repr!r}")
            else:
                shape_cells = rest

            shapes, bad = [], []
            for cell in shape_cells:
                resolved = resolve_shape_token(cell)
                (shapes if resolved else bad).append(resolved or cell)
            if bad:
                print(f"WARNING: unrecognized shape(s) {bad} in row: {entry_repr!r} -- skipped")
                skipped.append(entry_repr)
                continue

            if apply_entry(rows_by_base_name, base_name, affix_name, shapes, entry_repr):
                matched += 1
            else:
                skipped.append(entry_repr)

    write_sockets_rows(rows, fieldnames)
    save_weapon_types(weapon_types)
    print(f"\nmatched {matched} rows, skipped {len(skipped)}")


if __name__ == "__main__":
    main()
