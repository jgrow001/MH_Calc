# MH_Calc — Mistfall Hunters build calculator

Given a class and a set of target affixes + desired stack levels (e.g. Valor 7,
Elusive 5), find every feasible combination of gear pieces (one per slot) and
gems (socketed into that gear) that reaches those targets, plus whatever
bonus affixes come along with each option.

## Status

- [x] Flight-data parser for MistfallDB's embedded object format (`scraper/flight_parse.py`)
- [x] Sitemap crawler + HTML cache (`scraper/fetch.py`)
- [x] Field-mapped extractors for affixes / gems / gear (`scraper/parse_*.py`) — full crawl done: 44 affixes, 320 gems, 464 gear items (374 armor + 90 weapons)
- [x] Typed data model (`model/entities.py`)
- [x] Pruned-DFS feasibility/enumeration solver (`solver/build_solver.py`), 5 unit tests passing
- [x] Streamlit UI (`app.py`), smoke-tested against the full dataset
- [ ] **Socket-shape data — not scrapable from MistfallDB, confirmed by direct inspection.** Sockets are a fixed property of each base gear item; `data/processed/sockets_ruleset.csv` has all 464 items with empty `socket_count`/`socket_shapes` columns, 0 filled in so far. **The solver returns no builds until this is filled in** for at least the class/slot combos you care about (start with Sorcerer helmets). It excludes any item with unknown sockets rather than guessing.
- [ ] Per-affix stack caps — also not published by MistfallDB (its "Unlocks at" field is a different mechanic, a single roll's 1-32 level breakpoint, not a stacking cap). Add known caps to `data/processed/affix_caps_override.json` (currently just `valor: 7, elusive: 5`) and rerun `scraper/parse_affixes.py`.

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
python scraper/fetch.py --section affixes               # cache all /affixes/* pages (also: gems, armor, weapons)
python scraper/parse_affixes.py                         # -> data/processed/affixes.json
python scraper/parse_gems.py                            # -> data/processed/gems.json
python scraper/parse_gear.py                             # -> data/processed/gear.json + sockets_ruleset.csv template
```

## Filling in socket data

Open `data/processed/sockets_ruleset.csv`. For each item you care about, fill in:
- `socket_count`: how many gem sockets it has
- `socket_shapes`: comma-separated shapes, e.g. `purple_rhomb,purple_rhomb` or `agate` — must
  match a gem shape (moonstone/peridot/agate/onyx/amethyst/purple_rhomb) or `universal`

No rerun needed — `model/entities.py` reads the CSV directly at load time.

## Running the app

```
streamlit run app.py
```

## Running tests

```
python tests/test_solver.py
```
