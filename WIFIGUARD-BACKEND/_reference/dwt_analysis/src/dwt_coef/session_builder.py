"""
Mendeley Multi-Phase Session Reconstruction

The Mendeley dataset's "C" (class_id) field is a scripted multi-phase experiment
protocol (see Mendeley/Manuscript.pdf, Table 1): files sharing the same
(env, subject, class_id, trial) are sequential activity-phase segments of one
continuous physical recording, split into separate CSVs. This module reconstructs
that continuous recording by concatenating phases in the protocol-defined order.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .data_loader import load_csi_file
from .preprocessing import extract_amplitude, resample_signal

# Per Manuscript.pdf Table 1: activity phases in the order they were performed
# within one experiment-class trial (beep sounds mark phase transitions).
CLASS_ACTIVITY_ORDER: Dict[int, List[int]] = {
    1: [1, 2, 3],       # Falling from sitting: sit still -> falling down -> lie down
    2: [4, 5, 3],       # Falling from standing: stand still -> falling down -> lie down
    3: [6, 7, 8, 9],    # Walking: walk to receiver -> turn -> walk to transmitter -> turn
    4: [1, 10, 4, 11],  # Sit down/stand up: sit still -> standing up -> stand still -> sitting down
    5: [12],            # Pick a pen from the ground
}

ACTIVITY_NAME_MAP: Dict[int, str] = {
    1: "Sit still",
    2: "Falling down (from sit)",
    3: "Lie down",
    4: "Stand still",
    5: "Falling down (from stand)",
    6: "Walk to receiver",
    7: "Turning",
    8: "Walk to transmitter",
    9: "Turning",
    10: "Standing up",
    11: "Sitting down",
    12: "Pick pen from ground",
}


def get_session_files(
    file_index: pd.DataFrame,
    env: int,
    subject: int,
    class_id: int,
    trial: int,
) -> List[Tuple[int, Path]]:
    """
    Resolve the ordered list of phase files for one (env, subject, class_id, trial) session.

    Args:
        file_index: DataFrame from data_loader.get_file_index()
        env, subject, class_id, trial: Session identifiers

    Returns:
        List of (activity_id, filepath) tuples in CLASS_ACTIVITY_ORDER[class_id] order.

    Raises:
        ValueError: If class_id is unknown, or any required phase file is missing.
    """
    if class_id not in CLASS_ACTIVITY_ORDER:
        raise ValueError(f"Unknown class_id: {class_id} (expected one of {list(CLASS_ACTIVITY_ORDER)})")

    activity_order = CLASS_ACTIVITY_ORDER[class_id]

    subset = file_index[
        (file_index["env"] == env)
        & (file_index["subject"] == subject)
        & (file_index["class_id"] == class_id)
        & (file_index["trial"] == trial)
    ]

    files_by_activity = dict(zip(subset["activity"], subset["filepath"]))

    session_files = []
    missing = []
    for activity in activity_order:
        if activity in files_by_activity:
            session_files.append((activity, files_by_activity[activity]))
        else:
            missing.append(activity)

    if missing:
        raise ValueError(
            f"Missing phase file(s) for E{env}_S{subject:02d}_C{class_id:02d}_T{trial:02d}: "
            f"activities {missing}"
        )

    return session_files


def list_valid_sessions(
    file_index: pd.DataFrame,
    env: int,
    subject: int,
    class_id: int,
) -> List[int]:
    """
    List trial numbers for which ALL required phase files exist for (env, subject, class_id).

    Args:
        file_index: DataFrame from data_loader.get_file_index()
        env, subject, class_id: Session identifiers (trial not fixed)

    Returns:
        Sorted list of valid trial numbers.
    """
    if class_id not in CLASS_ACTIVITY_ORDER:
        return []

    activity_order = set(CLASS_ACTIVITY_ORDER[class_id])

    subset = file_index[
        (file_index["env"] == env)
        & (file_index["subject"] == subject)
        & (file_index["class_id"] == class_id)
    ]

    valid_trials = []
    for trial, group in subset.groupby("trial"):
        if activity_order.issubset(set(group["activity"])):
            valid_trials.append(int(trial))

    return sorted(valid_trials)


def build_session(
    file_index: pd.DataFrame,
    env: int,
    subject: int,
    class_id: int,
    trial: int,
    fs_hz: float = 320.0,
    tolerance_ms: float = 2.0,
    max_interp_gap_steps: int = 64,
) -> Dict[str, Any]:
    """
    Reconstruct one continuous multi-phase session by concatenating resampled amplitude
    from each activity phase, in protocol-defined order.

    Each phase is resampled to the fixed fs_hz grid independently before concatenation;
    the combined time axis is synthesized as arange(N_total) / fs_hz (phase transitions
    are treated as instantaneous — a documented simplification, not a bug).

    Args:
        file_index: DataFrame from data_loader.get_file_index()
        env, subject, class_id, trial: Session identifiers
        fs_hz: Target sampling frequency for resampling
        tolerance_ms: Resampling tolerance (see preprocessing.resample_signal)
        max_interp_gap_steps: Max interpolation steps (see preprocessing.resample_signal)

    Returns:
        {
            "env", "subject", "class_id", "trial": int,
            "fs_hz": float,
            "amplitude": ndarray (N, 3, 30) float32,
            "time_sec": ndarray (N,) float64,
            "source_files": List[Path] (in playback order),
            "phase_boundaries": [
                {"activity": int, "activity_name": str, "is_fall": bool,
                 "start_idx": int, "end_idx": int, "start_sec": float, "end_sec": float,
                 "n_samples": int},
                ...
            ],
        }
    """
    session_files = get_session_files(file_index, env, subject, class_id, trial)

    amplitude_chunks = []
    phase_boundaries = []
    source_files = []
    cursor = 0

    for activity, filepath in session_files:
        sample = load_csi_file(filepath)
        amp, ts = extract_amplitude(sample["csi"], sample["timestamps"])
        amp_resampled, _ = resample_signal(ts, amp, fs_hz, tolerance_ms, max_interp_gap_steps)

        n_samples = amp_resampled.shape[0]
        start_idx = cursor
        end_idx = cursor + n_samples

        phase_boundaries.append({
            "activity": int(activity),
            "activity_name": ACTIVITY_NAME_MAP.get(activity, f"A{activity:02d}"),
            "is_fall": activity in (2, 5),
            "start_idx": start_idx,
            "end_idx": end_idx,
            "start_sec": start_idx / fs_hz,
            "end_sec": end_idx / fs_hz,
            "n_samples": n_samples,
        })

        amplitude_chunks.append(amp_resampled)
        source_files.append(filepath)
        cursor = end_idx

    amplitude = np.concatenate(amplitude_chunks, axis=0).astype(np.float32)
    time_sec = np.arange(amplitude.shape[0], dtype=np.float64) / fs_hz

    return {
        "env": int(env),
        "subject": int(subject),
        "class_id": int(class_id),
        "trial": int(trial),
        "fs_hz": float(fs_hz),
        "amplitude": amplitude,
        "time_sec": time_sec,
        "source_files": source_files,
        "phase_boundaries": phase_boundaries,
    }
