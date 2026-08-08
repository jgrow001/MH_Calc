"""
Feasibility + enumeration solver.

Given a class and a set of target (affix_slug -> min stack count) pairs,
find every combination of gear (one item+variant per affix-relevant slot),
gems (socketed into that gear), and beverage allocation that reaches every
target, plus whatever other affixes tag along for the ride.

Deliberately not a single-objective optimizer: the ask was "show me the
possible builds," not "the best build," so this does a pruned exhaustive
search over the (small, once filtered to target-relevant candidates)
combinatorial space instead of using a MIP/CP-SAT solver.

Only gear VARIANTS with `socket_shapes` filled in (see variant_sockets.csv)
are considered -- variants with unknown sockets are invisible to the solver
until that data is entered, rather than silently assumed to have zero
sockets.

PERFORMANCE (confirmed 2026-08-07, real data): with several affix targets
at once, per-slot candidate counts hit the thousands (many gems can grant
any given affix, and jewelry has up to 3 sockets each) and the naive
product across 8 slots reaches ~10^24 -- the app hung for minutes on a
5-affix query. The per-affix independent-max pruning bound below is a very
loose over-estimate (it assumes a slot can simultaneously max out every
target affix via different hypothetical picks, when really it picks ONE
candidate), so it rarely cuts anything. Three mitigations, all exact
(no loss of correctness):
  - dedup candidates within a (item, variant) by their affix-count
    signature -- many distinct gem choices contribute identically for
    feasibility purposes
  - process slots with fewer candidates first (fail-fast, standard CSP
    ordering heuristic) instead of alphabetically
  - a hard cap on DFS nodes visited (max_nodes) as an absolute backstop,
    since no pruning bound is guaranteed tight against arbitrary inputs
An optional rarity filter (allowed_rarities) lets the caller shrink the
base candidate pool directly, which helps far more than any of the above.

A beverage (see model.entities.BEVERAGE_TIERS) is a second, independent
affix source: whatever gear+gems fall short of the targets, the chosen
beverage tier is checked for whether it can cover the remainder (its
allocation is fully determined by the shortfall -- there's no freedom in
how to split it, so it doesn't add branching, just a feasibility check at
each candidate build).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.entities import (  # noqa: E402
    BEVERAGE_TIERS,
    GameData,
    GearItem,
    GearVariant,
    Gem,
    SocketSpec,
    beverage_allocation_for,
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


def _candidate_slot_picks(
    data: GameData,
    class_req: str,
    slot: str,
    target_slugs: set[str],
    allowed_rarities: set[str] | None = None,
) -> list[SlotPick]:
    """Every (item, variant, socket-assignment) combo for one slot that
    grants at least one target affix, via innate roll and/or socketed gems.
    Deduped within each (item, variant) by affix-count signature -- many
    different gem choices are interchangeable for feasibility purposes."""
    items = [
        g for g in data.gear
        if g.slot == slot and g.usable_by(class_req)
        and (allowed_rarities is None or g.rarity in allowed_rarities)
    ]

    relevant_gems = [
        gem for gem in data.gems if any(a in target_slugs for a in gem.affix_slugs)
    ]

    picks: list[SlotPick] = []
    for item in items:
        for variant in item.variants:
            if variant.socket_shapes is None:
                continue  # not yet entered in variant_sockets.csv
            variant_relevant = variant.affix_slug in target_slugs

            per_socket_options: list[list[SocketPick]] = []
            for socket in variant.socket_shapes:
                options = [SocketPick(socket, None)]
                options.extend(
                    SocketPick(socket, gem) for gem in relevant_gems if socket.accepts(gem)
                )
                per_socket_options.append(options)

            if not per_socket_options:
                if variant_relevant:
                    picks.append(SlotPick(slot, item, variant, ()))
                continue

            seen_signatures: set[tuple] = set()
            for combo in product(*per_socket_options):
                if not (variant_relevant or any(s.gem for s in combo)):
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
    max_nodes: int = 300_000,
) -> list[Build]:
    target_slugs = set(targets)
    all_slots = sorted({
        g.slot for g in data.gear
        if g.slot and g.usable_by(class_req)
    })

    per_slot_candidates: dict[str, list[SlotPick]] = {}
    for slot in all_slots:
        cands = _candidate_slot_picks(data, class_req, slot, target_slugs, allowed_rarities)
        if cands:
            per_slot_candidates[slot] = cands

    if not per_slot_candidates:
        # no gear/gem touches any target affix -- still feasible if the
        # beverage alone can cover the whole target (e.g. a small target
        # fully covered by a T1 beverage, no gear needed at all)
        build = _try_finish_with_beverage((), {}, targets, beverage_tier)
        return [build] if build else []

    # fail-fast ordering: process the most-constrained (fewest candidate)
    # slots first -- standard CSP heuristic, makes both pruning checks below
    # cut branches much earlier in practice
    slots = sorted(per_slot_candidates, key=lambda s: len(per_slot_candidates[s]))

    # per-affix best-case (independent, so still an over-estimate -- a slot
    # can't actually max every affix at once via different hypothetical
    # picks) and a joint best-single-candidate total, both precomputed once
    # per slot and summed over the suffix. Together these catch both "this
    # one affix is unreachable" and "the total need across affixes exceeds
    # what remaining slots could jointly provide" -- the second is what a
    # multi-affix query actually needs to prune well.
    def best_case(slot: str) -> dict[str, int]:
        best: dict[str, int] = {}
        for pick in per_slot_candidates[slot]:
            for a, n in pick.affix_counts().items():
                if a in target_slugs:
                    best[a] = max(best.get(a, 0), n)
        return best

    def best_joint_score(slot: str) -> int:
        return max(
            sum(min(pick.affix_counts().get(a, 0), targets[a]) for a in target_slugs)
            for pick in per_slot_candidates[slot]
        )

    suffix_cap: list[dict[str, int]] = [{} for _ in range(len(slots) + 1)]
    suffix_joint: list[int] = [0] * (len(slots) + 1)
    for i in range(len(slots) - 1, -1, -1):
        combined = dict(suffix_cap[i + 1])
        for a, n in best_case(slots[i]).items():
            combined[a] = combined.get(a, 0) + n
        suffix_cap[i] = combined
        suffix_joint[i] = suffix_joint[i + 1] + best_joint_score(slots[i])

    beverage_budget = BEVERAGE_TIERS[beverage_tier]["budget"] if beverage_tier else 0
    # safe over-estimate for the per-affix check: the most a beverage could
    # add to any single affix (the real joint budget constraint is only
    # checked exactly at each leaf via _try_finish_with_beverage)
    beverage_slack = BEVERAGE_TIERS[beverage_tier]["max_per_affix"] if beverage_tier else 0

    results: list[Build] = []
    nodes_visited = 0

    def dfs(i: int, chosen: list[SlotPick], totals: dict[str, int]) -> None:
        nonlocal nodes_visited
        if len(results) >= max_results or nodes_visited >= max_nodes:
            return
        if i == len(slots):
            build = _try_finish_with_beverage(tuple(chosen), totals, targets, beverage_tier)
            if build:
                results.append(build)
            return
        cap = suffix_cap[i + 1]
        joint_remaining = suffix_joint[i + 1] + beverage_budget
        for pick in per_slot_candidates[slots[i]]:
            nodes_visited += 1
            if nodes_visited >= max_nodes:
                return
            new_totals = dict(totals)
            for a, n in pick.affix_counts().items():
                new_totals[a] = new_totals.get(a, 0) + n
            deficit_sum = sum(max(0, n - new_totals.get(a, 0)) for a, n in targets.items())
            if deficit_sum > joint_remaining:
                continue
            if all(
                new_totals.get(a, 0) + cap.get(a, 0) + beverage_slack >= n
                for a, n in targets.items()
            ):
                dfs(i + 1, chosen + [pick], new_totals)
                if len(results) >= max_results:
                    return

    dfs(0, [], {})
    return results
