"""Typed data model + loader for the scraped MistfallDB data.

Loads data/processed/{affixes,gems,gear}.json + variant_sockets.csv into a
single GameData object the solver and UI both consume. This is the one
place that knows how the raw scrape output is shaped, so parse_*.py can
stay dumb regex extractors.

Socket shape is a property of the specific gear VARIANT, not the base item
-- confirmed 2026-08-05 against a real example (Raven Priest Robe, 9
variants: 7 named-affix rolls each with their own single socket shape, plus
2 "Base roll" variants each with a different pair of socket shapes). It is
not derivable from any general rule and has to be entered by hand per
variant in variant_sockets.csv.

Socket COUNT, however, is derivable: every variant is either a named-affix
roll (1 fixed affix + budget-1 gem sockets) or a "Base roll" (0 affix +
budget gem sockets), where budget is fixed per rarity (confirmed by the
user): Legendary=3, Epic/Excellent=2, Rare=1. See RARITY_SOCKET_BUDGET.
Common/Damaged are intentionally unmodeled (confirmed not worth it); Holy
wasn't covered by the stated rule and needs the same manual treatment.

Sockets also have a TIER (confirmed 2026-08-05): a T2 socket accepts T1 or
T2 gems, a T1 socket only accepts T1 gems. Encoded in variant_sockets.csv as
a digit suffix on the shape token, e.g. "amethyst2" = T2 amethyst socket;
bare "amethyst" defaults to T1 (confirmed: the Raven Priest Robe example
given without tier suffixes was all-T1).

Beverages (confirmed 2026-08-07) are a second, independent affix source:
exactly one active at a time, each tier (T1-T4) grants a points budget to
spread freely across affixes (no shape/type restriction, unlike gems), with
a per-affix cap. See BEVERAGE_TIERS / beverage_allocation_for().

GEMS ARE CRAFTED, NOT A FIXED CATALOG (confirmed 2026-08-08, superseding the
earlier gems.json-catalog approach). MistfallDB's ~320 named gems are only a
sample of pre-made combinations, not the full space -- confirmed by a real
in-game build that needed a T1 agate "Sky Piercer" gem that doesn't exist
anywhere in the scraped catalog. The real mechanic: each of the 4 real gem
shapes (agate/amethyst/moonstone/peridot) has a fixed pool of ~12 affixes it
can grant (data/processed/gems_raw.json, user-provided); a T1 gem of that
shape lets you pick any ONE from the pool, a T2 gem lets you pick any TWO
DIFFERENT ones (never the same affix twice). "Universal" is confirmed to be
socket-only -- a universal socket accepts a gem crafted in any of the 4 real
shapes, there's no separate universal gem shape/pool. gems.json (the scraped
catalog) is no longer used by the solver; GameData.gem_pools is authoritative.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

RARITY_SOCKET_BUDGET: dict[str, int] = {
    "Legendary": 3,
    "Epic": 2,
    "Excellent": 2,
    "Rare": 1,
}

# Beverages: a second, independent affix source on top of gear+gems.
# Exactly one active at a time (confirmed 2026-08-05). Each tier gives a
# points budget to spread across affixes, capped per-affix -- T1/T2 cap at
# +1/affix (so budget == number of distinct affixes touched), T3/T4 allow
# up to +2 on a single affix.
BEVERAGE_TIERS: dict[str, dict[str, int]] = {
    "T1": {"budget": 2, "max_per_affix": 1},
    "T2": {"budget": 4, "max_per_affix": 1},
    "T3": {"budget": 6, "max_per_affix": 2},
    "T4": {"budget": 8, "max_per_affix": 2},
}

# Total-cap mechanic (confirmed 2026-08-11): a hard ceiling on the SUM of
# every affix stack in the whole build -- gear innate rolls + gems + beverage,
# target affixes and incidental/bonus ones alike -- not just per-affix caps.
# User-confirmed formula: (per-slot budget * 8 gear slots) + beverage budget.
# The per-slot budget here is one HIGHER than RARITY_SOCKET_BUDGET above --
# that table only counts GEM sockets, this one also counts the slot's own
# innate-affix contribution. Verified against two given examples: full
# Legendary + T4 = 4*8+8 = 40, full Epic + T4 = 3*8+8 = 32. Only meaningful
# for a build where every slot is assumed to be the same rarity (a "kit
# tier"), so this is only applied when the caller has committed to a single
# rarity, not a mixed search -- see find_builds' allowed_rarities handling.
TOTAL_CAP_SLOT_BUDGET: dict[str, int] = {
    "Legendary": 4,
    "Epic": 3,
    "Excellent": 3,
    "Rare": 2,
}

GEAR_SLOT_COUNT = 8  # Weapon/Head/Chest/Hands/Legs/Feet/Necklace/Ring

# Canonical display order (confirmed 2026-08-11) -- weapon, then head-to-toe,
# then jewelry. Purely a presentation concern: solver/build_solver.py doesn't
# care what order slots are processed or picks are returned in.
GEAR_SLOT_ORDER: tuple[str, ...] = (
    "Weapon", "Head", "Chest", "Hands", "Legs", "Feet", "Necklace", "Ring",
)


def total_stack_cap(rarity: str | None, beverage_tier: str | None) -> int | None:
    """Hard ceiling on the combined total of every affix stack (gear+gems+
    beverage, target and bonus alike) for a kit uniformly of this rarity.
    None if the rarity isn't in TOTAL_CAP_SLOT_BUDGET (unmodeled tier, e.g.
    Common/Damaged/Holy) -- caller should treat that as "no cap enforced"."""
    budget = TOTAL_CAP_SLOT_BUDGET.get(rarity or "")
    if budget is None:
        return None
    bev_budget = BEVERAGE_TIERS[beverage_tier]["budget"] if beverage_tier else 0
    return GEAR_SLOT_COUNT * budget + bev_budget


def beverage_allocation_for(
    deficits: dict[str, int], tier: str | None
) -> dict[str, int] | None:
    """Given remaining need per affix (after gear+gems), return how a
    beverage of this tier would cover it, or None if it can't. Greedy is
    exact here: there's no cost differentiation between affixes, so any
    allocation that respects the per-affix cap and total budget works."""
    needed = {a: d for a, d in deficits.items() if d > 0}
    if not needed:
        return {}
    if tier is None:
        return None
    spec = BEVERAGE_TIERS[tier]
    if any(d > spec["max_per_affix"] for d in needed.values()):
        return None
    if sum(needed.values()) > spec["budget"]:
        return None
    return needed


def expected_socket_count(rarity: str | None, has_affix: bool) -> int | None:
    """Total gem sockets for a variant, derived from rarity + whether it
    carries a fixed/rolled affix. None if the rarity isn't in the budget
    table (unmodeled rarity, e.g. Common/Damaged/Holy)."""
    budget = RARITY_SOCKET_BUDGET.get(rarity or "")
    if budget is None:
        return None
    return max(budget - 1, 0) if has_affix else budget


@dataclass(frozen=True)
class Affix:
    slug: str
    name: str
    category: str | None
    stack_cap: int | None  # None = unknown, not yet entered in affix_caps_override.json


@dataclass(frozen=True)
class Gem:
    slug: str
    name: str
    shape: str | None  # moonstone/peridot/agate/onyx/amethyst/purple_rhomb/None=unknown
    tier: int | None  # 1 or 2
    affix_slugs: tuple[str, ...]  # resolved from the gem's granted affix names


@dataclass(frozen=True)
class SocketSpec:
    shape: str  # moonstone/peridot/agate/onyx/amethyst/purple_rhomb/universal
    tier: int = 1  # a T2 socket accepts T1 or T2 gems; a T1 socket only accepts T1

    def accepts(self, gem: Gem) -> bool:
        if self.shape != "universal" and gem.shape != self.shape:
            return False
        return (gem.tier or 1) <= self.tier


_SOCKET_TOKEN_RE = re.compile(r"^([a-z_]+?)(\d)?$")


def parse_socket_token(token: str) -> SocketSpec:
    token = token.strip().lower()
    m = _SOCKET_TOKEN_RE.match(token)
    if not m:
        raise ValueError(f"unrecognized socket token: {token!r}")
    shape, tier_digit = m.group(1), m.group(2)
    return SocketSpec(shape=shape, tier=int(tier_digit) if tier_digit else 1)


@dataclass(frozen=True)
class GearVariant:
    slug: str
    affix_slug: str | None  # None for "Base roll" (no innate affix)
    combat_value: int
    rarity: str | None
    socket_shapes: tuple[SocketSpec, ...] | None  # None = not yet entered in variant_sockets.csv

    @property
    def expected_socket_count(self) -> int | None:
        return expected_socket_count(self.rarity, has_affix=self.affix_slug is not None)


@dataclass(frozen=True)
class GearItem:
    slug: str
    name: str
    kind: str  # "armor" | "weapon"
    classes: tuple[str, ...]  # one or more class names, or ("Any",) for class-agnostic gear
    slot: str | None
    rarity: str | None
    variants: tuple[GearVariant, ...]
    weapon_type: str | None = None  # e.g. "Dagger"/"Dual Blade" -- see data/processed/weapon_types.json

    def usable_by(self, class_req: str) -> bool:
        return "Any" in self.classes or class_req in self.classes


REAL_GEM_SHAPES = ("agate", "amethyst", "moonstone", "peridot")


@dataclass
class GameData:
    affixes_by_slug: dict[str, Affix] = field(default_factory=dict)
    gems: list[Gem] = field(default_factory=list)  # scraped named catalog -- reference only, not used by the solver
    gem_pools: dict[str, tuple[str, ...]] = field(default_factory=dict)  # shape -> craftable affix slugs
    gear: list[GearItem] = field(default_factory=list)
    unresolved_affix_names: set[str] = field(default_factory=set)

    def affix(self, slug: str) -> Affix | None:
        return self.affixes_by_slug.get(slug)

    def gear_for(self, class_req: str, slot: str) -> list[GearItem]:
        return [g for g in self.gear if g.slot == slot and g.usable_by(class_req)]

    def classes(self) -> list[str]:
        names = {c for g in self.gear for c in g.classes if c != "Any"}
        return sorted(names)

    def slots(self) -> list[str]:
        return sorted({g.slot for g in self.gear if g.slot})


def _slugify_name(name: str) -> str:
    return name.strip().lower().replace(" ", "-").replace("'", "").replace(":", "")


def load_game_data(data_dir: Path = DATA_DIR) -> GameData:
    data = GameData()

    affixes_raw = json.loads((data_dir / "affixes.json").read_text(encoding="utf-8"))
    name_to_slug: dict[str, str] = {}
    for a in affixes_raw:
        cap = a.get("stack_cap")
        data.affixes_by_slug[a["slug"]] = Affix(
            slug=a["slug"],
            name=a["name"],
            category=a.get("category"),
            stack_cap=int(cap) if cap is not None else None,
        )
        name_to_slug[a["name"].strip().lower()] = a["slug"]

    def resolve_affix_name(name: str) -> str | None:
        key = name.strip().lower()
        if key in name_to_slug:
            return name_to_slug[key]
        guess = _slugify_name(name)
        if guess in data.affixes_by_slug:
            return guess
        data.unresolved_affix_names.add(name)
        return None

    gems_path = data_dir / "gems.json"
    if gems_path.exists():
        for g in json.loads(gems_path.read_text(encoding="utf-8")):
            affix_slugs = tuple(
                s for s in (resolve_affix_name(n) for n in g.get("affixes", [])) if s
            )
            data.gems.append(Gem(
                slug=g["slug"],
                name=g["name"],
                shape=g.get("shape"),
                tier=g.get("tier"),
                affix_slugs=affix_slugs,
            ))

    gems_raw_path = data_dir / "gems_raw.json"
    if gems_raw_path.exists():
        raw_pools = json.loads(gems_raw_path.read_text(encoding="utf-8"))
        for shape, affix_names in raw_pools.items():
            resolved = tuple(
                s for s in (resolve_affix_name(n) for n in affix_names) if s
            )
            data.gem_pools[shape.strip().lower()] = resolved

    shapes_by_variant_slug: dict[str, tuple[SocketSpec, ...]] = {}
    variant_sockets_path = data_dir / "variant_sockets.csv"
    if variant_sockets_path.exists():
        with variant_sockets_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                raw = (row.get("socket_shapes") or "").strip()
                if raw.lower() == "none":
                    shapes_by_variant_slug[row["variant_slug"]] = ()  # confirmed zero sockets
                elif raw:
                    shapes_by_variant_slug[row["variant_slug"]] = tuple(
                        parse_socket_token(s) for s in raw.split(",") if s.strip()
                    )
                # else: blank = not yet entered, leave unset (None) so it's excluded

    weapon_types_path = data_dir / "weapon_types.json"
    weapon_types: dict[str, str] = {}
    if weapon_types_path.exists():
        weapon_types = json.loads(weapon_types_path.read_text(encoding="utf-8"))

    gear_path = data_dir / "gear.json"
    if gear_path.exists():
        for it in json.loads(gear_path.read_text(encoding="utf-8")):
            rarity = it.get("rarity")
            variants = tuple(
                GearVariant(
                    slug=v["slug"],
                    affix_slug=(
                        None if v["affix"] == "Base roll" else resolve_affix_name(v["affix"])
                    ),
                    combat_value=v["combat_value"],
                    rarity=rarity,
                    socket_shapes=shapes_by_variant_slug.get(v["slug"]),
                )
                for v in it.get("variants", [])
            )
            raw_classes = it.get("class") or ""
            classes = tuple(c.strip() for c in raw_classes.split(",") if c.strip()) or ("Any",)
            data.gear.append(GearItem(
                slug=it["slug"],
                name=it["name"],
                kind=it["kind"],
                classes=classes,
                slot=it.get("slot"),
                rarity=rarity,
                variants=variants,
                weapon_type=weapon_types.get(it["slug"]),
            ))

    return data


if __name__ == "__main__":
    gd = load_game_data()
    print(f"{len(gd.affixes_by_slug)} affixes, {len(gd.gems)} gems, {len(gd.gear)} gear items")
    print(f"classes seen so far: {gd.classes()}")
    print(f"slots seen so far: {gd.slots()}")
    if gd.unresolved_affix_names:
        print(f"WARNING: {len(gd.unresolved_affix_names)} affix names didn't resolve to a known affix:")
        for n in sorted(gd.unresolved_affix_names)[:20]:
            print(f"  {n!r}")
    total_variants = sum(len(g.variants) for g in gd.gear)
    with_shapes = sum(1 for g in gd.gear for v in g.variants if v.socket_shapes is not None)
    print(f"variants with socket shapes filled in: {with_shapes}/{total_variants}")
