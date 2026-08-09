# MH_Calc — Mistfall Hunters build calculator

Given a class and a set of target affixes + desired stack levels (e.g. Valor 7,
Elusive 5), find every feasible combination of gear pieces (one per slot),
gems (socketed into that gear), and beverage that reaches those targets, plus
whatever bonus affixes come along with each option.

## Status

- [x] Flight-data parser for MistfallDB's embedded object format (`scraper/flight_parse.py`)
- [x] Sitemap crawler + HTML cache (`scraper/fetch.py`)
- [x] Field-mapped extractors for affixes / gems / gear (`scraper/parse_*.py`) — full crawl done: 44 affixes, 320 gems, 464 gear items (374 armor + 90 weapons)
- [x] Typed data model (`model/entities.py`)
- [x] CP-SAT feasibility/enumeration solver (`solver/build_solver.py`, OR-Tools), 19 unit tests passing
- [x] Enumerated builds are deduped by base item, not by roll/variant or gem arrangement — see below
- [x] Beverages — second, independent affix source, see below
- [x] Gems are crafted from a shape's affix pool, not a fixed catalog — see below
- [x] Hard per-affix stack caps enforced in the solver (gear+gems+beverage can't exceed the real cap)
- [x] Lock specific gear into a slot (`locked_items`) — for items with the same socket layout but different base stats, e.g. jewelry
- [x] Weapon category filter (`weapon_type`) — for classes with two mutually-exclusive weapon types filling one slot, see below
- [x] Streamlit UI (`app.py`), smoke-tested against the full dataset
- [x] Per-affix stack caps — 32/44 affixes, from the user's `data/processed/Caps.txt` (see `scraper/parse_caps.py`); the other 12 aren't implemented in the game yet and are dropped entirely from `affixes.json`
- [ ] **Socket-shape data — not scrapable from MistfallDB, confirmed by direct inspection.** Socket shape is a property of the specific gear VARIANT (not the base item — confirmed via Raven Priest Robe, whose 9 variants each carry a different socket-shape combo). `data/processed/variant_sockets.csv` has all 1707 variants with an empty `socket_shapes` column — **761/1707 filled in so far** (Sorcerer + Shadowstrix + Blackarrow armor+weapon, plus all class-agnostic jewelry). **The solver excludes any variant with unknown sockets rather than guessing.** Socket *count* is not manual, though — see below. Other 3 classes still need their gear filled in.

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
- **Beverages** are a second, independent affix source on top of gear+gems —
  exactly one active at a time, confirmed by the user. Each tier grants a
  points budget spread freely across affixes (no shape/type restriction), with
  a per-affix cap: T1 (budget 2, ≤1/affix), T2 (budget 4, ≤1/affix), T3
  (budget 6, ≤2/affix), T4 (budget 8, ≤2/affix). See `BEVERAGE_TIERS` in
  `model/entities.py`. It's a UI selector (None/T1-T4), not auto-assumed-best,
  since lower tiers may be cheaper/easier to get in-game.
- **Gems are crafted, not a fixed catalog** (confirmed 2026-08-08). MistfallDB's
  ~320 scraped named gems turned out to be only a *sample* of combinations —
  a real in-game build needed a T1 agate "Sky Piercer" gem with no match
  anywhere in the scrape. The real mechanic: each of the 4 real gem shapes
  (agate/amethyst/moonstone/peridot) has a fixed pool of ~12 affixes it can
  grant (`data/processed/gems_raw.json`, user-provided); a T1 gem lets you
  pick any ONE from the pool, a T2 gem any TWO *different* ones (never the
  same affix twice). Universal is socket-only — a universal socket accepts a
  gem crafted in any of the 4 real shapes, there's no separate universal
  pool. See `GameData.gem_pools` and `solver.build_solver._craftable_gems_for_socket`.
  The scraped catalog (`gems.json`) is only consulted to reuse a real gem's
  name when one happens to match, for nicer display.
- **Some classes have two mutually-exclusive weapon categories** filling the
  same Weapon slot (confirmed 2026-08-09, Shadowstrix: Dagger vs Dual Blade)
  — both are equippable, but only one actually grants its affixes, so a
  build has to commit to one type rather than mixing candidates from both.
  Captured as `GearItem.weapon_type`, sourced from `(Category)` section
  headers in bulk dumps like `shadowstrix.txt` (see `scraper/ingest_bulk_shapes.py`)
  and written to `data/processed/weapon_types.json`. `find_builds(...,
  weapon_type="Dagger")` restricts the Weapon slot to that category; the app
  shows a selector automatically when a class has more than one.

## Solver engine

`solver/build_solver.py` builds a CP-SAT model (OR-Tools) — one boolean variable per candidate
gear+gem combo per slot, exactly-one constraint per slot, a linear `>=` constraint per target
affix, beverage encoded as bounded integer variables — and enumerates feasible solutions by
solving, then blocking the found gear combination and re-solving. This replaced an earlier
hand-rolled pruned-DFS design: with several affix targets at once a *tight* target (every affix
landing exactly on target, beverage budget fully used, zero slack anywhere) defeated every DFS
pruning/ordering heuristic tried — confirmed on a real 7-affix in-game build that took 8+ seconds
and millions of backtracking nodes to rediscover even with best-first candidate ordering. That's
exactly the class of problem (near-equality constraints across many dimensions) a real constraint
solver is built for; CP-SAT finds the same query in well under a second, correct by construction.
A per-solve time limit plus an overall wall-clock budget (`max_time_seconds` /
`overall_time_budget_seconds`, defaults 8s / 15s) bound total search time regardless of how many
affixes are targeted at once.

**Slots that could contribute to a target are optional, not mandatory** (fixed 2026-08-08). Every
slot touching a target affix gets an explicit "skip" boolean alongside its real candidates, chosen
via `AddExactlyOne(candidates + [skip])`. Without this, a single narrow target (e.g. just Curse)
forced *every* slot capable of granting it to participate — and once the hard stack-cap constraint
was added, that mandatory participation could force a total above the affix's own cap (8 slots each
forced to contribute Curse, cap 5 → guaranteed infeasible), breaking even trivially-achievable
single-affix queries. Locked slots (see `locked_items`) are the one exception — no skip there, since
locking means the item must appear.

**Enumerated builds are unique by base item, not by roll or gem arrangement** (fixed 2026-08-09).
When re-solving to find the next build, the blocking constraint used to key on the exact candidate
chosen per slot — which includes the specific variant (affix roll) *and* the specific gem
arrangement. In practice this meant a locked item could still show up several times in a row, once
per drop variant (jewelry rolls differ only by socket shape, not by anything meaningful to show
separately), and items with slack sockets could repeat with just a different "extra" gem in the
leftover slot. The blocking constraint now sums each slot's candidates by *base item* (`pick.item.slug`)
before blocking, so the next solve is forced to use a different item somewhere rather than just a
different roll or leftover-socket filler of the same one.

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

**Preferred format going forward: CSV**, via `scraper/ingest_csv_shapes.py` — comma delimiting
removes the ambiguity the space-separated format needed heuristics for (dash separators,
multi-word affix names, prefix-matching item names), so it's the most reliable to fill in by hand.
One row per variant, up to 4 columns (`item_name,affix_or_shape,shape,shape`), empty trailing
cells are fine:

```
Insatiable Heart - Soulseeker Bow,Sky Piercer,p2,g1
Insatiable Heart - Soulseeker Bow,g2,r1,b1
Oil-soaked Wooden Bow,Sky Piercer,,
```

```
python scraper/ingest_csv_shapes.py data/processed/BA.csv --class Blackarrow
```

Same `(Category Name)` row convention for weapon types (a row with just that in column 1, rest
empty). Shares its matching/dedup/conflict logic with `ingest_bulk_shapes.py` (see `apply_entry`
et al. in that module) — only the per-row tokenizing differs between the two formats.

## Locking specific gear into a slot

Useful for items with identical socket layouts but different base stats (jewelry especially — see
`data/processed/jewelry.txt`). In the app, the "Lock specific gear" sidebar section has a
per-slot dropdown. Programmatically, pass `locked_items` to `find_builds`:

```python
find_builds(data, "Sorcerer", targets, locked_items={"Necklace": "raven-war-pendant", "Ring": "eye-of-the-sea-giant"})
```

The solver still picks which variant/gems for that item — locking only fixes *which base item*
fills the slot. Locking to an item with no usable socket data (see coverage above) returns no
builds rather than silently ignoring the lock.

## Running the app

```
streamlit run app.py
```

## Running tests

```
python tests/test_solver.py
```
