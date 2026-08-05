"""
Build data/processed/affixes.json from the cached affixes list page
(data/raw/affixes.html) plus, if present, per-affix detail pages
(data/raw/affixes__<slug>.html) for extra stats.

IMPORTANT: the per-affix stack cap (e.g. Valor caps at 7 stacks across
gear+gems) is NOT exposed anywhere on MistfallDB -- confirmed by direct
inspection on 2026-08-05. MistfallDB's "Unlocks at" filter refers to a
single item's roll-level breakpoint (1-32 scale), a different mechanic.
`stack_cap` is written as null here and must be filled in manually in
data/processed/affix_caps_override.json, e.g. {"valor": 7, "elusive": 5}.
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
    for slug, name, category in CARD_RE.findall(list_html):
        entry = {
            "slug": slug,
            "name": name.strip(),
            "category": category,
            "description": None,
            "gear_count": None,
            "stack_cap": caps.get(slug),
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
    missing_caps = [a["slug"] for a in affixes if a["stack_cap"] is None]
    print(f"wrote {len(affixes)} affixes -> {out_path}")
    print(
        f"{len(missing_caps)} affixes have no stack_cap yet -- add them to "
        f"data/processed/affix_caps_override.json as {{'slug': cap}} and rerun"
    )


if __name__ == "__main__":
    main()
