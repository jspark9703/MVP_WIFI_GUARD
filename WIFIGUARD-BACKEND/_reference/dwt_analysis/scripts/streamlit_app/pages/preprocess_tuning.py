"""Page 1: Preprocess Tuning — resample -> (optional filter) -> moving-variance -> q-value -> subcarrier selection."""

import streamlit as st

from lib.cache import get_file_index_cached, run_pipeline_cached
from lib.plotting import plot_moving_variance, plot_q_value_bar, plot_selected_streams
from src.dwt_coef.session_builder import list_valid_sessions

st.title("1. Preprocess Tuning")
st.caption(
    "Tune resample -> (optional filter) -> moving-variance -> q-value -> subcarrier-selection "
    "parameters against a reconstructed multi-phase Mendeley session, and see which subcarriers "
    "get selected."
)

mendeley_root = st.session_state["mendeley_root"]
file_index = get_file_index_cached(mendeley_root)

with st.sidebar:
    st.header("Session")

    envs = sorted(file_index["env"].unique().tolist())
    env_default = st.session_state["pp_env"] if st.session_state["pp_env"] in envs else envs[0]
    env = st.selectbox("Environment", envs, index=envs.index(env_default))

    subjects = sorted(file_index[file_index["env"] == env]["subject"].unique().tolist())
    subject_default = st.session_state["pp_subject"] if st.session_state["pp_subject"] in subjects else subjects[0]
    subject = st.selectbox("Subject", subjects, index=subjects.index(subject_default))

    class_ids = sorted(
        file_index[(file_index["env"] == env) & (file_index["subject"] == subject)]["class_id"].unique().tolist()
    )
    class_default = st.session_state["pp_class_id"] if st.session_state["pp_class_id"] in class_ids else class_ids[0]
    class_id = st.selectbox(
        "Class (C) — experiment protocol", class_ids, index=class_ids.index(class_default),
        format_func=lambda c: f"C{c:02d}",
    )

    valid_trials = list_valid_sessions(file_index, env, subject, class_id)
    if not valid_trials:
        st.error(f"No complete sessions found for E{env} S{subject} C{class_id}.")
        st.stop()
    trial_default = st.session_state["pp_trial"] if st.session_state["pp_trial"] in valid_trials else valid_trials[0]
    trial = st.selectbox("Trial", valid_trials, index=valid_trials.index(trial_default))

    st.divider()
    st.header("Resampling")
    fs_hz = st.number_input(
        "fs_hz (sampling rate)", value=float(st.session_state["pp_fs_hz"]),
        min_value=50.0, max_value=1000.0, step=10.0,
        help="Hardware-nominal sampling rate; rarely changed.",
    )
    tolerance_ms = st.number_input(
        "tolerance_ms", value=float(st.session_state["pp_tolerance_ms"]),
        min_value=0.1, max_value=50.0, step=0.5,
    )
    max_interp_gap_steps = st.number_input(
        "max_interp_gap_steps", value=int(st.session_state["pp_max_interp_gap_steps"]),
        min_value=1, max_value=500, step=1,
    )

    st.divider()
    st.header("Bandpass Filter (optional)")
    use_filter = st.checkbox("Enable bandpass filter", value=bool(st.session_state["pp_use_filter"]))
    if use_filter:
        low_hz = st.number_input(
            "low_hz", value=float(st.session_state["pp_low_hz"]), min_value=0.01, max_value=100.0, step=0.1,
        )
        high_hz = st.number_input(
            "high_hz", value=float(st.session_state["pp_high_hz"]), min_value=1.0, max_value=160.0, step=1.0,
        )
        filter_order = st.number_input(
            "filter_order", value=int(st.session_state["pp_filter_order"]), min_value=1, max_value=10, step=1,
        )
    else:
        low_hz = st.session_state["pp_low_hz"]
        high_hz = st.session_state["pp_high_hz"]
        filter_order = st.session_state["pp_filter_order"]

    st.divider()
    st.header("Moving Variance & Subcarrier Selection")
    omega = st.slider(
        "omega (moving-variance window half-width)", min_value=8, max_value=256,
        value=int(st.session_state["pp_omega"]), step=1,
    )
    n_streams = st.slider(
        "n_streams (top-N candidate pool)", min_value=1, max_value=90,
        value=int(st.session_state["pp_n_streams"]), step=1,
    )

    ant_options = [1, 2, 3]
    current_ant = st.session_state["pp_use_ant"]
    current_ant_labels = ant_options if current_ant is None else [a + 1 for a in current_ant]
    selected_ant_labels = st.multiselect("Antennas to use", ant_options, default=current_ant_labels)
    if not selected_ant_labels:
        st.warning("Select at least one antenna.")
        st.stop()
    use_ant = tuple(a - 1 for a in sorted(selected_ant_labels))

st.session_state.update({
    "pp_env": env, "pp_subject": subject, "pp_class_id": class_id, "pp_trial": trial,
    "pp_fs_hz": fs_hz, "pp_tolerance_ms": tolerance_ms, "pp_max_interp_gap_steps": max_interp_gap_steps,
    "pp_use_filter": use_filter, "pp_low_hz": low_hz, "pp_high_hz": high_hz, "pp_filter_order": filter_order,
    "pp_omega": omega, "pp_n_streams": n_streams, "pp_use_ant": use_ant,
})

try:
    pipeline = run_pipeline_cached(
        mendeley_root, env, subject, class_id, trial,
        fs_hz, tolerance_ms, int(max_interp_gap_steps),
        use_filter, low_hz, high_hz, int(filter_order),
        int(omega), int(n_streams), use_ant,
    )
except ValueError as e:
    st.error(str(e))
    st.stop()

st.subheader(f"Session E{env}_S{subject:02d}_C{class_id:02d}_T{trial:02d}")
st.caption(
    f"Duration: {pipeline['time_sec'][-1]:.2f}s across {len(pipeline['phase_boundaries'])} phases "
    f"| Selected {pipeline['stream_stats']['selected_stream_count']} of "
    f"{pipeline['stream_stats']['candidate_stream_count']} candidate streams "
    f"({'filtered' if pipeline['filtered'] else 'unfiltered'})"
)

st.plotly_chart(plot_selected_streams(pipeline), width='stretch')
st.plotly_chart(plot_q_value_bar(pipeline["stream_stats"]), width='stretch')
st.plotly_chart(plot_moving_variance(pipeline), width='stretch')

with st.expander("Stream selection stats"):
    st.json(pipeline["stream_stats"])

with st.expander("Phase boundaries"):
    st.dataframe(pipeline["phase_boundaries"], width='stretch')
