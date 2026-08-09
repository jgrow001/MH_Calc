"""
Streamlit UI for the Mistfall Hunters build calculator.

    streamlit run app.py

Pick a class and a set of target affixes + stack levels; see every gear+gem
combination (within current data coverage) that reaches those targets, plus
whatever other affixes come along with each option.
"""
from __future__ import annotations

import streamlit as st

from model.entities import BEVERAGE_TIERS, load_game_data
from solver.build_solver import Build, find_builds

st.set_page_config(page_title="Mistfall Hunters Build Calculator", layout="wide")


@st.cache_resource
def get_data():
    return load_game_data()


data = get_data()

st.title("Mistfall Hunters — Build Calculator")

total_variants = sum(len(g.variants) for g in data.gear)
with_sockets = sum(1 for g in data.gear for v in g.variants if v.socket_shapes is not None)
st.caption(
    f"{len(data.affixes_by_slug)} affixes · {len(data.gems)} gems · "
    f"{len(data.gear)} gear items ({with_sockets}/{total_variants} variants with socket data filled in)"
)
if with_sockets == 0:
    st.warning(
        "No gear variants have socket data filled in yet (`data/processed/variant_sockets.csv`), "
        "so the solver has nothing to search over. Fill in socket_shapes for the "
        "class/slot/variant combos you care about — no rerun needed, the app reads the CSV directly."
    )

classes = data.classes()
if not classes:
    st.error("No gear data loaded yet — run the scraper first (see README).")
    st.stop()

st.sidebar.header("Build target")
class_req = st.sidebar.selectbox("Class", classes)

affix_options = sorted(data.affixes_by_slug.values(), key=lambda a: a.name)
affix_labels = {a.slug: f"{a.name} (cap {a.stack_cap})" if a.stack_cap else f"{a.name} (cap unknown)"
                for a in affix_options}
chosen_slugs = st.sidebar.multiselect(
    "Target affixes",
    options=[a.slug for a in affix_options],
    format_func=lambda s: affix_labels[s],
)

targets: dict[str, int] = {}
for slug in chosen_slugs:
    affix = data.affixes_by_slug[slug]
    max_val = affix.stack_cap or 10
    targets[slug] = st.sidebar.slider(f"{affix.name} level", 1, max_val, min(max_val, 1))

all_rarities = sorted(
    {g.rarity for g in data.gear if g.rarity}, key=lambda r: -(len(r))  # stable, arbitrary order
)
rarity_choices = st.sidebar.multiselect(
    "Gear rarity (narrows search, faster)",
    options=all_rarities,
    default=all_rarities,
    help="Fewer rarities = fewer candidates to search, useful if Calculate is slow for a broad target.",
)
allowed_rarities = set(rarity_choices) if rarity_choices else None

weapon_types_here = sorted({
    g.weapon_type for g in data.gear
    if g.slot == "Weapon" and g.usable_by(class_req) and g.weapon_type
})
weapon_type: str | None = None
if len(weapon_types_here) > 1:
    wt_choice = st.sidebar.selectbox(
        "Weapon type",
        ["Any"] + weapon_types_here,
        help="Some classes can equip either of two weapon categories in the same slot, but only "
             "one actually grants its affixes when equipped — pick which one to build around.",
    )
    weapon_type = None if wt_choice == "Any" else wt_choice

with st.sidebar.expander("Lock specific gear (optional)"):
    st.caption("Force a specific item into a slot — useful for jewelry, where pieces with the same "
               "socket layout can differ in base stats. Still lets the solver pick gems.")
    locked_items: dict[str, str] = {}
    for slot in data.slots():
        slot_items = sorted(
            (g for g in data.gear if g.slot == slot and g.usable_by(class_req)),
            key=lambda g: g.name,
        )
        if not slot_items:
            continue
        options = ["Any"] + [g.slug for g in slot_items]
        labels = {"Any": "Any"} | {g.slug: f"{g.name} ({g.rarity})" for g in slot_items}
        choice = st.selectbox(slot, options, index=0, format_func=lambda s: labels[s], key=f"lock_{slot}")
        if choice != "Any":
            locked_items[slot] = choice

beverage_options = ["None"] + list(BEVERAGE_TIERS)
beverage_labels = {"None": "None"} | {
    t: f"{t} (budget {s['budget']}, up to +{s['max_per_affix']}/affix)" for t, s in BEVERAGE_TIERS.items()
}
beverage_choice = st.sidebar.selectbox(
    "Beverage", beverage_options, index=0, format_func=lambda t: beverage_labels[t]
)
beverage_tier = None if beverage_choice == "None" else beverage_choice

max_results = st.sidebar.number_input("Max builds to show", min_value=1, max_value=100, value=25)
run = st.sidebar.button("Calculate", type="primary", disabled=not targets)


def render_build(i: int, build: Build, target_slugs: set[str]) -> None:
    totals = build.total_affix_counts()
    header = " · ".join(f"{data.affixes_by_slug[a].name} {totals.get(a, 0)}" for a in target_slugs)
    with st.expander(f"Build {i}: {header}", expanded=(i == 1)):
        cols = st.columns(len(build.picks)) if build.picks else []
        for col, pick in zip(cols, build.picks):
            with col:
                st.markdown(f"**{pick.slot}**")
                st.write(pick.item.name)
                if pick.variant.affix_slug:
                    st.caption(f"Innate: {data.affixes_by_slug[pick.variant.affix_slug].name}")
                for sock in pick.sockets:
                    socket_label = f"{sock.socket.shape} T{sock.socket.tier}"
                    if sock.gem:
                        granted = ", ".join(data.affixes_by_slug[a].name for a in sock.gem.affix_slugs)
                        st.caption(f"Gem ({socket_label}): {sock.gem.name} → {granted}")
                    else:
                        st.caption(f"Empty socket ({socket_label})")

        if build.beverage_allocation:
            chips = ", ".join(
                f"+{n} {data.affixes_by_slug[a].name}" for a, n in build.beverage_allocation
            )
            st.caption(
                f"Beverage ({build.beverage_tier}): {chips} "
                f"— {build.beverage_points_remaining()} point(s) left unused"
            )

        st.divider()
        for slug in target_slugs:
            affix = data.affixes_by_slug[slug]
            have = totals.get(slug, 0)
            cap = affix.stack_cap or max(have, 1)
            st.progress(min(have / cap, 1.0), text=f"{affix.name}: {have}/{cap}")

        bonus = build.bonus_affixes(target_slugs)
        if bonus:
            chips = ", ".join(f"{data.affixes_by_slug[a].name} ×{n}" for a, n in bonus.items())
            st.caption(f"Bonus affixes: {chips}")


if run:
    with st.spinner("Searching…"):
        builds = find_builds(
            data, class_req, targets, max_results=int(max_results),
            beverage_tier=beverage_tier, allowed_rarities=allowed_rarities,
            locked_items=locked_items, weapon_type=weapon_type,
        )
    if not builds:
        st.error(
            "No feasible build found within the search budget. Either the targets aren't reachable "
            "with known gear/gems, the relevant items don't have socket data filled in yet, or the "
            "search hit its safety limit — try narrowing target affixes or gear rarity."
        )
    else:
        st.success(f"Found {len(builds)} feasible build(s) (capped at {max_results}).")
        for i, b in enumerate(builds, 1):
            render_build(i, b, set(targets))
else:
    st.info("Pick a class and at least one target affix, then hit Calculate.")
