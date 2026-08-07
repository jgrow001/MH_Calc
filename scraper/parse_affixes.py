"""
Build data/processed/affixes.json from the cached affixes list page
(data/raw/affixes.html) plus, if present, per-affix detail pages
(data/raw/affixes__<slug>.html) for extra stats.

IMPORTANT: the per-affix stack cap (e.g. Valor caps at 7 stacks across
gear+gems) is NOT exposed anywhere on MistfallDB -- confirmed by direct
inspection on 2026-08-05. MistfallDB's "Unlocks at" filter refers to a
single item's roll-level breakpoint (1-32 scale), a different mechanic.
`stack_cap` comes from data/processed/affix_caps_override.json (generated
by parse_caps.py from the user's Caps.txt).

Confirmed 2026-08-07: any affix NOT in Caps.txt isn't implemented in the
game yet, so affixes with no stack_cap are dropped entirely here rather
than kept with stack_cap=null. Run parse_caps.py first (needs an existing
affixes.json to resolve names -> slugs -- if bootstrapping from scratch,
run parse_affixes.py once unfiltered first, then parse_caps.py, then this
again to apply the filter).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from html_utils import dt_dd_pairs, meta_description

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

CARD_RE = re.compile(
    r'<a href="/affixes/([a-z0-9-]+)" class="group flex h-full[^"]*">.*?'
    r'<h3[^>]*>([^<]+)<span[^>]*title="([A-Za-z]+) affix',
    re.DOTALL,
)


def load_caps_override() -> dict[str, int]:
    path = OUT_DIR / "affix_caps_override.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def main() -> None:
    list_html = (RAW_DIR / "affixes.html").read_text(encoding="utf-8", errors="ignore")
    caps = load_caps_override()

    affixes = []
    skipped_unimplemented = []
    for slug, name, category in CARD_RE.findall(list_html):
        cap = caps.get(slug)
        if cap is None:
            skipped_unimplemented.append(name.strip())
            continue
        entry = {
            "slug": slug,
            "name": name.strip(),
            "category": category,
            "description": None,
            "gear_count": None,
            "stack_cap": cap,
        }
        detail_path = RAW_DIR / f"affixes__{slug}.html"
        if detail_path.exists():
            detail_html = detail_path.read_text(encoding="utf-8", errors="ignore")
            entry["description"] = meta_description(detail_html)
            stats = dt_dd_pairs(detail_html)
            entry["gear_count"] = stats.get("Gear that rolls it")
        affixes.append(entry)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "affixes.json"
    out_path.write_text(json.dumps(affixes, indent=2), encoding="utf-8")
    print(f"wrote {len(affixes)} affixes -> {out_path}")
    if skipped_unimplemented:
        print(
            f"skipped {len(skipped_unimplemented)} affix(es) not in Caps.txt "
            f"(not yet implemented in-game): {skipped_unimplemented}"
        )
    if not caps:
        print(
            "WARNING: no affix_caps_override.json found -- this would drop ALL affixes. "
            "Run without filtering first (temporarily comment out the cap check), then "
            "scraper/parse_caps.py, then this script again."
        )


if __name__ == "__main__":
    main()
