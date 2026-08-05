"""
Build data/processed/gems.json from cached gem detail pages
(data/raw/gems__<slug>.html, fetched via `fetch.py --section gems`).

Field sourcing, confirmed by inspecting real pages on 2026-08-05:
  - name       <h1>
  - affixes    meta description: "<Name> is an affix gem ... that grants X, Y."
  - tier       len(affixes) -- 1 affix = tier 1, 2 affixes = tier 2
  - shape      keyword match against slug (moonstone/peridot/agate/onyx/
               amethyst/rhomb) -- MistfallDB calls this the gem "type" but
               it lines up with the socket-shape concept from the game
  - gem_level, combat_value, tradable  dt/dd stat rows
Socket *count* per gear item is a separate, manually-curated concern (see
sockets_ruleset.csv) -- gems.json only describes the gems themselves.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from html_utils import dt_dd_pairs, h1_title, meta_description

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

GRANTS_RE = re.compile(r"that grants (.+?)\.?\s*$")

SHAPE_KEYWORDS = [
    ("purple rhomb", "purple_rhomb"),
    ("rhomb", "purple_rhomb"),
    ("moonstone", "moonstone"),
    ("peridot", "peridot"),
    ("agate", "agate"),
    ("onyx", "onyx"),
    ("amethyst", "amethyst"),
]


def derive_shape(slug: str, name: str) -> str | None:
    hay = f"{slug} {name}".lower()
    for keyword, shape in SHAPE_KEYWORDS:
        if keyword in hay:
            return shape
    return None


def parse_gem_page(slug: str, page_html: str) -> dict:
    name = h1_title(page_html) or slug
    desc = meta_description(page_html) or ""
    m = GRANTS_RE.search(desc)
    affixes = [a.strip() for a in m.group(1).split(",")] if m else []
    stats = dt_dd_pairs(page_html)
    return {
        "slug": slug,
        "name": name,
        "shape": derive_shape(slug, name),
        "tier": len(affixes) if affixes else None,
        "affixes": affixes,
        "gem_level": stats.get("Gem level"),
        "combat_value": stats.get("Combat value"),
        "tradable": stats.get("Tradable"),
    }


def main() -> None:
    gems = []
    unmatched_shape = []
    for path in sorted(RAW_DIR.glob("gems__*.html")):
        slug = path.stem[len("gems__") :]
        page_html = path.read_text(encoding="utf-8", errors="ignore")
        gem = parse_gem_page(slug, page_html)
        gems.append(gem)
        if gem["shape"] is None:
            unmatched_shape.append(slug)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "gems.json"
    out_path.write_text(json.dumps(gems, indent=2), encoding="utf-8")
    print(f"wrote {len(gems)} gems -> {out_path}")
    if unmatched_shape:
        print(f"WARNING: {len(unmatched_shape)} gems had no recognizable shape keyword:")
        for s in unmatched_shape[:20]:
            print(f"  {s}")


if __name__ == "__main__":
    main()
