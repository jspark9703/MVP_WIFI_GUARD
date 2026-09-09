"""Shared Streamlit session_state schema and initialization."""

from typing import Any, Dict, Optional, Tuple

import streamlit as st

# Page 1 tuned preprocessing parameters (pp_ prefix)
PP_DEFAULTS: Dict[str, Any] = {
    "pp_env": 1,
    "pp_subject": 1,
    "pp_class_id": 1,
    "pp_trial": 1,
    "pp_fs_hz": 320.0,
    "pp_tolerance_ms": 2.0,
    "pp_max_interp_gap_steps": 64,
    "pp_use_filter": False,
    "pp_low_hz": 0.5,
    "pp_high_hz": 150.0,
    "pp_filter_order": 4,
    "pp_omega": 64,
    "pp_n_streams": 30,
    "pp_use_ant": None,  # None = all 3 antennas
}

# Page 2 playback state (rt_ prefix) — seeded from pp_* only on first visit to Page 2.
# Actual animation playback (play/pause/scrub) runs client-side in the precomputed
# Plotly figure, so only the session selection and display controls are tracked here.
RT_DEFAULTS: Dict[str, Any] = {
    "rt_env": None,
    "rt_subject": None,
    "rt_class_id": None,
    "rt_trial": None,
    "rt_speed": 1.0,
    "rt_window_sec": 5.0,
    "rt_seeded": False,
}


# Page 3 batch-statistics sampling controls (ss_ prefix)
SS_DEFAULTS: Dict[str, Any] = {
    "ss_envs": (1, 2, 3),
    "ss_class_ids": (1, 2, 3, 4, 5),
    "ss_sessions_per_group": 10,
    "ss_seed": 0,
    "ss_has_run": False,
}


def init_session_state() -> None:
    """Populate st.session_state with defaults for any keys not already set."""
    for key, value in {**PP_DEFAULTS, **RT_DEFAULTS, **SS_DEFAULTS}.items():
        if key not in st.session_state:
            st.session_state[key] = value


def seed_realtime_from_tuning() -> None:
    """Copy the Page 1 (pp_*) session identifiers into Page 2 (rt_*) state, once per session."""
    if st.session_state.get("rt_seeded"):
        return
    st.session_state["rt_env"] = st.session_state["pp_env"]
    st.session_state["rt_subject"] = st.session_state["pp_subject"]
    st.session_state["rt_class_id"] = st.session_state["pp_class_id"]
    st.session_state["rt_trial"] = st.session_state["pp_trial"]
    st.session_state["rt_seeded"] = True
