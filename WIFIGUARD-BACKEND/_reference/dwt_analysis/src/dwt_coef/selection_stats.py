"""
Batch subcarrier-selection sampling across many Mendeley sessions.

Supports statistical analysis of how antenna, environment, and experiment protocol
(class_id) affect select_streams()'s output, by running the selection pipeline across
a sample of reconstructed sessions rather than just one.
"""

from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from .preprocessing import bandpass_filter, select_streams
from .session_builder import build_session, list_valid_sessions

# Antenna restrictions used for the "forced single-antenna" comparison, in addition to
# the unrestricted ("free") run.
_FORCED_ANTENNA_MODES = [("ant0_only", (0,)), ("ant1_only", (1,)), ("ant2_only", (2,))]


def sample_sessions(
    file_index: pd.DataFrame,
    envs: List[int],
    class_ids: List[int],
    sessions_per_group: int,
    seed: int = 0,
) -> pd.DataFrame:
    """
    Draw up to `sessions_per_group` valid (subject, trial) sessions for each (env,
    class_id) in the cross product of envs x class_ids, for reproducible sampling.

    Args:
        file_index: DataFrame from data_loader.get_file_index()
        envs: Environments to include
        class_ids: Experiment classes (C) to include
        sessions_per_group: Max sessions to sample per (env, class_id) cell
        seed: Random seed for reproducible sampling

    Returns:
        DataFrame with columns: env, subject, class_id, trial
    """
    rng = np.random.default_rng(seed)
    rows = []

    for env in envs:
        subjects = sorted(file_index[file_index["env"] == env]["subject"].unique().tolist())
        for class_id in class_ids:
            candidates = []
            for subject in subjects:
                for trial in list_valid_sessions(file_index, env, subject, class_id):
                    candidates.append((subject, trial))

            if len(candidates) > sessions_per_group:
                idx = rng.choice(len(candidates), size=sessions_per_group, replace=False)
                candidates = [candidates[i] for i in idx]

            for subject, trial in candidates:
                rows.append({"env": env, "subject": subject, "class_id": class_id, "trial": trial})

    return pd.DataFrame(rows, columns=["env", "subject", "class_id", "trial"])


def run_batch_selection(
    file_index: pd.DataFrame,
    sessions_df: pd.DataFrame,
    fs_hz: float,
    tolerance_ms: float,
    max_interp_gap_steps: int,
    use_filter: bool,
    low_hz: float,
    high_hz: float,
    filter_order: int,
    omega: int,
    n_streams: int,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> pd.DataFrame:
    """
    Run subcarrier selection across many sessions, both freely (all antennas) and
    restricted to each single antenna, for statistical comparison.

    For each sampled session, build_session() runs once; select_streams() then runs
    once "free" (use_ant=None) and once per single antenna (use_ant=(0,)/(1,)/(2,)) —
    cheap additions since select_streams() is far cheaper than build_session().

    Args:
        file_index: DataFrame from data_loader.get_file_index()
        sessions_df: DataFrame from sample_sessions() with columns env, subject, class_id, trial
        fs_hz, tolerance_ms, max_interp_gap_steps: Resampling parameters
        use_filter, low_hz, high_hz, filter_order: Optional bandpass filter parameters
        omega, n_streams: Moving-variance / stream-selection parameters
        progress_callback: Optional callable(done, total) invoked after each session

    Returns:
        Long-format DataFrame, one row per (session, selection_mode, selected stream):
            env, subject, class_id, trial, selection_mode ("free" | "ant0_only" | "ant1_only" | "ant2_only"),
            antenna, subcarrier, q_value, Q_normalized, n_selected_total
    """
    total = len(sessions_df)
    records: List[Dict[str, Any]] = []

    for done, (_, row) in enumerate(sessions_df.iterrows(), start=1):
        env, subject, class_id, trial = int(row["env"]), int(row["subject"]), int(row["class_id"]), int(row["trial"])

        session = build_session(
            file_index, env, subject, class_id, trial,
            fs_hz=fs_hz, tolerance_ms=tolerance_ms, max_interp_gap_steps=max_interp_gap_steps,
        )
        amp = session["amplitude"]
        if use_filter:
            amp = bandpass_filter(amp, fs_hz, low_hz, high_hz, filter_order)

        modes = [("free", None)] + _FORCED_ANTENNA_MODES
        for mode_name, use_ant in modes:
            _, stats = select_streams(amp, omega, n_streams, list(use_ant) if use_ant else None)
            origins = stats.get("selected_stream_origins", [])
            q_values = stats.get("q_values_top", [])
            Q_normalized = stats.get("Q_values_normalized", [])
            n_selected_total = stats.get("selected_stream_count", 0)

            # select_streams()'s Q-threshold is applied to a q-descending-sorted list, so
            # the selected set is always a PREFIX of q_values_top/Q_values_normalized —
            # position j in `origins` lines up directly with position j in those arrays
            # (selected_stream_indices, by contrast, holds indices into the full candidate
            # pool and is not usable to index q_values_top/Q_values_normalized).
            for j in range(n_selected_total):
                antenna, subcarrier = origins[j]
                records.append({
                    "env": env, "subject": subject, "class_id": class_id, "trial": trial,
                    "selection_mode": mode_name,
                    "antenna": int(antenna), "subcarrier": int(subcarrier),
                    "q_value": float(q_values[j]),
                    "Q_normalized": float(Q_normalized[j]),
                    "n_selected_total": int(n_selected_total),
                })

        if progress_callback is not None:
            progress_callback(done, total)

    return pd.DataFrame(records)
