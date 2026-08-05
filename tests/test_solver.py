"""Unit tests for solver/build_solver.py against small hand-built GameData,
not the real scrape (keeps tests fast and independent of site data)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.entities import Affix, GameData, GearItem, GearVariant, Gem, SocketSpec  # noqa: E402
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
        Gem("stoic-elusive-agate-t2", "Stoic Elusive Agate", "agate", 2, ("stoic", "elusive")),
    ]

    # Head slot: item A has a fixed Valor roll + one T1 purple_rhomb socket
    # (Legendary budget 3, minus 1 for the fixed affix = 2 -- but only 1
    # entered here on purpose, to also cover "fewer sockets than the rarity
    # budget allows" being handled fine since shapes are just whatever's
    # actually entered in variant_sockets.csv, not re-derived).
    item_a = GearItem(
        slug="item-a", name="Item A", kind="armor", classes=("Sorcerer",),
        slot="Head", rarity="Legendary",
        variants=(GearVariant("item-a-v1", "valor", 500, "Legendary", (SocketSpec("purple_rhomb", 1),)),),
    )
    # Head slot: item B has no innate affix, two T2 purple_rhomb sockets
    # (needed so the T2 "Valor Elusive Rhomb" gem has somewhere to go).
    item_b = GearItem(
        slug="item-b", name="Item B", kind="armor", classes=("Sorcerer",),
        slot="Head", rarity="Legendary",
        variants=(GearVariant(
            "item-b-v1", None, 480, "Legendary",
            (SocketSpec("purple_rhomb", 2), SocketSpec("purple_rhomb", 2)),
        ),),
    )
    # Chest slot: item C, one T1 agate socket -- can hold the T1 stoic gem
    # but NOT the T2 stoic/elusive gem (tier restriction).
    item_c = GearItem(
        slug="item-c", name="Item C", kind="armor", classes=("Sorcerer",),
        slot="Chest", rarity="Rare",
        variants=(GearVariant("item-c-v1", None, 300, "Rare", (SocketSpec("agate", 1),)),),
    )
    # Legs slot: item D, one T2 agate socket -- can hold either stoic gem.
    item_d = GearItem(
        slug="item-d", name="Item D", kind="armor", classes=("Sorcerer",),
        slot="Legs", rarity="Rare",
        variants=(GearVariant("item-d-v1", None, 300, "Rare", (SocketSpec("agate", 2),)),),
    )
    # An item with unknown sockets (socket_shapes=None) must never appear in results.
    item_unknown = GearItem(
        slug="item-unknown", name="Item Unknown", kind="armor", classes=("Sorcerer",),
        slot="Head", rarity="Legendary",
        variants=(GearVariant("item-unknown-v1", "valor", 999, "Legendary", None),),
    )
    data.gear = [item_a, item_b, item_c, item_d, item_unknown]
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
        # elusive is only reachable here via gems that also grant stoic (the
        # tier-2 rhomb gem on Head, or item-d's tier-2 agate gem on Legs),
        # so "stoic" tagging along as a bonus is expected, nothing else is
        assert b.bonus_affixes({"valor", "elusive"}).keys() <= {"stoic"}


def test_stoic_target_pulls_in_chest_slot():
    data = make_game_data()
    builds = find_builds(data, "Sorcerer", {"stoic": 1}, max_results=25)
    assert builds
    assert all(
        any(pick.slot == "Chest" for pick in b.picks)
        for b in builds
    )


def test_socket_tier_accepts_rule():
    t1_gem = Gem("g1", "G1", "agate", 1, ("stoic",))
    t2_gem = Gem("g2", "G2", "agate", 2, ("stoic", "elusive"))
    t1_socket = SocketSpec("agate", 1)
    t2_socket = SocketSpec("agate", 2)
    assert t1_socket.accepts(t1_gem)
    assert not t1_socket.accepts(t2_gem)
    assert t2_socket.accepts(t1_gem)
    assert t2_socket.accepts(t2_gem)


def test_solver_respects_socket_tier_end_to_end():
    data = GameData()
    data.affixes_by_slug = {
        "stoic": Affix("stoic", "Stoic", "Defensive", stack_cap=5),
        "elusive": Affix("elusive", "Elusive", "Utility", stack_cap=5),
    }
    data.gems = [
        Gem("stoic-agate", "Stoic Agate", "agate", 1, ("stoic",)),
        Gem("stoic-elusive-agate-t2", "Stoic Elusive Agate", "agate", 2, ("stoic", "elusive")),
    ]
    item_c = GearItem(
        slug="item-c", name="Item C", kind="armor", classes=("Sorcerer",),
        slot="Chest", rarity="Rare",
        variants=(GearVariant("item-c-v1", None, 300, "Rare", (SocketSpec("agate", 1),)),),
    )
    data.gear = [item_c]
    # a T1 socket can never hold the T2 gem that grants elusive here
    assert find_builds(data, "Sorcerer", {"elusive": 1}, max_results=5) == []
    # but stoic alone is reachable via the T1 gem in that same T1 socket
    assert find_builds(data, "Sorcerer", {"stoic": 1}, max_results=5)


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
