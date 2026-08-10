"""
Feasibility + enumeration solver.

Given a class and a set of target (affix_slug -> min stack count) pairs,
find every combination of gear (one item+variant per affix-relevant slot),
gems (socketed into that gear), and beverage allocation that reaches every
target, plus whatever other affixes tag along for the ride.

Only gear VARIANTS with `socket_shapes` filled in (see variant_sockets.csv)
are considered -- variants with unknown sockets are invisible to the solver
until that data is entered, rather than silently assumed to have zero
sockets.

SOLVER ENGINE (confirmed 2026-08-08, superseding an earlier hand-rolled
pruned-DFS design): with several affix targets at once, per-slot candidate
counts hit the hundreds to thousands, and a *tight* target (every affix
landing exactly on target with a fully-used beverage budget, zero slack
anywhere) turned out to defeat every DFS pruning/ordering heuristic tried --
confirmed on a real 7-affix in-game build that a hand-verified-feasible
combination took 8+ seconds / millions of backtracking nodes to rediscover,
even with best-first candidate ordering. That's exactly the class of
problem exact/near-exact equality constraints across many dimensions that
a constraint solver is built for, so this now builds a CP-SAT model (one
boolean var per candidate per slot, exactly-one per slot, linear >= per
affix, beverage as bounded IntVars) and lets OR-Tools' solver find and
enumerate solutions -- correct by construction, no pruning bound to get
wrong, no node budget to exhaust into a false negative.
An optional rarity filter (allowed_rarities) still lets the caller shrink
the base candidate pool directly, which keeps the model itself smaller.

A beverage (see model.entities.BEVERAGE_TIERS) is a second, independent
affix source, encoded directly in the CP-SAT model as bounded integer
variables (0..max_per_affix each, summing to at most the tier's budget)
added to each affix's linear constraint -- not a post-hoc check.

GEMS ARE CRAFTED (confirmed 2026-08-08): a T1 gem of a given shape can be
any ONE affix from that shape's pool (model.entities.GameData.gem_pools,
from the user-provided gems_raw.json), a T2 gem any TWO DIFFERENT affixes
from the pool. This replaced an earlier design that filtered MistfallDB's
~320 scraped named gems -- that catalog turned out to be only a sample of
combinations, not the full craftable space (a real in-game build needed a
T1 agate "Sky Piercer" gem with no match in the scrape). Only combos where
at least one affix is target-relevant are generated (see
_craftable_gems_for_socket) -- for T2, only pairs where BOTH affixes are
target-relevant, since a "wasted" filler affix never helps feasibility and
the user doesn't care about efficiency, so there's no reason to search
filler variations. data.gems (the scraped catalog) is only consulted to
reuse a real gem's name/slug when one happens to match, for nicer display.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from itertools import combinations, product
from pathlib import Path

from ortools.sat.python import cp_model

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.entities import (  # noqa: E402
    BEVERAGE_TIERS,
    REAL_GEM_SHAPES,
    GameData,
    GearItem,
    GearVariant,
    Gem,
    SocketSpec,
    beverage_allocation_for,
    total_stack_cap,
)


@dataclass(frozen=True)
class SocketPick:
    socket: SocketSpec
    gem: Gem | None  # None = left empty


@dataclass(frozen=True)
class SlotPick:
    slot: str
    item: GearItem
    variant: GearVariant
    sockets: tuple[SocketPick, ...]

    def affix_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        if self.variant.affix_slug:
            counts[self.variant.affix_slug] = counts.get(self.variant.affix_slug, 0) + 1
        for sock in self.sockets:
            if sock.gem:
                for a in sock.gem.affix_slugs:
                    counts[a] = counts.get(a, 0) + 1
        return counts


@dataclass(frozen=True)
class Build:
    picks: tuple[SlotPick, ...]
    beverage_tier: str | None = None
    beverage_allocation: tuple[tuple[str, int], ...] = ()  # affix_slug -> points used

    def gear_affix_counts(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for pick in self.picks:
            for affix, n in pick.affix_counts().items():
                totals[affix] = totals.get(affix, 0) + n
        return totals

    def total_affix_counts(self) -> dict[str, int]:
        totals = self.gear_affix_counts()
        for a, n in self.beverage_allocation:
            totals[a] = totals.get(a, 0) + n
        return totals

    def beverage_points_used(self) -> int:
        return sum(n for _, n in self.beverage_allocation)

    def beverage_points_remaining(self) -> int:
        if self.beverage_tier is None:
            return 0
        return BEVERAGE_TIERS[self.beverage_tier]["budget"] - self.beverage_points_used()

    def bonus_affixes(self, target_slugs: set[str]) -> dict[str, int]:
        return {a: n for a, n in self.total_affix_counts().items() if a not in target_slugs}


def _build_named_gem_lookup(data: GameData) -> dict[tuple[str, int, frozenset], Gem]:
    """(shape, tier, affix set) -> a real scraped gem with that exact
    signature, so crafted gems can borrow a real name/slug when one exists."""
    lookup: dict[tuple[str, int, frozenset], Gem] = {}
    for gem in data.gems:
        if gem.shape and gem.tier:
            lookup.setdefault((gem.shape, gem.tier, frozenset(gem.affix_slugs)), gem)
    return lookup


def _craft_gem(
    data: GameData, shape: str, tier: int, affix_slugs: tuple[str, ...],
    named_lookup: dict[tuple[str, int, frozenset], Gem],
) -> Gem:
    named = named_lookup.get((shape, tier, frozenset(affix_slugs)))
    if named:
        return named
    names = [data.affixes_by_slug[a].name for a in affix_slugs]
    return Gem(
        slug=f"craft-{shape}-t{tier}-" + "-".join(affix_slugs),
        name=f"Custom {shape.title()} ({' + '.join(names)})",
        shape=shape,
        tier=tier,
        affix_slugs=affix_slugs,
    )


def _craftable_gems_for_socket(
    data: GameData, socket: SocketSpec, target_slugs: set[str],
    named_lookup: dict[tuple[str, int, frozenset], Gem],
) -> list[Gem]:
    """Every craftable gem that fits this socket and grants at least one
    target affix. T1: any single target-relevant pool affix. T2: any pair of
    target-relevant pool affixes (both members relevant -- a filler affix
    the player doesn't care about never helps feasibility, so there's no
    value in enumerating what it could be). A universal socket draws from
    all 4 real shapes' pools, since it accepts a gem crafted in any of them."""
    shapes = REAL_GEM_SHAPES if socket.shape == "universal" else (socket.shape,)
    gems: list[Gem] = []
    for shape in shapes:
        pool = data.gem_pools.get(shape, ())
        relevant = [a for a in pool if a in target_slugs]
        for a in relevant:
            gems.append(_craft_gem(data, shape, 1, (a,), named_lookup))
        if socket.tier >= 2:
            for a, b in combinations(relevant, 2):
                gems.append(_craft_gem(data, shape, 2, (a, b), named_lookup))
    return gems


def _candidate_slot_picks(
    data: GameData,
    class_req: str,
    slot: str,
    target_slugs: set[str],
    allowed_rarities: set[str] | None = None,
    required_item_slug: str | None = None,
    weapon_type: str | None = None,
    allowed_variant_slugs: set[str] | None = None,
) -> list[SlotPick]:
    """Every (item, variant, socket-assignment) combo for one slot that
    grants at least one target affix, via innate roll and/or socketed gems.
    Deduped within each (item, variant) by affix-count signature -- many
    different gem choices are interchangeable for feasibility purposes.

    If required_item_slug is set, candidates are restricted to that one
    item (e.g. "must use Raven War Pendant in Necklace") -- and unlike the
    normal case, combos that don't touch any target affix are still kept
    (an all-empty-sockets combo included), since the item must be usable in
    the build even if none of its sockets end up helping the target.

    allowed_variant_slugs further restricts which of that item's specific
    rolls/variants are eligible (e.g. only the "Aegis" and "Curse" rolls of
    Raven Priest Robe, not all 9) -- lets the user narrow down to one or a
    few known variants after locking an item, rather than the solver being
    free to pick any roll.

    weapon_type (e.g. "Dagger"/"Dual Blade") only applies to the Weapon
    slot -- some classes can equip either of two incompatible weapon
    categories filling the same slot, but only one actually grants its
    affixes when equipped, so the build has to commit to one type rather
    than the solver mixing candidates from both."""
    items = [
        g for g in data.gear
        if g.slot == slot and g.usable_by(class_req)
        and (allowed_rarities is None or g.rarity in allowed_rarities)
        and (required_item_slug is None or g.slug == required_item_slug)
        and (weapon_type is None or slot != "Weapon" or g.weapon_type == weapon_type)
    ]

    named_lookup = _build_named_gem_lookup(data)
    gem_cache: dict[tuple[str, int], list[Gem]] = {}

    def gems_for(socket: SocketSpec) -> list[Gem]:
        key = (socket.shape, socket.tier)
        if key not in gem_cache:
            gem_cache[key] = _craftable_gems_for_socket(data, socket, target_slugs, named_lookup)
        return gem_cache[key]

    picks: list[SlotPick] = []
    for item in items:
        for variant in item.variants:
            if variant.socket_shapes is None:
                continue  # not yet entered in variant_sockets.csv
            if allowed_variant_slugs is not None and variant.slug not in allowed_variant_slugs:
                continue
            variant_relevant = variant.affix_slug in target_slugs

            per_socket_options: list[list[SocketPick]] = []
            for socket in variant.socket_shapes:
                options = [SocketPick(socket, None)]
                options.extend(SocketPick(socket, gem) for gem in gems_for(socket))
                per_socket_options.append(options)

            if not per_socket_options:
                if variant_relevant or required_item_slug:
                    picks.append(SlotPick(slot, item, variant, ()))
                continue

            seen_signatures: set[tuple] = set()
            for combo in product(*per_socket_options):
                if not required_item_slug and not (variant_relevant or any(s.gem for s in combo)):
                    continue
                pick = SlotPick(slot, item, variant, combo)
                sig = tuple(sorted(pick.affix_counts().items()))
                if sig in seen_signatures:
                    continue
                seen_signatures.add(sig)
                picks.append(pick)
    return picks


def _try_finish_with_beverage(
    chosen: tuple[SlotPick, ...], totals: dict[str, int], targets: dict[str, int], beverage_tier: str | None
) -> Build | None:
    deficits = {a: n - totals.get(a, 0) for a, n in targets.items()}
    allocation = beverage_allocation_for(deficits, beverage_tier)
    if allocation is None:
        return None
    return Build(chosen, beverage_tier, tuple(allocation.items()))


def find_builds(
    data: GameData,
    class_req: str,
    targets: dict[str, int],
    max_results: int = 25,
    beverage_tier: str | None = None,
    allowed_rarities: set[str] | None = None,
    max_nodes: int = 300_000,  # unused, kept for API compatibility with older callers
    max_time_seconds: float = 8.0,
    overall_time_budget_seconds: float = 15.0,
    locked_items: dict[str, str] | None = None,
    weapon_type: str | None = None,
    allowed_variants: dict[str, set[str]] | None = None,
) -> list[Build]:
    """locked_items: {slot: item_slug} forces that specific base item into
    the build for that slot (e.g. {"Necklace": "raven-war-pendant"}) --
    still lets the solver pick which variant/gems, just not which item.
    weapon_type restricts the Weapon slot to one category (e.g. "Dagger" vs
    "Dual Blade") for classes where only one equipped weapon type actually
    grants its affixes -- see model.entities.GearItem.weapon_type.
    allowed_variants: {slot: {variant_slug, ...}} further narrows a locked
    slot to one or a few specific rolls of that item, rather than leaving
    the solver free to pick any of its variants.

    Total-cap: when allowed_rarities narrows the search to exactly ONE
    rarity, that's treated as a commitment to a uniform "kit tier" (e.g. "I'm
    running full Legendary"), and model.entities.total_stack_cap()'s hard
    ceiling on the combined total of every affix stack in the build (gear +
    gems + beverage, target and bonus alike) is enforced as an extra CP-SAT
    constraint. Left unenforced for a mixed/unfiltered rarity search, since
    there's no single kit tier to compute the cap from."""
    target_slugs = set(targets)
    all_slots = sorted({
        g.slot for g in data.gear
        if g.slot and g.usable_by(class_req)
    })
    locked_items = locked_items or {}
    allowed_variants = allowed_variants or {}

    per_slot_candidates: dict[str, list[SlotPick]] = {}
    for slot in all_slots:
        cands = _candidate_slot_picks(
            data, class_req, slot, target_slugs, allowed_rarities,
            required_item_slug=locked_items.get(slot),
            weapon_type=weapon_type,
            allowed_variant_slugs=allowed_variants.get(slot),
        )
        if cands:
            per_slot_candidates[slot] = cands
        elif slot in locked_items:
            return []  # the locked item has no usable variant here (e.g. missing socket data) -- infeasible

    if not per_slot_candidates:
        # no gear/gem touches any target affix -- still feasible if the
        # beverage alone can cover the whole target (e.g. a small target
        # fully covered by a T1 beverage, no gear needed at all)
        build = _try_finish_with_beverage((), {}, targets, beverage_tier)
        return [build] if build else []

    slots = list(per_slot_candidates)
    per_slot_counts: dict[str, list[dict[str, int]]] = {
        slot: [pick.affix_counts() for pick in picks]
        for slot, picks in per_slot_candidates.items()
    }

    model = cp_model.CpModel()
    slot_vars: dict[str, list] = {}
    all_decision_vars: dict[str, list] = {}  # includes the skip var, for blocking-constraint purposes
    for slot in slots:
        n = len(per_slot_candidates[slot])
        vs = [model.NewBoolVar(f"{slot}#{k}") for k in range(n)]
        slot_vars[slot] = vs
        if slot in locked_items:
            # a locked slot has no skip option -- the whole point is that
            # something from this specific item must be equipped
            model.AddExactlyOne(vs)
            all_decision_vars[slot] = vs
        else:
            # every other touching slot is OPTIONAL: without this, a slot
            # that only has affix-granting candidates (e.g. because the
            # target is a single narrow affix) would be forced to
            # contribute, and with several such slots mandatory, total
            # contribution could be forced above the affix's own stack cap
            # -- a real bug this surfaced (curse cap 5, but 8 slots all
            # forced to grant >=1 curse each = impossible under the cap).
            skip = model.NewBoolVar(f"{slot}#skip")
            model.AddExactlyOne(vs + [skip])
            all_decision_vars[slot] = vs + [skip]

    beverage_vars: dict[str, cp_model.IntVar] = {}
    if beverage_tier:
        spec = BEVERAGE_TIERS[beverage_tier]
        for a in target_slugs:
            beverage_vars[a] = model.NewIntVar(0, spec["max_per_affix"], f"bev_{a}")
        model.Add(sum(beverage_vars.values()) <= spec["budget"])

    for a, need in targets.items():
        terms = [
            counts[a] * slot_vars[slot][k]
            for slot in slots
            for k, counts in enumerate(per_slot_counts[slot])
            if counts.get(a, 0)
        ]
        bev = beverage_vars.get(a)
        if not terms and bev is None:
            return []  # nothing anywhere can ever grant this affix -- guaranteed infeasible
        expr = sum(terms) + (bev if bev is not None else 0)
        model.Add(expr >= need)
        # hard cap: a beverage (or, in principle, redundant gear/gems) can't
        # push an affix's total past its real in-game stack cap -- without
        # this the solver would happily "achieve" e.g. eloquence 8 against a
        # cap of 7, wasting a beverage point that could cover something else
        cap = data.affixes_by_slug[a].stack_cap
        if cap is not None:
            model.Add(expr <= cap)

    if allowed_rarities is not None and len(allowed_rarities) == 1:
        kit_rarity = next(iter(allowed_rarities))
        cap_total = total_stack_cap(kit_rarity, beverage_tier)
        if cap_total is not None:
            all_terms = [
                sum(counts.values()) * slot_vars[slot][k]
                for slot in slots
                for k, counts in enumerate(per_slot_counts[slot])
            ]
            bev_total = sum(beverage_vars.values()) if beverage_vars else 0
            model.Add(sum(all_terms) + bev_total <= cap_total)

    # group each slot's candidates by (base item, innate affix roll) --
    # ignoring only which specific gems were chosen -- used below so
    # enumeration blocks on GEAR identity, not on the exact candidate.
    # Grouping by item alone would also hide genuinely different rolls of
    # the same item (e.g. an Aegis roll vs an Elusive roll of the same
    # named piece are different physical drops); grouping by the full
    # variant slug would under-collapse "Base roll" variants, which have no
    # affix and differ only by socket shape (jewelry: 3 rolls of the same
    # conceptual item) -- keying on affix_slug handles both correctly,
    # since every "Base roll" variant shares affix_slug=None.
    item_group_vars: dict[str, dict[tuple[str, str | None], list]] = {}
    for slot in slots:
        groups: dict[tuple[str, str | None], list] = {}
        for idx, pick in enumerate(per_slot_candidates[slot]):
            key = (pick.item.slug, pick.variant.affix_slug)
            groups.setdefault(key, []).append(slot_vars[slot][idx])
        item_group_vars[slot] = groups

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 8

    results: list[Build] = []
    start = time.monotonic()
    for _ in range(max_results):
        remaining = overall_time_budget_seconds - (time.monotonic() - start)
        if remaining <= 0:
            break  # overall wall-clock budget exhausted -- return whatever was found so far
        solver.parameters.max_time_in_seconds = min(max_time_seconds, remaining)
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        chosen: list[SlotPick] = []
        decision_true_vars = []  # one per slot: sum of that slot's active item-group (0/1), or the skip var
        for slot in slots:
            for k, v in enumerate(slot_vars[slot]):
                if solver.Value(v):
                    pick = per_slot_candidates[slot][k]
                    chosen.append(pick)
                    key = (pick.item.slug, pick.variant.affix_slug)
                    decision_true_vars.append(sum(item_group_vars[slot][key]))
                    break
            else:
                decision_true_vars.append(all_decision_vars[slot][-1])  # the skip var was chosen
        bev_alloc = {a: solver.Value(v) for a, v in beverage_vars.items() if solver.Value(v) > 0}
        results.append(Build(tuple(chosen), beverage_tier, tuple(sorted(bev_alloc.items()))))
        # block this exact GEAR combination (which base item per slot, or
        # skipped) so the next solve is forced to use a different item
        # somewhere, rather than just a different roll/gem arrangement
        model.Add(sum(decision_true_vars) <= len(slots) - 1)

    return results
