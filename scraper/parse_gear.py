"""
Build data/processed/gear.json from cached armor/weapon base-item pages
(data/raw/armor__<slug>.html, data/raw/weapons__<slug>.html -- base items
only, no numeric-suffixed variant pages; MistfallDB's sitemap doesn't index
variants separately, they only exist as links on the base item's page).

Each base item's "drops with different affix rolls" section lists its
variants (slug, affix, combat value) directly on the base page, so no
per-variant fetch is needed.

Also emits data/processed/sockets_ruleset.csv: one row per base item with
empty socket_count / socket_shapes columns. Socket data isn't present
anywhere on MistfallDB (confirmed 2026-08-05) -- it's a fixed property of
each physical item that has to be filled in by hand, incrementally, only
for the class/slot combos you actually care about.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from html_utils import dt_dd_pairs, h1_title

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

    sockets_path = OUT_DIR / "sockets_ruleset.csv"
    existing_slugs: set[str] = set()
    if sockets_path.exists():
        with sockets_path.open(newline="", encoding="utf-8") as f:
            existing_slugs = {row["slug"] for row in csv.DictReader(f)}

    fieldnames = [
        "slug", "name", "kind", "class", "slot", "rarity",
        "num_variants", "socket_count", "socket_shapes",
    ]
    rows = []
    if sockets_path.exists():
        with sockets_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    new_rows = 0
    for item in items:
        if item["slug"] in existing_slugs:
            continue
        rows.append({
            "slug": item["slug"],
            "name": item["name"],
            "kind": item["kind"],
            "class": item["class"] or "",
            "slot": item["slot"] or "",
            "rarity": item["rarity"] or "",
            "num_variants": len(item["variants"]),
            "socket_count": "",
            "socket_shapes": "",
        })
        new_rows += 1

    with sockets_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"sockets_ruleset.csv: {new_rows} new rows added, "
        f"{len(rows)} total ({sum(1 for r in rows if r['socket_count'])} filled in) -> {sockets_path}"
    )


if __name__ == "__main__":
    main()
