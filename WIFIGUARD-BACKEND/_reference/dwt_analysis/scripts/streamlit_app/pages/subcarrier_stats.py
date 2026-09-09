"""Page 3: Subcarrier Selection Statistics — how antenna, environment, and experiment
protocol affect which subcarriers select_streams() picks, across a sample of sessions."""

import streamlit as st

from lib.cache import get_file_index_cached, run_batch_selection_cached
from lib.stats_analysis import categorical_chisq, cramers_v_matrix, forced_antenna_friedman, q_value_kruskal
from lib.stats_plotting import (
    plot_antenna_group_bar, plot_cramers_v_heatmap, plot_forced_antenna_box, plot_group_subcarrier_heatmap,
    plot_q_value_box,
)

st.title("3. Subcarrier Selection Statistics")
st.caption(
    "Runs subcarrier selection across a SAMPLE of Mendeley sessions (not just one) to check "
    "whether antenna, environment, or experiment protocol systematically affect which "
    "subcarriers select_streams() picks. Reuses Page 1's tuned omega/n_streams/filter "
    "settings, but always considers all 3 antennas for the 'free' selection pass — "
    "restricting antennas here would bias away the very effect being studied."
)

mendeley_root = st.session_state["mendeley_root"]
file_index = get_file_index_cached(mendeley_root)

all_envs = sorted(file_index["env"].unique().tolist())
all_class_ids = sorted(file_index["class_id"].unique().tolist())

with st.sidebar:
    st.header("Sampling")
    envs = st.multiselect("Environments", all_envs, default=list(st.session_state["ss_envs"]))
    class_ids = st.multiselect(
        "Experiment classes (C)", all_class_ids, default=list(st.session_state["ss_class_ids"]),
        format_func=lambda c: f"C{c:02d}",
    )
    sessions_per_group = st.slider(
        "Sessions per (env, class) group", 2, 50, int(st.session_state["ss_sessions_per_group"]), step=1,
    )
    seed = st.number_input("Random seed", value=int(st.session_state["ss_seed"]), step=1)

    st.session_state.update({
        "ss_envs": tuple(envs), "ss_class_ids": tuple(class_ids),
        "ss_sessions_per_group": sessions_per_group, "ss_seed": seed,
    })

    n_groups = len(envs) * len(class_ids)
    est_sessions = n_groups * sessions_per_group
    est_seconds = est_sessions * 0.9
    st.caption(
        f"{n_groups} group(s) x {sessions_per_group} sessions = up to {est_sessions} sessions "
        f"(~{est_seconds:.0f}s estimated on first run; instant on a cache hit)."
    )

    st.caption("Reused from Page 1 (fixed here — edit on Page 1 to change):")
    st.json({
        "omega": st.session_state["pp_omega"],
        "n_streams": st.session_state["pp_n_streams"],
        "filter": st.session_state["pp_use_filter"],
    }, expanded=False)

    run_clicked = st.button("Run batch analysis", type="primary", width='stretch')

if run_clicked:
    st.session_state["ss_has_run"] = True

if not envs or not class_ids:
    st.warning("Select at least one environment and one experiment class.")
    st.stop()

if not st.session_state["ss_has_run"]:
    st.info("Configure sampling in the sidebar, then click **Run batch analysis**.")
    st.stop()

df = run_batch_selection_cached(
    mendeley_root, tuple(sorted(envs)), tuple(sorted(class_ids)), int(sessions_per_group), int(seed),
    st.session_state["pp_fs_hz"], st.session_state["pp_tolerance_ms"],
    int(st.session_state["pp_max_interp_gap_steps"]),
    st.session_state["pp_use_filter"], st.session_state["pp_low_hz"], st.session_state["pp_high_hz"],
    int(st.session_state["pp_filter_order"]), int(st.session_state["pp_omega"]),
    int(st.session_state["pp_n_streams"]),
)

if df.empty:
    st.warning("No streams were selected across the sampled sessions — try different parameters.")
    st.stop()

free = df[df["selection_mode"] == "free"]
n_sessions = df.drop_duplicates(["env", "subject", "class_id", "trial"]).shape[0]

st.subheader("Summary")
st.caption(
    f"{n_sessions} sessions sampled across {len(envs)} environment(s) x {len(class_ids)} "
    f"experiment class(es). Note: environments have disjoint subject pools per the Mendeley "
    f"protocol (E1=S1-10, E2=S11-20, E3=S21-30), so environment and subject identity are "
    f"confounded here — differences by environment can't be fully separated from differences "
    f"between the specific people recorded in each one."
)

def _render_chisq_caption(result):
    if result["chi2"] is not None:
        st.caption(
            f"Chi-square: chi2={result['chi2']:.2f}, df={result['dof']}, "
            f"p={result['p_value']:.4g}, Cramér's V={result['cramers_v']:.3f}"
        )
    else:
        st.caption("Not enough groups for a chi-square test.")


st.subheader("Antenna vs. Environment / Protocol")
col1, col2 = st.columns(2)
with col1:
    st.plotly_chart(plot_antenna_group_bar(free, "env"), width='stretch')
    _render_chisq_caption(categorical_chisq(free, "antenna", "env"))
with col2:
    st.plotly_chart(plot_antenna_group_bar(free, "class_id"), width='stretch')
    _render_chisq_caption(categorical_chisq(free, "antenna", "class_id"))

st.subheader("Subcarrier vs. Environment / Protocol / Antenna")
st.caption(
    "Does WHICH subcarrier gets selected (not just which antenna) depend on environment, "
    "protocol, or antenna?"
)
col_sc1, col_sc2, col_sc3 = st.columns(3)
for col, group_col in [(col_sc1, "env"), (col_sc2, "class_id"), (col_sc3, "antenna")]:
    with col:
        st.plotly_chart(plot_group_subcarrier_heatmap(free, group_col), width='stretch')
        _render_chisq_caption(categorical_chisq(free, "subcarrier", group_col))

st.subheader("Correlation Between Factors (Cramér's V)")
st.caption(
    "Pairwise association strength among env / class_id / antenna / subcarrier (all "
    "categorical, so Cramér's V stands in for a correlation coefficient — 0 = independent, "
    "1 = perfectly associated)."
)
matrix = cramers_v_matrix(free, ["env", "class_id", "antenna", "subcarrier"])
st.plotly_chart(plot_cramers_v_heatmap(matrix), width='stretch')

st.subheader("q(h) Distribution")
col3, col4, col5 = st.columns(3)
for col, group_col in [(col3, "antenna"), (col4, "env"), (col5, "class_id")]:
    with col:
        st.plotly_chart(plot_q_value_box(free, group_col), width='stretch')
        r = q_value_kruskal(free, group_col)
        if r["statistic"] is not None:
            st.caption(f"Kruskal-Wallis: H={r['statistic']:.2f}, p={r['p_value']:.4g} (n={r['n_groups']} groups)")
        else:
            st.caption("Not enough groups for a Kruskal-Wallis test.")

st.subheader("Forced Single-Antenna Comparison")
st.caption(
    "Same sessions, selection restricted to one antenna at a time — a controlled comparison "
    "independent of which antenna free selection happens to prefer."
)
st.plotly_chart(plot_forced_antenna_box(df), width='stretch')
r_friedman = forced_antenna_friedman(df)
if r_friedman["statistic"] is not None:
    st.caption(
        f"Friedman test (paired by session): chi2={r_friedman['statistic']:.2f}, "
        f"p={r_friedman['p_value']:.4g} (n={r_friedman['n_sessions']} sessions with data in all 3 conditions)"
    )
else:
    st.caption("Not enough paired sessions for a Friedman test.")

with st.expander("Raw batch results"):
    st.dataframe(df, width='stretch')
