# MH_Calc — Mistfall Hunters build calculator

Given a class and a set of target affixes + desired stack levels (e.g. Valor 7,
Elusive 5), find every feasible combination of gear pieces (one per slot) and
gems (socketed into that gear) that reaches those targets, plus whatever
bonus affixes come along with each option.

## Status

- [x] Flight-data parser for MistfallDB's embedded object format (`scraper/flight_parse.py`)
- [x] Sitemap crawler + HTML cache (`scraper/fetch.py`)
- [ ] Field-mapped extractors for affixes / gems / gear (`scraper/parse_*.py`) — next step, needs `inspect_page.py` run against cached pages to confirm exact key names
- [ ] Socket-shape data — **not scrapable from MistfallDB, confirmed by direct inspection.** Sockets are a fixed property of each base gear item; will be entered manually into `data/processed/sockets_ruleset.csv`, generated as a template by the extractor and filled in incrementally (start with the classes/slots you care about — doesn't need to be complete to be useful).
- [ ] Typed data model (`model/`)
- [ ] CP-SAT feasibility/enumeration solver (`solver/`)
- [ ] Streamlit UI (`app.py`)

## Key mechanics (confirmed 2026-08-05)

- 6 classes: Mercenary, Sorcerer, Blackarrow, Shadowstrix, Seer, Withered Knight
- 8 gear slots: weapon, head, chest, hands, legs, feet, necklace, ring
- Affix stacking is **count-based**: each item/gem that grants an affix contributes
  one stack, capped per-affix (e.g. Valor caps at 7). This is a different
  mechanic from MistfallDB's documented "affix roll level 1-32" scaling, which
  is about a single item's roll quality, not multi-item stacking.
- Gear pieces come in fixed archetypes per base item: either 2 gem sockets, or
  1 gem socket + 1 fixed/prebuilt affix. Some base items roll random affixes
  (multiple "variant" URLs, e.g. Ace Assassin Boots has an Aegis/Ethereal/
  Seeker/... roll each), others have a hardcoded fixed affix instead of a roll.
- Gem shapes = gem type names on MistfallDB: Moonstone, Peridot, Agate, Onyx,
  Amethyst, Purple Rhomb (+ a universal "slot all" socket). Tier 1 gems grant
  one affix, tier 2 gems grant two.

## Setup

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Scraper usage

```
python scraper/fetch.py --list-sitemap                 # see URL counts by section
python scraper/fetch.py --section affixes               # cache all /affixes/* pages
python scraper/inspect_page.py data/raw/gems.html       # dump dict shapes to find field names
```
