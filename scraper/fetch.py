"""
Polite crawler for mistfalldb.com. Pulls the sitemap, filters it down to the
page types we care about, and caches raw HTML to data/raw/ so the rest of
the pipeline never has to hit the live site again.

robots.txt for mistfalldb.com is `Allow: /` for all user-agents (checked
2026-08-05), so a rate-limited crawl is fine.

Usage:
    python scraper/fetch.py --list-sitemap          # just show URL counts by section
    python scraper/fetch.py --section affixes
    python scraper/fetch.py --section gems
    python scraper/fetch.py --section armor
    python scraper/fetch.py --section weapons
    python scraper/fetch.py --url https://mistfalldb.com/affixes/valor
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

import requests

BASE = "https://mistfalldb.com"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
USER_AGENT = "Mozilla/5.0 (compatible; MH-Calc-research/0.1; personal build-planning tool)"
REQUEST_DELAY_S = 0.75

SECTION_PREFIXES = {
    "affixes": "/affixes/",
    "gems": "/gems/",
    "armor": "/armor/",
    "weapons": "/weapons/",
    "classes": "/classes/",
}


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp


def fetch_sitemap_urls() -> list[str]:
    resp = _get(f"{BASE}/sitemap.xml")
    root = ElementTree.fromstring(resp.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [loc.text.strip() for loc in root.findall(".//sm:loc", ns) if loc.text]


def _url_to_cache_path(url: str) -> Path:
    slug = re.sub(r"^https?://[^/]+/?", "", url).strip("/") or "index"
    slug = slug.replace("/", "__")
    return RAW_DIR / f"{slug}.html"


def fetch_and_cache(url: str, force: bool = False) -> Path:
    path = _url_to_cache_path(url)
    if path.exists() and not force:
        return path
    resp = _get(url)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    time.sleep(REQUEST_DELAY_S)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-sitemap", action="store_true")
    ap.add_argument("--section", choices=sorted(SECTION_PREFIXES))
    ap.add_argument("--url", help="fetch a single URL")
    ap.add_argument("--force", action="store_true", help="refetch even if cached")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    if args.url:
        path = fetch_and_cache(args.url, force=args.force)
        print(f"cached -> {path}")
        return

    urls = fetch_sitemap_urls()

    if args.list_sitemap:
        counts = Counter()
        for u in urls:
            path = re.sub(r"^https?://[^/]+", "", u)
            top = "/" + path.strip("/").split("/")[0] if path.strip("/") else "/"
            counts[top] += 1
        for section, n in counts.most_common():
            print(f"{n:5d}  {section}")
        print(f"{len(urls):5d}  TOTAL")
        return

    if args.section:
        prefix = SECTION_PREFIXES[args.section]
        targets = [u for u in urls if re.sub(r"^https?://[^/]+", "", u).startswith(prefix)]
        if args.limit:
            targets = targets[: args.limit]
        print(f"fetching {len(targets)} URLs for section {args.section!r}", file=sys.stderr)
        for i, u in enumerate(targets, 1):
            path = fetch_and_cache(u, force=args.force)
            print(f"[{i}/{len(targets)}] {u} -> {path.name}", file=sys.stderr)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
