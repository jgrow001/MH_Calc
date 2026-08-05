"""Typed data model + loader for the scraped MistfallDB data.

Loads data/processed/{affixes,gems,gear}.json + sockets_ruleset.csv into a
single GameData object the solver and UI both consume. This is the one
place that knows how the raw scrape output is shaped, so parse_*.py can
stay dumb regex extractors.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"


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
class GearVariant:
    slug: str
    affix_slug: str | None  # None for "Base roll" (no innate affix)
    combat_value: int


@dataclass(frozen=True)
class GearItem:
    slug: str
    name: str
    kind: str  # "armor" | "weapon"
    classes: tuple[str, ...]  # one or more class names, or ("Any",) for class-agnostic gear
    slot: str | None
    rarity: str | None
    variants: tuple[GearVariant, ...]
    socket_count: int | None  # None = not yet filled into sockets_ruleset.csv
    socket_shapes: tuple[str, ...]  # e.g. ("purple_rhomb", "purple_rhomb") or ("universal",)

    def usable_by(self, class_req: str) -> bool:
        return "Any" in self.classes or class_req in self.classes


@dataclass
class GameData:
    affixes_by_slug: dict[str, Affix] = field(default_factory=dict)
    gems: list[Gem] = field(default_factory=list)
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

    sockets_by_slug: dict[str, dict] = {}
    sockets_path = data_dir / "sockets_ruleset.csv"
    if sockets_path.exists():
        with sockets_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("socket_count"):
                    shapes = tuple(
                        s.strip() for s in row.get("socket_shapes", "").split(",") if s.strip()
                    )
                    sockets_by_slug[row["slug"]] = {
                        "socket_count": int(row["socket_count"]),
                        "socket_shapes": shapes,
                    }

    gear_path = data_dir / "gear.json"
    if gear_path.exists():
        for it in json.loads(gear_path.read_text(encoding="utf-8")):
            variants = tuple(
                GearVariant(
                    slug=v["slug"],
                    affix_slug=(
                        None if v["affix"] == "Base roll" else resolve_affix_name(v["affix"])
                    ),
                    combat_value=v["combat_value"],
                )
                for v in it.get("variants", [])
            )
            sock = sockets_by_slug.get(it["slug"])
            raw_classes = it.get("class") or ""
            classes = tuple(c.strip() for c in raw_classes.split(",") if c.strip()) or ("Any",)
            data.gear.append(GearItem(
                slug=it["slug"],
                name=it["name"],
                kind=it["kind"],
                classes=classes,
                slot=it.get("slot"),
                rarity=it.get("rarity"),
                variants=variants,
                socket_count=sock["socket_count"] if sock else None,
                socket_shapes=sock["socket_shapes"] if sock else (),
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
    with_sockets = sum(1 for g in gd.gear if g.socket_count is not None)
    print(f"gear items with socket data filled in: {with_sockets}/{len(gd.gear)}")
