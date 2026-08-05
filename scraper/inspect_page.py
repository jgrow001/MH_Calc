"""
Dev tool: run the flight-registry parser against a saved HTML page and print
every distinct dict "shape" (sorted key set) found, with a count and one
example. Used to reverse-engineer field names for gear/gem/affix objects
before writing the real parse_*.py extractors.

Usage:
    python scraper/inspect_page.py data/raw/some_page.html
    python scraper/inspect_page.py data/raw/some_page.html --key slug   # only shapes containing this key
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from flight_parse import parse_flight_registry, iter_dicts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("html_file", type=Path)
    ap.add_argument("--key", help="only show shapes whose keys include this")
    ap.add_argument("--limit", type=int, default=40, help="max shapes to print")
    args = ap.parse_args()

    html = args.html_file.read_text(encoding="utf-8", errors="ignore")
    registry = parse_flight_registry(html)
    print(f"parsed {len(registry)} top-level $R entries", file=sys.stderr)

    shapes: dict[tuple, dict] = {}
    counts: dict[tuple, int] = defaultdict(int)
    for d in iter_dicts(registry):
        shape = tuple(sorted(d.keys()))
        if args.key and args.key not in shape:
            continue
        counts[shape] += 1
        shapes.setdefault(shape, d)

    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    for shape, count in ordered[: args.limit]:
        print(f"\n--- shape ({count}x): {shape}")
        print(json.dumps(shapes[shape], indent=2)[:800])


if __name__ == "__main__":
    main()
