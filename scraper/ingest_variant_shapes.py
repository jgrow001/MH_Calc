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

A shape token may have a trailing tier digit, e.g. "amethyst2" for a T2
amethyst socket (a T2 socket accepts T1 or T2 gems, T1 only accepts T1).
No digit = T1.

For speed, shapes can also be given as a single letter + digit (case
insensitive), color-coded per the user's shorthand:
    r = agate (red)   g = peridot (green)   b = moonstone (blue)   p = amethyst (purple)   w = universal
e.g. "r1 g2 b1 p2 w1". Both forms are normalized to the full shape name
before being written to socket_shapes (e.g. "agate,amethyst2").

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

KNOWN_SHAPES = {"moonstone", "peridot", "agate", "amethyst", "universal"}
LETTER_SHAPES = {"r": "agate", "g": "peridot", "b": "moonstone", "p": "amethyst", "w": "universal"}
LINE_PREFIX_RE = re.compile(r"^\s*\d+[.):]?\s*")
SHAPE_TOKEN_RE = re.compile(r"^([a-z_]+?)(\d)?$")


def resolve_shape_token(token: str) -> str | None:
    """Parse a shape token (full name or letter shorthand, either with an
    optional trailing tier digit) into canonical 'shape' or 'shapeN' form.
    Returns None if unrecognized."""
    raw = token.strip().lower().replace(" ", "_").replace("-", "_")
    m = SHAPE_TOKEN_RE.match(raw)
    if not m:
        return None
    base, tier_digit = m.group(1), m.group(2)
    if base in LETTER_SHAPES:
        shape = LETTER_SHAPES[base]
    elif base in KNOWN_SHAPES:
        shape = base
    else:
        return None
    return f"{shape}{tier_digit}" if tier_digit else shape


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
            parsed.append((tokens[0], [resolve_shape_token(t) or f"?{t}" for t in tokens[1:]]))
        else:
            parsed.append((None, [resolve_shape_token(t) or f"?{t}" for t in tokens]))
    return parsed


def _warn_if_count_mismatch(row: dict, shapes: list[str]) -> None:
    expected = row.get("expected_socket_count")
    if expected and expected.isdigit() and int(expected) != len(shapes):
        print(
            f"WARNING: {row['variant_slug']} ({row['affix']}) got {len(shapes)} shape(s) "
            f"but expected_socket_count says {expected} -- double check this one"
        )


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
        bad_shapes = [s for s in shapes if s.startswith("?")]
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
            _warn_if_count_mismatch(target, shapes)
            matched += 1
        else:
            if not unfilled_base_roll_rows:
                print(f"WARNING: no remaining un-filled 'Base roll' row for shapes {shapes} -- skipped")
                unmatched.append((affix_name, shapes))
                continue
            target = unfilled_base_roll_rows.pop(0)
            target["socket_shapes"] = ",".join(shapes) if shapes else "none"
            _warn_if_count_mismatch(target, shapes)
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
