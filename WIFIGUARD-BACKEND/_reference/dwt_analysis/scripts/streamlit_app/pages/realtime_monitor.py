"""Page 2: Realtime Monitor — replay a merged session as dummy real-time CSI data using Page 1's tuned parameters."""

import streamlit as st

from lib.cache import build_animation_cached, get_file_index_cached
from lib.state_defaults import seed_realtime_from_tuning
from src.dwt_coef.session_builder import list_valid_sessions

st.title("2. Realtime Monitor (Demo)")
st.caption(
    "Replays a merged multi-phase Mendeley session as dummy real-time data (normalized "
    "per stream to [0, 1]), using the moving-variance / subcarrier-selection parameters "
    "tuned on Page 1. Subcarrier selection is computed once over the WHOLE session before "
    "playback starts — a simplification for this demo, not a fully causal real-time "
    "computation. The whole replay is precomputed up front; Play/Pause/scrub below run "
    "entirely in the browser, so playback doesn't stutter waiting on the server."
)

seed_realtime_from_tuning()

mendeley_root = st.session_state["mendeley_root"]
file_index = get_file_index_cached(mendeley_root)

with st.sidebar:
    st.header("Session to Replay")

    envs = sorted(file_index["env"].unique().tolist())
    env_default = st.session_state["rt_env"] if st.session_state["rt_env"] in envs else envs[0]
    env = st.selectbox("Environment", envs, index=envs.index(env_default), key="rt_env_widget")

    subjects = sorted(file_index[file_index["env"] == env]["subject"].unique().tolist())
    subject_default = st.session_state["rt_subject"] if st.session_state["rt_subject"] in subjects else subjects[0]
    subject = st.selectbox("Subject", subjects, index=subjects.index(subject_default), key="rt_subject_widget")

    class_ids = sorted(
        file_index[(file_index["env"] == env) & (file_index["subject"] == subject)]["class_id"].unique().tolist()
    )
    class_default = st.session_state["rt_class_id"] if st.session_state["rt_class_id"] in class_ids else class_ids[0]
    class_id = st.selectbox(
        "Class (C)", class_ids, index=class_ids.index(class_default), format_func=lambda c: f"C{c:02d}",
        key="rt_class_id_widget",
    )

    valid_trials = list_valid_sessions(file_index, env, subject, class_id)
    if not valid_trials:
        st.error(f"No complete sessions found for E{env} S{subject} C{class_id}.")
        st.stop()
    trial_default = st.session_state["rt_trial"] if st.session_state["rt_trial"] in valid_trials else valid_trials[0]
    trial = st.selectbox("Trial", valid_trials, index=valid_trials.index(trial_default), key="rt_trial_widget")

    st.session_state.update({"rt_env": env, "rt_subject": subject, "rt_class_id": class_id, "rt_trial": trial})

    st.divider()
    st.header("Playback")
    st.session_state["rt_speed"] = st.slider(
        "Speed multiplier", 0.25, 8.0, float(st.session_state["rt_speed"]), step=0.25,
    )
    st.session_state["rt_window_sec"] = st.slider(
        "Trailing window (s)", 1.0, 30.0, float(st.session_state["rt_window_sec"]), step=1.0,
    )

    st.caption("Tuned parameters in effect (from Page 1):")
    st.json({
        "omega": st.session_state["pp_omega"],
        "n_streams": st.session_state["pp_n_streams"],
        "use_ant": st.session_state["pp_use_ant"],
        "filter": st.session_state["pp_use_filter"],
    }, expanded=False)

try:
    animation_fig = build_animation_cached(
        mendeley_root, env, subject, class_id, trial,
        st.session_state["pp_fs_hz"], st.session_state["pp_tolerance_ms"],
        int(st.session_state["pp_max_interp_gap_steps"]),
        st.session_state["pp_use_filter"], st.session_state["pp_low_hz"], st.session_state["pp_high_hz"],
        int(st.session_state["pp_filter_order"]), int(st.session_state["pp_omega"]),
        int(st.session_state["pp_n_streams"]), st.session_state["pp_use_ant"],
        float(st.session_state["rt_window_sec"]), 0.1, float(st.session_state["rt_speed"]),
    )
except ValueError as e:
    st.error(str(e))
    st.stop()

st.subheader(f"Session E{env}_S{subject:02d}_C{class_id:02d}_T{trial:02d}")
st.plotly_chart(animation_fig, width='stretch')
