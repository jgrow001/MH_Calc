"""
Streamlit UI for the Mistfall Hunters build calculator.

    streamlit run app.py

Pick a class and a set of target affixes + stack levels; see every gear+gem
combination (within current data coverage) that reaches those targets, plus
whatever other affixes come along with each option.
"""
from __future__ import annotations

import streamlit as st

from model.entities import load_game_data
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
                    if sock.gem:
                        granted = ", ".join(data.affixes_by_slug[a].name for a in sock.gem.affix_slugs)
                        st.caption(f"Gem ({sock.shape}): {sock.gem.name} → {granted}")
                    else:
                        st.caption(f"Empty socket ({sock.shape})")

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
    builds = find_builds(data, class_req, targets, max_results=int(max_results))
    if not builds:
        st.error(
            "No feasible build found with current data. Either the targets aren't reachable "
            "with known gear/gems, or the relevant items don't have socket data filled in yet."
        )
    else:
        st.success(f"Found {len(builds)} feasible build(s) (capped at {max_results}).")
        for i, b in enumerate(builds, 1):
            render_build(i, b, set(targets))
else:
    st.info("Pick a class and at least one target affix, then hit Calculate.")
