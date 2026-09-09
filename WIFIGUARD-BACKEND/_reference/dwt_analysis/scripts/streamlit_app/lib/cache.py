"""
Cached data-loading and pipeline computation for the Streamlit app.

All cached functions are keyed on scalars/tuples only (never raw numpy arrays),
so repeated calls with the same parameters are cheap regardless of how large
the underlying arrays are.
"""

from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.dwt_coef.data_loader import get_file_index
from src.dwt_coef.preprocessing import _moving_variance, bandpass_filter, select_streams
from src.dwt_coef.selection_stats import run_batch_selection, sample_sessions
from src.dwt_coef.session_builder import build_session
from lib.plotting import build_realtime_animation


def _normalize_columns(x: np.ndarray) -> np.ndarray:
    """Per-column min-max normalization to [0, 1], for display purposes only."""
    if x.shape[1] == 0:
        return x
    col_min = x.min(axis=0, keepdims=True)
    col_max = x.max(axis=0, keepdims=True)
    col_range = col_max - col_min
    col_range[col_range < 1e-10] = 1.0
    return ((x - col_min) / col_range).astype(np.float32)


@st.cache_data(show_spinner=False)
def get_file_index_cached(mendeley_root: str) -> pd.DataFrame:
    return get_file_index(mendeley_root)


@st.cache_data(show_spinner="Merging session phases...")
def build_session_cached(
    mendeley_root: str,
    env: int,
    subject: int,
    class_id: int,
    trial: int,
    fs_hz: float,
    tolerance_ms: float,
    max_interp_gap_steps: int,
) -> Dict[str, Any]:
    file_index = get_file_index_cached(mendeley_root)
    return build_session(
        file_index, env, subject, class_id, trial,
        fs_hz=fs_hz, tolerance_ms=tolerance_ms, max_interp_gap_steps=max_interp_gap_steps,
    )


@st.cache_data(show_spinner="Computing subcarrier selection...")
def run_pipeline_cached(
    mendeley_root: str,
    env: int,
    subject: int,
    class_id: int,
    trial: int,
    fs_hz: float,
    tolerance_ms: float,
    max_interp_gap_steps: int,
    use_filter: bool,
    low_hz: float,
    high_hz: float,
    filter_order: int,
    omega: int,
    n_streams: int,
    use_ant: Optional[Tuple[int, ...]],
) -> Dict[str, Any]:
    """
    Full pipeline: merged session -> (optional bandpass filter) -> stream selection
    -> per-selected-stream moving variance.

    Returns the session dict plus:
        - "H_S": ndarray (N, n_selected) — selected stream amplitude
        - "H_S_norm": ndarray (N, n_selected) — per-stream min-max normalized to [0, 1]
        - "stream_stats": dict from select_streams()
        - "mv_per_stream": ndarray (N, n_selected) — moving variance per selected stream
        - "mv_per_stream_norm": ndarray (N, n_selected) — per-stream min-max normalized to [0, 1]
        - "filtered": bool
    """
    session = build_session_cached(
        mendeley_root, env, subject, class_id, trial, fs_hz, tolerance_ms, max_interp_gap_steps
    )

    amp = session["amplitude"]
    if use_filter:
        amp = bandpass_filter(amp, fs_hz, low_hz, high_hz, filter_order)

    use_ant_list = list(use_ant) if use_ant else None
    H_S, stream_stats = select_streams(amp, omega, n_streams, use_ant_list)

    n_selected = H_S.shape[1]
    if n_selected > 0:
        mv_per_stream = np.stack(
            [_moving_variance(H_S[:, i], omega) for i in range(n_selected)], axis=1
        ).astype(np.float32)
    else:
        mv_per_stream = np.zeros((H_S.shape[0], 0), dtype=np.float32)

    return {
        **session,
        "H_S": H_S,
        "H_S_norm": _normalize_columns(H_S),
        "stream_stats": stream_stats,
        "mv_per_stream": mv_per_stream,
        "mv_per_stream_norm": _normalize_columns(mv_per_stream),
        "filtered": use_filter,
    }


@st.cache_data(show_spinner="Precomputing realtime animation...")
def build_animation_cached(
    mendeley_root: str,
    env: int,
    subject: int,
    class_id: int,
    trial: int,
    fs_hz: float,
    tolerance_ms: float,
    max_interp_gap_steps: int,
    use_filter: bool,
    low_hz: float,
    high_hz: float,
    filter_order: int,
    omega: int,
    n_streams: int,
    use_ant: Optional[Tuple[int, ...]],
    window_sec: float,
    tick_sec: float,
    speed: float,
) -> go.Figure:
    """
    Precompute the entire Page 2 replay as one Plotly figure with baked-in animation
    frames, so playback runs client-side (Play/Pause/scrub) with no server round-trip
    per frame. Traces hold the FULL session once; each frame only moves the x-axis
    range and updates two small annotations (phase name, fall/normal badge) — not the
    trace data — which is what keeps per-frame payload tiny regardless of window size.
    """
    pipeline = run_pipeline_cached(
        mendeley_root, env, subject, class_id, trial, fs_hz, tolerance_ms, max_interp_gap_steps,
        use_filter, low_hz, high_hz, filter_order, omega, n_streams, use_ant,
    )
    return build_realtime_animation(pipeline, window_sec=window_sec, tick_sec=tick_sec, speed=speed)


@st.cache_data(show_spinner="Running batch selection across sampled sessions...")
def run_batch_selection_cached(
    mendeley_root: str,
    envs: Tuple[int, ...],
    class_ids: Tuple[int, ...],
    sessions_per_group: int,
    seed: int,
    fs_hz: float,
    tolerance_ms: float,
    max_interp_gap_steps: int,
    use_filter: bool,
    low_hz: float,
    high_hz: float,
    filter_order: int,
    omega: int,
    n_streams: int,
) -> pd.DataFrame:
    """
    Sample up to `sessions_per_group` sessions per (env, class_id) cell and run
    subcarrier selection (free + forced-single-antenna) across all of them.

    Always uses all 3 antennas for the "free" selection pass regardless of any
    Page 1 antenna restriction — restricting antennas here would bias away the very
    effect Page 3 studies.
    """
    file_index = get_file_index_cached(mendeley_root)
    sessions_df = sample_sessions(file_index, list(envs), list(class_ids), sessions_per_group, seed)
    return run_batch_selection(
        file_index, sessions_df, fs_hz, tolerance_ms, max_interp_gap_steps,
        use_filter, low_hz, high_hz, filter_order, omega, n_streams,
    )
