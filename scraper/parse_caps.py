"""
Build data/processed/affix_caps_override.json from data/processed/Caps.txt
(user-provided, one "<Affix Name> <cap>" per line, e.g. "Valor 7").

Not scrapable from MistfallDB (its "Unlocks at" field is a different
mechanic, a single roll's 1-32 level breakpoint, not a stacking cap) -- see
parse_affixes.py. Affixes not listed in Caps.txt keep stack_cap=null until
added (some genuinely aren't stackable affixes, e.g. secondary/passive
effects like "Defence Penetration").

Usage:
    python scraper/parse_caps.py          # -> affix_caps_override.json
    python scraper/parse_affixes.py       # picks it up into affixes.json
"""
from __future__ import annotations

import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"


def main() -> None:
    affixes = json.loads((OUT_DIR / "affixes.json").read_text(encoding="utf-8"))
    slug_by_name = {a["name"].strip().lower(): a["slug"] for a in affixes}

    caps: dict[str, int] = {}
    unmatched: list[str] = []
    caps_path = OUT_DIR / "Caps.txt"
    if not caps_path.exists():
        caps_path = OUT_DIR / "caps.txt"  # case-sensitive filesystems, just in case
    for raw_line in caps_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name, _, cap = line.rpartition(" ")
        if not name or not cap.isdigit():
            unmatched.append(line)
            continue
        slug = slug_by_name.get(name.strip().lower())
        if slug is None:
            unmatched.append(line)
            continue
        caps[slug] = int(cap)

    out_path = OUT_DIR / "affix_caps_override.json"
    out_path.write_text(json.dumps(caps, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {len(caps)} affix caps -> {out_path}")
    if unmatched:
        print(f"{len(unmatched)} line(s) in Caps.txt didn't match a known affix:")
        for line in unmatched:
            print(f"  {line!r}")

    no_cap = sorted(a["name"] for a in affixes if a["slug"] not in caps)
    print(f"{len(no_cap)} affixes still have no cap: {no_cap}")


if __name__ == "__main__":
    main()
