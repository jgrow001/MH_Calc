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
- [x] Pruned-DFS feasibility/enumeration solver (`solver/build_solver.py`), 7 unit tests passing
- [x] Streamlit UI (`app.py`), smoke-tested against the full dataset
- [ ] **Socket-shape data — not scrapable from MistfallDB, confirmed by direct inspection.** Socket shape is a property of the specific gear VARIANT (not the base item — confirmed via Raven Priest Robe, whose 9 variants each carry a different socket-shape combo). `data/processed/variant_sockets.csv` has all 1707 variants with an empty `socket_shapes` column — **321/1707 filled in so far** (all Sorcerer armor+weapon, plus all class-agnostic jewelry). **The solver excludes any variant with unknown sockets rather than guessing.** Socket *count* is not manual, though — see below. Other 5 classes still need their gear filled in.
- [ ] Per-affix stack caps — also not published by MistfallDB (its "Unlocks at" field is a different mechanic, a single roll's 1-32 level breakpoint, not a stacking cap). Add known caps to `data/processed/affix_caps_override.json` (currently just `valor: 7, elusive: 5`) and rerun `scraper/parse_affixes.py`.

## Key mechanics (confirmed 2026-08-05)

- 6 classes: Mercenary, Sorcerer, Blackarrow, Shadowstrix, Seer, Withered Knight
- 8 gear slots: weapon, head, chest, hands, legs, feet, necklace, ring
- Affix stacking is **count-based**: each item/gem that grants an affix contributes
  one stack, capped per-affix (e.g. Valor caps at 7). This is a different
  mechanic from MistfallDB's documented "affix roll level 1-32" scaling, which
  is about a single item's roll quality, not multi-item stacking.
- **5 real socket shapes, confirmed by the user**: amethyst, agate, moonstone,
  peridot, universal. MistfallDB names gems "Onyx" and "Purple Rhomb" but
  these aren't separate shapes — Onyx is always tier 1/single-affix and
  Purple Rhomb is always tier 2/dual-affix, so both are folded into
  `universal` at parse time (see `scraper/parse_gems.py` SHAPE_KEYWORDS).
- **Socket count is derived, not manual**, from rarity + whether the variant
  carries an affix: budget is 3 (Legendary) / 2 (Epic, Excellent) / 1 (Rare),
  minus 1 if the variant has a named affix, 0 if it's a "Base roll." Common/
  Damaged are unmodeled (confirmed not worth it); Holy wasn't covered by the
  rule and needs the same manual treatment as socket shape. See
  `model.entities.expected_socket_count` / `RARITY_SOCKET_BUDGET`.
- **Socket shape is NOT derivable** — it's bespoke per variant (e.g. Raven
  Priest Robe's "Aegis" roll takes a peridot gem, its "Curse" roll takes
  amethyst). Has to be entered by hand, or pasted in bulk — see below.
- **Sockets also have a tier**: a T2 socket accepts T1 or T2 gems; a T1
  socket only accepts T1 gems. Encoded as a digit suffix on the shape token
  (`amethyst2` = T2 amethyst socket); no digit defaults to T1. See
  `SocketSpec.accepts()` in `model/entities.py`.

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
python scraper/parse_gear.py                             # -> data/processed/gear.json + variant_sockets.csv template
```

## Filling in socket shape data

`data/processed/variant_sockets.csv` has one row per gear **variant** (not base item), with an
`expected_socket_count` reference column (derived from rarity, see above) and an empty
`socket_shapes` column to fill in:
- comma-separated shapes with optional tier digit, e.g. `agate,amethyst2` — must match one of the
  5 real shapes (moonstone/peridot/agate/amethyst/universal), no digit = T1
- literal `none` for a confirmed-zero-socket variant (distinct from blank = not yet entered)

No rerun needed — `model/entities.py` reads the CSV directly at load time.

Fast path for bulk entry (`scraper/ingest_variant_shapes.py`), in the compact format ("name,
shape" per line, matched against real affix names to tell fixed-affix lines from Base-roll
lines). Shapes can be given as full names or single-letter shorthand + tier digit, color-coded:
`r`=agate(red) `g`=peridot(green) `b`=moonstone(blue) `p`=amethyst(purple) `w`=universal,
e.g. `r1 g2 b1 p2 w1`. Cross-checks entered shape count against `expected_socket_count` and
warns on mismatch.

```
python scraper/ingest_variant_shapes.py raven-priest-robe <<'EOF'
1. ethereal, agate
2. tenacious, a2
...
EOF
```

For dumps covering many base items at once — one line per variant, item name inferred by
matching against known base names, no per-item command needed — use
`scraper/ingest_bulk_shapes.py` instead. Space-separated, not comma-separated:

```
Moon Deity - Myriad Soul Staff fervor r2 g1
Moon Deity - Myriad Soul Staff r2 b1 g1
Focus Staff fervor
Focus Staff g1
```

```
python scraper/ingest_bulk_shapes.py data/processed/sorc.txt --class Sorcerer
```

`--class` restricts name-matching to that class (recommended when the item names might collide
across classes); omit it for class-agnostic gear like jewelry. Prints `NOTE` for fuzzy-matched
base names (e.g. a typo) and duplicate lines, `CONFLICT` if a repeated line disagrees with what's
already recorded, `WARNING` for anything it couldn't match at all.

## Running the app

```
streamlit run app.py
```

## Running tests

```
python tests/test_solver.py
```
