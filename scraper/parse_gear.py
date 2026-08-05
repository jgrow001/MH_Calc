"""
Build data/processed/gear.json from cached armor/weapon base-item pages
(data/raw/armor__<slug>.html, data/raw/weapons__<slug>.html -- base items
only, no numeric-suffixed variant pages; MistfallDB's sitemap doesn't index
variants separately, they only exist as links on the base item's page).

Each base item's "drops with different affix rolls" section lists its
variants (slug, affix, combat value) directly on the base page, so no
per-variant fetch is needed.

Also emits data/processed/variant_sockets.csv: one row per gear VARIANT
(not base item -- confirmed 2026-08-05 that socket shape is a per-variant
property, e.g. Raven Priest Robe's 9 variants each have their own distinct
socket shape(s)) with an empty socket_shapes column to fill in by hand,
plus an expected_socket_count reference column derived from the rarity
budget rule (see model/entities.py) so you can sanity-check how many
shapes to enter.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

from html_utils import dt_dd_pairs, h1_title

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from model.entities import expected_socket_count  # noqa: E402

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

VARIANT_RE = re.compile(
    r'<a href="/(?:armor|weapons)/([a-z0-9-]+)" class="group flex items-center justify-between[^"]*">'
    r'<span[^>]*>([^<]+)</span>'
    r'<span[^>]*>CV (\d+)</span>',
    re.DOTALL,
)

# base-item slugs never end in a long numeric id; variant slugs do
VARIANT_SLUG_RE = re.compile(r"-\d{5,}$")


def parse_gear_page(kind: str, slug: str, page_html: str) -> dict:
    name = h1_title(page_html) or slug
    stats = dt_dd_pairs(page_html)
    variants = [
        {"slug": vslug, "affix": affix.strip(), "combat_value": int(cv)}
        for vslug, affix, cv in VARIANT_RE.findall(page_html)
        if not VARIANT_SLUG_RE.search(slug)  # sanity: only parse from base pages
    ]
    return {
        "slug": slug,
        "kind": kind,  # "armor" | "weapon"
        "name": name,
        "slot": stats.get("Slot") or ("Weapon" if kind == "weapon" else None),
        "rarity": stats.get("Rarity"),
        "class": stats.get("Class"),
        "combat_value": stats.get("Combat value"),
        "durability": stats.get("Durability"),
        "variants": variants,
    }


def main() -> None:
    items = []
    for kind, prefix in (("armor", "armor__"), ("weapon", "weapons__")):
        for path in sorted(RAW_DIR.glob(f"{prefix}*.html")):
            slug = path.stem[len(prefix) :]
            if VARIANT_SLUG_RE.search(slug):
                continue  # shouldn't happen given fetch.py only pulls sitemap urls, but skip defensively
            page_html = path.read_text(encoding="utf-8", errors="ignore")
            items.append(parse_gear_page(kind, slug, page_html))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gear_path = OUT_DIR / "gear.json"
    gear_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    print(f"wrote {len(items)} base gear items -> {gear_path}")

    sockets_path = OUT_DIR / "variant_sockets.csv"
    existing_variant_slugs: set[str] = set()
    rows = []
    if sockets_path.exists():
        with sockets_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            existing_variant_slugs = {row["variant_slug"] for row in rows}

    fieldnames = [
        "variant_slug", "base_slug", "base_name", "kind", "class", "slot", "rarity",
        "affix", "combat_value", "expected_socket_count", "socket_shapes",
    ]
    new_rows = 0
    for item in items:
        for v in item["variants"]:
            if v["slug"] in existing_variant_slugs:
                continue
            has_affix = v["affix"] != "Base roll"
            rows.append({
                "variant_slug": v["slug"],
                "base_slug": item["slug"],
                "base_name": item["name"],
                "kind": item["kind"],
                "class": item["class"] or "",
                "slot": item["slot"] or "",
                "rarity": item["rarity"] or "",
                "affix": v["affix"],
                "combat_value": v["combat_value"],
                "expected_socket_count": expected_socket_count(item["rarity"], has_affix),
                "socket_shapes": "",
            })
            new_rows += 1

    with sockets_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"variant_sockets.csv: {new_rows} new rows added, "
        f"{len(rows)} total ({sum(1 for r in rows if r['socket_shapes'])} filled in) -> {sockets_path}"
    )


if __name__ == "__main__":
    main()
