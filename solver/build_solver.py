"""
Feasibility + enumeration solver.

Given a class and a set of target (affix_slug -> min stack count) pairs,
find every combination of gear (one item+variant per affix-relevant slot)
and gems (socketed into that gear) that reaches every target, plus whatever
other affixes tag along for the ride.

Deliberately not a single-objective optimizer: the ask was "show me the
possible builds," not "the best build," so this does a pruned exhaustive
search over the (small, once filtered to target-relevant candidates)
combinatorial space instead of using a MIP/CP-SAT solver.

Only gear VARIANTS with `socket_shapes` filled in (see variant_sockets.csv)
are considered -- variants with unknown sockets are invisible to the solver
until that data is entered, rather than silently assumed to have zero
sockets.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.entities import GameData, GearItem, GearVariant, Gem, SocketSpec  # noqa: E402


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

    def total_affix_counts(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for pick in self.picks:
            for affix, n in pick.affix_counts().items():
                totals[affix] = totals.get(affix, 0) + n
        return totals

    def bonus_affixes(self, target_slugs: set[str]) -> dict[str, int]:
        return {a: n for a, n in self.total_affix_counts().items() if a not in target_slugs}


def _candidate_slot_picks(
    data: GameData, class_req: str, slot: str, target_slugs: set[str]
) -> list[SlotPick]:
    """Every (item, variant, socket-assignment) combo for one slot that
    grants at least one target affix, via innate roll and/or socketed gems."""
    items = [g for g in data.gear if g.slot == slot and g.usable_by(class_req)]

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

            for combo in product(*per_socket_options):
                if variant_relevant or any(s.gem for s in combo):
                    picks.append(SlotPick(slot, item, variant, combo))
    return picks


def find_builds(
    data: GameData,
    class_req: str,
    targets: dict[str, int],
    max_results: int = 25,
) -> list[Build]:
    target_slugs = set(targets)
    all_slots = sorted({
        g.slot for g in data.gear
        if g.slot and g.usable_by(class_req)
    })

    per_slot_candidates: dict[str, list[SlotPick]] = {}
    for slot in all_slots:
        cands = _candidate_slot_picks(data, class_req, slot, target_slugs)
        if cands:
            per_slot_candidates[slot] = cands

    if not per_slot_candidates:
        return []

    slots = list(per_slot_candidates)

    # best-case achievable count per target affix from slots[i:], precomputed once
    def best_case(slot: str) -> dict[str, int]:
        best: dict[str, int] = {}
        for pick in per_slot_candidates[slot]:
            for a, n in pick.affix_counts().items():
                if a in target_slugs:
                    best[a] = max(best.get(a, 0), n)
        return best

    suffix_cap: list[dict[str, int]] = [{} for _ in range(len(slots) + 1)]
    for i in range(len(slots) - 1, -1, -1):
        combined = dict(suffix_cap[i + 1])
        for a, n in best_case(slots[i]).items():
            combined[a] = combined.get(a, 0) + n
        suffix_cap[i] = combined

    results: list[Build] = []

    def dfs(i: int, chosen: list[SlotPick], totals: dict[str, int]) -> None:
        if len(results) >= max_results:
            return
        if i == len(slots):
            if all(totals.get(a, 0) >= n for a, n in targets.items()):
                results.append(Build(tuple(chosen)))
            return
        cap = suffix_cap[i + 1]
        for pick in per_slot_candidates[slots[i]]:
            new_totals = dict(totals)
            for a, n in pick.affix_counts().items():
                new_totals[a] = new_totals.get(a, 0) + n
            if all(new_totals.get(a, 0) + cap.get(a, 0) >= n for a, n in targets.items()):
                dfs(i + 1, chosen + [pick], new_totals)
                if len(results) >= max_results:
                    return

    dfs(0, [], {})
    return results
