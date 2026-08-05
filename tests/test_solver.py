"""Unit tests for solver/build_solver.py against small hand-built GameData,
not the real scrape (keeps tests fast and independent of site data)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.entities import Affix, GameData, GearItem, GearVariant, Gem  # noqa: E402
from solver.build_solver import find_builds  # noqa: E402


def make_game_data() -> GameData:
    data = GameData()
    data.affixes_by_slug = {
        "valor": Affix("valor", "Valor", "Offensive", stack_cap=7),
        "elusive": Affix("elusive", "Elusive", "Utility", stack_cap=5),
        "stoic": Affix("stoic", "Stoic", "Defensive", stack_cap=5),
    }

    data.gems = [
        Gem("valor-peridot", "Valor Peridot", "peridot", 1, ("valor",)),
        Gem("valor-elusive-rhomb", "Valor Elusive Rhomb", "purple_rhomb", 2, ("valor", "elusive")),
        Gem("stoic-agate", "Stoic Agate", "agate", 1, ("stoic",)),
    ]

    # Head slot: item A has a fixed Valor roll + one purple_rhomb socket
    # (Legendary budget 3, minus 1 for the fixed affix = 2 -- but only 1
    # entered here on purpose, to also cover "fewer sockets than the rarity
    # budget allows" being handled fine since shapes are just whatever's
    # actually entered in variant_sockets.csv, not re-derived).
    item_a = GearItem(
        slug="item-a", name="Item A", kind="armor", classes=("Sorcerer",),
        slot="Head", rarity="Legendary",
        variants=(GearVariant("item-a-v1", "valor", 500, "Legendary", ("purple_rhomb",)),),
    )
    # Head slot: item B has no innate affix, two purple_rhomb sockets.
    item_b = GearItem(
        slug="item-b", name="Item B", kind="armor", classes=("Sorcerer",),
        slot="Head", rarity="Legendary",
        variants=(GearVariant("item-b-v1", None, 480, "Legendary", ("purple_rhomb", "purple_rhomb")),),
    )
    # Chest slot: item C, one agate socket only, no innate affix options relevant here.
    item_c = GearItem(
        slug="item-c", name="Item C", kind="armor", classes=("Sorcerer",),
        slot="Chest", rarity="Rare",
        variants=(GearVariant("item-c-v1", None, 300, "Rare", ("agate",)),),
    )
    # An item with unknown sockets (socket_shapes=None) must never appear in results.
    item_unknown = GearItem(
        slug="item-unknown", name="Item Unknown", kind="armor", classes=("Sorcerer",),
        slot="Head", rarity="Legendary",
        variants=(GearVariant("item-unknown-v1", "valor", 999, "Legendary", None),),
    )
    data.gear = [item_a, item_b, item_c, item_unknown]
    return data


def test_finds_at_least_one_build_for_simple_target():
    data = make_game_data()
    builds = find_builds(data, "Sorcerer", {"valor": 2}, max_results=25)
    assert builds, "expected at least one feasible build"
    for b in builds:
        assert b.total_affix_counts().get("valor", 0) >= 2


def test_excludes_gear_with_unknown_sockets():
    data = make_game_data()
    builds = find_builds(data, "Sorcerer", {"valor": 3}, max_results=25)
    for b in builds:
        for pick in b.picks:
            assert pick.item.slug != "item-unknown"


def test_infeasible_target_returns_empty():
    data = make_game_data()
    # max achievable valor across item-a/item-b + gems is well under 100
    builds = find_builds(data, "Sorcerer", {"valor": 100}, max_results=25)
    assert builds == []


def test_multi_affix_target_and_bonus_reporting():
    data = make_game_data()
    builds = find_builds(data, "Sorcerer", {"valor": 2, "elusive": 1}, max_results=25)
    assert builds
    for b in builds:
        totals = b.total_affix_counts()
        assert totals.get("valor", 0) >= 2
        assert totals.get("elusive", 0) >= 1
        # elusive only ever comes bundled with valor via the tier-2 gem here,
        # so nothing outside the two targets should show up as a "bonus"
        assert b.bonus_affixes({"valor", "elusive"}) == {}


def test_stoic_target_pulls_in_chest_slot():
    data = make_game_data()
    builds = find_builds(data, "Sorcerer", {"stoic": 1}, max_results=25)
    assert builds
    assert all(
        any(pick.slot == "Chest" for pick in b.picks)
        for b in builds
    )


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
