"""
Fast path for entering socket-shape data in the compact format you already
use, e.g. for Raven Priest Robe (Excellent, budget 2):

    1. ethereal, agate
    2. tenacious, amethyst
    3. aegis, peridot
    ...
    7. moonstone, peridot
    9. agate, amethyst

Each line is either:
  - "<affix name>, <shape>[, <shape>...]"  -- a named-affix variant, first
    token matches a real affix (checked against data/processed/affixes.json)
  - "<affix name>" alone, no shapes        -- a named-affix variant confirmed
    to have zero sockets (e.g. Rare-rarity affixed gear, budget 1-1=0)
  - "<shape>, <shape>[, ...]"              -- a "Base roll" variant (no affix),
    first token matches a known gem shape instead

Zero-socket rows are written as the literal "none" (not blank) in
socket_shapes, so the loader can tell "confirmed zero sockets" apart from
"not yet entered" -- see model/entities.py.

Usage:
    python scraper/ingest_variant_shapes.py raven-priest-robe <<'EOF'
    1. ethereal, agate
    2. tenacious, amethyst
    ...
    EOF

Matches named-affix lines to the row with that affix; matches shape-only
lines to the base item's remaining un-filled "Base roll" rows, in order.
Prints anything it couldn't match instead of guessing.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
SOCKETS_PATH = OUT_DIR / "variant_sockets.csv"

KNOWN_SHAPES = {"moonstone", "peridot", "agate", "onyx", "amethyst", "purple_rhomb", "universal"}
LINE_PREFIX_RE = re.compile(r"^\s*\d+[.):]?\s*")


def normalize_shape(token: str) -> str:
    return token.strip().lower().replace(" ", "_").replace("-", "_")


def load_affix_names() -> set[str]:
    affixes = json.loads((OUT_DIR / "affixes.json").read_text(encoding="utf-8"))
    return {a["name"].strip().lower() for a in affixes}


def parse_lines(text: str) -> list[tuple[str | None, list[str]]]:
    """Returns [(affix_name_or_None, [shape, ...]), ...]."""
    affix_names = load_affix_names()
    parsed = []
    for raw_line in text.splitlines():
        line = LINE_PREFIX_RE.sub("", raw_line).strip()
        if not line:
            continue
        tokens = [t.strip() for t in line.split(",") if t.strip()]
        if not tokens:
            continue
        first = tokens[0].lower()
        if first in affix_names:
            parsed.append((tokens[0], [normalize_shape(t) for t in tokens[1:]]))
        else:
            parsed.append((None, [normalize_shape(t) for t in tokens]))
    return parsed


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python scraper/ingest_variant_shapes.py <base_slug>  (paste lines on stdin)")
        sys.exit(1)
    base_slug = sys.argv[1]
    text = sys.stdin.read()
    entries = parse_lines(text)

    with SOCKETS_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    base_rows = [r for r in rows if r["base_slug"] == base_slug]
    if not base_rows:
        print(f"no rows found for base_slug={base_slug!r} in {SOCKETS_PATH}")
        sys.exit(1)

    unfilled_base_roll_rows = [r for r in base_rows if r["affix"] == "Base roll" and not r["socket_shapes"]]
    matched = 0
    unmatched: list[tuple[str | None, list[str]]] = []

    for affix_name, shapes in entries:
        bad_shapes = [s for s in shapes if s not in KNOWN_SHAPES]
        if bad_shapes:
            print(f"WARNING: unrecognized shape(s) {bad_shapes} in line for {affix_name or shapes!r} -- skipped")
            unmatched.append((affix_name, shapes))
            continue
        if affix_name is not None:
            target = next(
                (r for r in base_rows if r["affix"].lower() == affix_name.lower() and not r["socket_shapes"]),
                None,
            )
            if target is None:
                print(f"WARNING: no un-filled row for affix {affix_name!r} on {base_slug} -- skipped")
                unmatched.append((affix_name, shapes))
                continue
            target["socket_shapes"] = ",".join(shapes) if shapes else "none"
            matched += 1
        else:
            if not unfilled_base_roll_rows:
                print(f"WARNING: no remaining un-filled 'Base roll' row for shapes {shapes} -- skipped")
                unmatched.append((affix_name, shapes))
                continue
            target = unfilled_base_roll_rows.pop(0)
            target["socket_shapes"] = ",".join(shapes) if shapes else "none"
            matched += 1

    with SOCKETS_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"matched {matched}/{len(entries)} lines for {base_slug}")
    still_unfilled = [r for r in base_rows if not r["socket_shapes"]]
    if still_unfilled:
        print(f"{len(still_unfilled)} row(s) for {base_slug} still unfilled:")
        for r in still_unfilled:
            print(f"  {r['variant_slug']} ({r['affix']}, expected {r['expected_socket_count']} sockets)")


if __name__ == "__main__":
    main()
