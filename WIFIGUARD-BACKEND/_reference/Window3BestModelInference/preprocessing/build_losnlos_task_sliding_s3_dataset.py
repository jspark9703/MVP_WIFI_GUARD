#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from amfall_losnlos_common import (
    DEFAULT_FS_HZ,
    FALL_ACTIVITIES,
    RX_COUNT,
    binary_to_index,
    discover_losnlos_csvs,
    load_rx_amplitude,
    load_timestamps,
    parse_losnlos_metadata,
    utc_now_iso,
)
from build_losnlos_sliding_s3_dataset import (
    compute_window_s3,
    format_eta,
    hard_negative,
    trial_block,
    valid_existing_feature,
    window_starts_no_tail,
)


FEATURE_FIELDS = [
    "sample_id",
    "task_id",
    "source_csv",
    "source_csvs",
    "feature_path",
    "status",
    "error",
    "env",
    "subject",
    "condition",
    "activity",
    "trial",
    "rx",
    "binary_label",
    "binary_label_idx",
    "multiclass_label",
    "multiclass_label_idx",
    "hard_negative",
    "trial_block",
    "task_activity_sequence",
    "window_activity_sequence",
    "window_activity_counts",
    "window_crosses_activity_boundary",
    "window_contains_fall",
    "window_fall_overlap_samples",
    "window_label_policy",
    "fs_hz",
    "segment_sec",
    "window_samples",
    "window_stride_sec",
    "stride_samples",
    "window_index",
    "window_start",
    "window_end",
    "window_start_sec",
    "window_end_sec",
    "task_window_count",
    "tail_dropped_samples",
    "source_file_count",
    "raw_rows",
    "resampled_rows",
    "interp_steps",
    "fallback_steps",
    "irregular_gaps",
    "nonpositive_gaps",
    "task_boundary_count",
    "task_boundary_gap_min_us",
    "task_boundary_gap_median_us",
    "task_boundary_gap_max_us",
    "task_boundary_duplicate_rows_dropped",
    "selected_stream_count",
    "selected_stream_indices",
    "selected_pc_count",
    "selected_pc_indices",
    "candidate_pc_count",
    "signal_q",
    "effective_freq_max_hz",
    "s0_nonzero_ratio",
    "s1_nonzero_ratio",
    "s2_nonzero_ratio",
    "s3_nonzero_ratio",
    "s3_shape",
    "feature_dtype",
]

SOURCE_FIELDS = [
    "task_id",
    "source_csvs",
    "env",
    "subject",
    "condition",
    "trial",
    "rx",
    "task_activity_sequence",
    "task_contains_fall",
    "status",
    "error",
    "source_file_count",
    "raw_rows",
    "resampled_rows",
    "window_count",
    "window_samples",
    "stride_samples",
    "window_stride_sec",
    "tail_dropped_samples",
    "interp_steps",
    "fallback_steps",
    "irregular_gaps",
    "nonpositive_gaps",
    "task_boundary_count",
    "task_boundary_gap_min_us",
    "task_boundary_gap_median_us",
    "task_boundary_gap_max_us",
    "task_boundary_duplicate_rows_dropped",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build S3 scalogram features from LOS/NLOS CSI by first joining "
            "activity CSVs that belong to the same continuous experiment trial "
            "(same E/S/C/T) and then applying fixed sliding windows."
        )
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("dataset/benchmark/LOS:NLOS Wi-Fi HAR dataset"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("dataset/processed/losnlos_s3_task_sliding3s_stride0p25"),
    )
    parser.add_argument("--fs-hz", type=float, default=DEFAULT_FS_HZ)
    parser.add_argument("--interp-tol-ms", type=float, default=2.0)
    parser.add_argument("--segment-sec", type=float, default=3.0)
    parser.add_argument("--window-stride-sec", type=float, default=0.25)
    parser.add_argument(
        "--label-policy",
        choices=("center", "majority", "any-fall"),
        default="center",
        help=(
            "How to assign a label to windows that cross activity CSV boundaries. "
            "center uses the activity at the window center, majority uses the "
            "dominant activity in the window, and any-fall marks a window as fall "
            "if it overlaps a fall activity."
        ),
    )
    parser.add_argument("--omega", type=int, default=30)
    parser.add_argument("--w-sec", type=float, default=0.4)
    parser.add_argument("--freq-min-hz", type=float, default=1.0)
    parser.add_argument("--freq-max-hz", type=float, default=170.0)
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--th-scmax", type=float, default=1.0)
    parser.add_argument("--kappa", type=float, default=1.0)
    parser.add_argument("--carrier-hz", type=float, default=2.4e9)
    parser.add_argument(
        "--feature-dtype",
        choices=("float32", "float16"),
        default="float32",
    )
    parser.add_argument(
        "--no-compression",
        action="store_true",
        help="Use np.savez instead of np.savez_compressed.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-task", type=int, default=None)
    parser.add_argument(
        "--num-workers",
        type=int,
        default=max(1, min(4, (os.cpu_count() or 2) - 1)),
    )
    return parser.parse_args()


def task_id_for(parts: list[Path]) -> str:
    meta = parse_losnlos_metadata(parts[0])
    return f"{meta.env}_{meta.subject}_{meta.condition}_{meta.trial}"


def sample_id_for(task_id: str, rx: int, window_index: int) -> str:
    return f"{task_id}_rx{rx}_w{window_index:04d}"


def feature_path_for(output_root: Path, task_id: str, rx: int, window_index: int) -> Path:
    return output_root / "features" / f"{sample_id_for(task_id, rx, window_index)}.npz"


def consecutive_unique(values: np.ndarray | list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value)
        if not out or out[-1] != text:
            out.append(text)
    return out


def activity_counts_text(values: np.ndarray) -> str:
    counts = Counter(str(value) for value in values)
    return ";".join(f"{activity}:{counts[activity]}" for activity in sorted(counts))


def activity_sequence_text(values: np.ndarray | list[str]) -> str:
    return ";".join(consecutive_unique(values))


def binary_label_for(activity: str) -> str:
    return "fall" if activity in FALL_ACTIVITIES else "non_fall"


def primary_activity_for_window(activity_window: np.ndarray, label_policy: str) -> str:
    center_activity = str(activity_window[len(activity_window) // 2])
    if label_policy == "center":
        return center_activity
    if label_policy == "any-fall":
        if center_activity in FALL_ACTIVITIES:
            return center_activity
        for activity in consecutive_unique(activity_window):
            if activity in FALL_ACTIVITIES:
                return activity
        return center_activity

    counts = Counter(str(value) for value in activity_window)
    max_count = max(counts.values())
    tied = {activity for activity, count in counts.items() if count == max_count}
    if center_activity in tied:
        return center_activity
    for activity in consecutive_unique(activity_window):
        if activity in tied:
            return activity
    return center_activity


def group_task_csvs(csv_paths: list[Path]) -> list[list[Path]]:
    groups: dict[tuple[str, str, str, str], list[Path]] = defaultdict(list)
    for path in csv_paths:
        meta = parse_losnlos_metadata(path)
        groups[(meta.env, meta.subject, meta.condition, meta.trial)].append(path)

    ordered_groups: list[list[Path]] = []
    for key in sorted(groups):
        parts = groups[key]
        parts.sort(key=lambda path: (float(load_timestamps(path)[0]), path.name))
        ordered_groups.append(parts)
    return ordered_groups


def boundary_gap_stats(boundary_gaps: list[float]) -> dict[str, Any]:
    if not boundary_gaps:
        return {
            "task_boundary_count": 0,
            "task_boundary_gap_min_us": "",
            "task_boundary_gap_median_us": "",
            "task_boundary_gap_max_us": "",
        }
    arr = np.asarray(boundary_gaps, dtype=np.float64)
    return {
        "task_boundary_count": int(len(boundary_gaps)),
        "task_boundary_gap_min_us": int(np.min(arr)),
        "task_boundary_gap_median_us": float(np.median(arr)),
        "task_boundary_gap_max_us": int(np.max(arr)),
    }


def load_task_amplitude(parts: list[Path], rx: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    timestamps_all: list[np.ndarray] = []
    amplitude_all: list[np.ndarray] = []
    activity_all: list[np.ndarray] = []
    source_csvs: list[str] = []
    boundary_gaps: list[float] = []
    duplicate_rows_dropped = 0
    raw_rows = 0
    previous_last_ts: float | None = None

    for index, path in enumerate(parts):
        meta = parse_losnlos_metadata(path)
        timestamps, amplitude = load_rx_amplitude(path, rx)
        raw_rows += int(len(timestamps))
        source_csvs.append(str(path))
        if index > 0 and previous_last_ts is not None and len(timestamps):
            boundary_gaps.append(float(timestamps[0] - previous_last_ts))

        if previous_last_ts is not None:
            drop_count = 0
            while drop_count < len(timestamps) and timestamps[drop_count] <= previous_last_ts:
                drop_count += 1
            duplicate_rows_dropped += drop_count
            if drop_count:
                timestamps = timestamps[drop_count:]
                amplitude = amplitude[drop_count:]
        if len(timestamps) == 0:
            continue

        timestamps_all.append(timestamps.astype(np.float64))
        amplitude_all.append(amplitude.astype(np.float32))
        activity_all.append(np.full(len(timestamps), meta.activity, dtype="<U3"))
        previous_last_ts = float(timestamps[-1])

    if not timestamps_all:
        return (
            np.asarray([], dtype=np.float64),
            np.empty((0, 30), dtype=np.float32),
            np.asarray([], dtype="<U3"),
            {
                "raw_rows": raw_rows,
                "source_file_count": len(parts),
                "source_csvs": ";".join(source_csvs),
                "task_boundary_duplicate_rows_dropped": duplicate_rows_dropped,
                **boundary_gap_stats(boundary_gaps),
            },
        )

    stats = {
        "raw_rows": raw_rows,
        "source_file_count": len(parts),
        "source_csvs": ";".join(source_csvs),
        "task_boundary_duplicate_rows_dropped": duplicate_rows_dropped,
        **boundary_gap_stats(boundary_gaps),
    }
    return (
        np.concatenate(timestamps_all).astype(np.float64),
        np.concatenate(amplitude_all, axis=0).astype(np.float32),
        np.concatenate(activity_all).astype("<U3"),
        stats,
    )


def resample_amplitude_with_activity(
    timestamps: np.ndarray,
    amplitude: np.ndarray,
    activities: np.ndarray,
    grid_us: float,
    tolerance_us: float,
    max_interp_gap_steps: int = 64,
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    if len(amplitude) == 0:
        return amplitude.astype(np.float32), activities.astype("<U3"), {
            "interp_steps": 0,
            "fallback_steps": 0,
            "irregular_gaps": 0,
            "nonpositive_gaps": 0,
        }

    rows = [amplitude[0].astype(np.float32)]
    labels = [str(activities[0])]
    interp_steps = 0
    fallback_steps = 0
    irregular_gaps = 0
    nonpositive_gaps = 0
    for idx in range(1, len(amplitude)):
        gap = timestamps[idx] - timestamps[idx - 1]
        current_activity = str(activities[idx])
        previous_activity = str(activities[idx - 1])
        if gap <= 0:
            rows.append(amplitude[idx].astype(np.float32))
            labels.append(current_activity)
            fallback_steps += 1
            nonpositive_gaps += 1
            continue

        nearest_steps = max(1, int(round(gap / grid_us)))
        can_reconstruct = (
            nearest_steps <= max_interp_gap_steps
            and abs(gap - nearest_steps * grid_us) <= tolerance_us
        )
        if can_reconstruct:
            for step in range(1, nearest_steps):
                alpha = step / nearest_steps
                interpolated = (1.0 - alpha) * amplitude[idx - 1] + alpha * amplitude[idx]
                rows.append(interpolated.astype(np.float32))
                labels.append(previous_activity if alpha < 0.5 else current_activity)
                interp_steps += 1
            rows.append(amplitude[idx].astype(np.float32))
            labels.append(current_activity)
        else:
            rows.append(amplitude[idx].astype(np.float32))
            labels.append(current_activity)
            fallback_steps += 1
            irregular_gaps += 1

    return np.stack(rows, axis=0).astype(np.float32), np.asarray(labels, dtype="<U3"), {
        "interp_steps": interp_steps,
        "fallback_steps": fallback_steps,
        "irregular_gaps": irregular_gaps,
        "nonpositive_gaps": nonpositive_gaps,
    }


def task_base(parts: list[Path], rx: int) -> dict[str, Any]:
    meta = parse_losnlos_metadata(parts[0])
    source_csvs = ";".join(str(path) for path in parts)
    activities = [parse_losnlos_metadata(path).activity for path in parts]
    task_sequence = activity_sequence_text(activities)
    return {
        "task_id": task_id_for(parts),
        "source_csv": str(parts[0]),
        "source_csvs": source_csvs,
        "env": meta.env,
        "subject": meta.subject,
        "condition": meta.condition,
        "trial": meta.trial,
        "rx": int(rx),
        "trial_block": trial_block(meta.trial),
        "task_activity_sequence": task_sequence,
        "task_contains_fall": int(any(activity in FALL_ACTIVITIES for activity in activities)),
    }


def source_row(
    *,
    base: dict[str, Any],
    status: str,
    error: str,
    source_stats: dict[str, Any],
    config: dict[str, Any],
    window_count: int,
    tail_dropped_samples: int,
) -> dict[str, Any]:
    return {
        "task_id": base["task_id"],
        "source_csvs": base["source_csvs"],
        "env": base["env"],
        "subject": base["subject"],
        "condition": base["condition"],
        "trial": base["trial"],
        "rx": base["rx"],
        "task_activity_sequence": base["task_activity_sequence"],
        "task_contains_fall": base["task_contains_fall"],
        "status": status,
        "error": error,
        "source_file_count": source_stats.get("source_file_count", ""),
        "raw_rows": source_stats.get("raw_rows", ""),
        "resampled_rows": source_stats.get("resampled_rows", ""),
        "window_count": int(window_count),
        "window_samples": int(config["window_samples"]),
        "stride_samples": int(config["stride_samples"]),
        "window_stride_sec": float(config["window_stride_sec"]),
        "tail_dropped_samples": int(tail_dropped_samples),
        "interp_steps": source_stats.get("interp_steps", ""),
        "fallback_steps": source_stats.get("fallback_steps", ""),
        "irregular_gaps": source_stats.get("irregular_gaps", ""),
        "nonpositive_gaps": source_stats.get("nonpositive_gaps", ""),
        "task_boundary_count": source_stats.get("task_boundary_count", ""),
        "task_boundary_gap_min_us": source_stats.get("task_boundary_gap_min_us", ""),
        "task_boundary_gap_median_us": source_stats.get("task_boundary_gap_median_us", ""),
        "task_boundary_gap_max_us": source_stats.get("task_boundary_gap_max_us", ""),
        "task_boundary_duplicate_rows_dropped": source_stats.get("task_boundary_duplicate_rows_dropped", ""),
    }


def feature_row(
    *,
    base: dict[str, Any],
    sample_id: str,
    feature_path: Path,
    status: str,
    error: str,
    config: dict[str, Any],
    source_stats: dict[str, Any],
    window_index: int,
    window_start: int,
    task_window_count: int,
    tail_dropped_samples: int,
    activity_window: np.ndarray,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stats = stats or {}
    window_samples = int(config["window_samples"])
    fs_hz = float(config["fs_hz"])
    primary_activity = primary_activity_for_window(activity_window, str(config["label_policy"]))
    if config["label_policy"] == "any-fall" and np.isin(activity_window, list(FALL_ACTIVITIES)).any():
        binary_label = "fall"
    else:
        binary_label = binary_label_for(primary_activity)
    fall_overlap_samples = int(np.sum(np.isin(activity_window, list(FALL_ACTIVITIES))))
    activity_sequence = activity_sequence_text(activity_window)
    activity_counts = activity_counts_text(activity_window)
    return {
        "sample_id": sample_id,
        "task_id": base["task_id"],
        "source_csv": base["source_csv"],
        "source_csvs": base["source_csvs"],
        "feature_path": str(feature_path),
        "status": status,
        "error": error,
        "env": base["env"],
        "subject": base["subject"],
        "condition": base["condition"],
        "activity": primary_activity,
        "trial": base["trial"],
        "rx": base["rx"],
        "binary_label": binary_label,
        "binary_label_idx": binary_to_index(binary_label),
        "multiclass_label": primary_activity,
        "multiclass_label_idx": int(primary_activity[1:]) - 1,
        "hard_negative": hard_negative(primary_activity),
        "trial_block": base["trial_block"],
        "task_activity_sequence": base["task_activity_sequence"],
        "window_activity_sequence": activity_sequence,
        "window_activity_counts": activity_counts,
        "window_crosses_activity_boundary": int(len(consecutive_unique(activity_window)) > 1),
        "window_contains_fall": int(fall_overlap_samples > 0),
        "window_fall_overlap_samples": fall_overlap_samples,
        "window_label_policy": config["label_policy"],
        "fs_hz": fs_hz,
        "segment_sec": float(config["segment_sec"]),
        "window_samples": window_samples,
        "window_stride_sec": float(config["window_stride_sec"]),
        "stride_samples": int(config["stride_samples"]),
        "window_index": int(window_index),
        "window_start": int(window_start),
        "window_end": int(window_start + window_samples),
        "window_start_sec": float(window_start / fs_hz),
        "window_end_sec": float((window_start + window_samples) / fs_hz),
        "task_window_count": int(task_window_count),
        "tail_dropped_samples": int(tail_dropped_samples),
        "source_file_count": source_stats.get("source_file_count", ""),
        "raw_rows": source_stats.get("raw_rows", ""),
        "resampled_rows": source_stats.get("resampled_rows", ""),
        "interp_steps": source_stats.get("interp_steps", ""),
        "fallback_steps": source_stats.get("fallback_steps", ""),
        "irregular_gaps": source_stats.get("irregular_gaps", ""),
        "nonpositive_gaps": source_stats.get("nonpositive_gaps", ""),
        "task_boundary_count": source_stats.get("task_boundary_count", ""),
        "task_boundary_gap_min_us": source_stats.get("task_boundary_gap_min_us", ""),
        "task_boundary_gap_median_us": source_stats.get("task_boundary_gap_median_us", ""),
        "task_boundary_gap_max_us": source_stats.get("task_boundary_gap_max_us", ""),
        "task_boundary_duplicate_rows_dropped": source_stats.get("task_boundary_duplicate_rows_dropped", ""),
        "selected_stream_count": stats.get("selected_stream_count", ""),
        "selected_stream_indices": stats.get("selected_stream_indices", ""),
        "selected_pc_count": stats.get("selected_pc_count", ""),
        "selected_pc_indices": stats.get("selected_pc_indices", ""),
        "candidate_pc_count": stats.get("candidate_pc_count", ""),
        "signal_q": stats.get("signal_q", ""),
        "effective_freq_max_hz": stats.get("effective_freq_max_hz", ""),
        "s0_nonzero_ratio": stats.get("s0_nonzero_ratio", ""),
        "s1_nonzero_ratio": stats.get("s1_nonzero_ratio", ""),
        "s2_nonzero_ratio": stats.get("s2_nonzero_ratio", ""),
        "s3_nonzero_ratio": stats.get("s3_nonzero_ratio", ""),
        "s3_shape": f"{int(config['image_size'])}x{int(config['image_size'])}",
        "feature_dtype": config["feature_dtype"],
    }


def process_one(parts_text: list[str], rx: int, output_root_text: str, config: dict[str, Any], resume: bool) -> dict[str, Any]:
    parts = [Path(text) for text in parts_text]
    output_root = Path(output_root_text)
    base = task_base(parts, rx)
    source_stats: dict[str, Any] = {}
    feature_rows: list[dict[str, Any]] = []
    try:
        timestamps, amplitude, activities, task_stats = load_task_amplitude(parts, rx)
        resampled, resampled_activities, resample_stats = resample_amplitude_with_activity(
            timestamps=timestamps,
            amplitude=amplitude,
            activities=activities,
            grid_us=1_000_000.0 / float(config["fs_hz"]),
            tolerance_us=float(config["interp_tol_ms"]) * 1000.0,
        )
        source_stats = {
            **task_stats,
            **resample_stats,
            "resampled_rows": int(len(resampled)),
        }
        starts = window_starts_no_tail(
            source_len=len(resampled),
            window_samples=int(config["window_samples"]),
            stride_samples=int(config["stride_samples"]),
        )
        if starts:
            tail_dropped = len(resampled) - (starts[-1] + int(config["window_samples"]))
        else:
            tail_dropped = len(resampled)
        source_summary = source_row(
            base=base,
            status="ok" if starts else "no_full_window",
            error="",
            source_stats=source_stats,
            config=config,
            window_count=len(starts),
            tail_dropped_samples=tail_dropped,
        )
        if not starts:
            return {"source_row": source_summary, "feature_rows": feature_rows}

        dtype = np.float16 if config["feature_dtype"] == "float16" else np.float32
        savez = np.savez if config["no_compression"] else np.savez_compressed
        task_id = base["task_id"]
        for window_index, window_start in enumerate(starts):
            sample_id = sample_id_for(task_id, rx, window_index)
            out_path = feature_path_for(output_root, task_id, rx, window_index)
            activity_window = resampled_activities[window_start : window_start + int(config["window_samples"])]
            row_base_kwargs = {
                "base": base,
                "sample_id": sample_id,
                "feature_path": out_path,
                "config": config,
                "source_stats": source_stats,
                "window_index": window_index,
                "window_start": window_start,
                "task_window_count": len(starts),
                "tail_dropped_samples": tail_dropped,
                "activity_window": activity_window,
            }
            if resume and valid_existing_feature(out_path, int(config["image_size"])):
                feature_rows.append(feature_row(status="skipped_existing", error="", **row_base_kwargs))
                continue

            window = resampled[window_start : window_start + int(config["window_samples"])]
            s3, stats = compute_window_s3(window, config)
            if s3.shape != (int(config["image_size"]), int(config["image_size"])):
                raise ValueError(f"unexpected S3 shape {s3.shape}")
            if not np.all(np.isfinite(s3)):
                raise ValueError("S3 contains NaN or Inf")
            row = feature_row(status="ok", error="", stats=stats, **row_base_kwargs)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = out_path.with_name(out_path.name + f".{os.getpid()}.tmp")
            with tmp_path.open("wb") as handle:
                savez(
                    handle,
                    s3=s3.astype(dtype),
                    sample_id=np.asarray(sample_id),
                    task_id=np.asarray(task_id),
                    source_csvs=np.asarray(base["source_csvs"]),
                    rx=np.asarray(rx, dtype=np.int32),
                    window_index=np.asarray(window_index, dtype=np.int32),
                    window_start=np.asarray(window_start, dtype=np.int32),
                    window_end=np.asarray(window_start + int(config["window_samples"]), dtype=np.int32),
                    window_start_sec=np.asarray(window_start / float(config["fs_hz"]), dtype=np.float32),
                    window_end_sec=np.asarray(
                        (window_start + int(config["window_samples"])) / float(config["fs_hz"]),
                        dtype=np.float32,
                    ),
                    fs_hz=np.asarray(float(config["fs_hz"]), dtype=np.float32),
                    window_stride_sec=np.asarray(float(config["window_stride_sec"]), dtype=np.float32),
                    binary_label_idx=np.asarray(row["binary_label_idx"], dtype=np.int32),
                    activity=np.asarray(row["activity"]),
                    window_activity_sequence=np.asarray(row["window_activity_sequence"]),
                    window_activity_counts=np.asarray(row["window_activity_counts"]),
                    window_contains_fall=np.asarray(row["window_contains_fall"], dtype=np.int32),
                    env=np.asarray(base["env"]),
                    subject=np.asarray(base["subject"]),
                    condition=np.asarray(base["condition"]),
                    trial=np.asarray(base["trial"]),
                )
            tmp_path.replace(out_path)
            feature_rows.append(row)
        return {"source_row": source_summary, "feature_rows": feature_rows}
    except Exception:
        return {
            "source_row": source_row(
                base=base,
                status="error",
                error=traceback.format_exc(limit=8),
                source_stats=source_stats,
                config=config,
                window_count=0,
                tail_dropped_samples=0,
            ),
            "feature_rows": feature_rows,
        }


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def main() -> None:
    args = parse_args()
    window_samples = int(round(args.segment_sec * args.fs_hz))
    stride_samples = int(round(args.window_stride_sec * args.fs_hz))
    if window_samples <= 0:
        raise SystemExit("--segment-sec and --fs-hz must produce a positive window length")
    if stride_samples <= 0:
        raise SystemExit("--window-stride-sec and --fs-hz must produce a positive stride")

    output_root = args.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    csv_paths = discover_losnlos_csvs(args.dataset_root)
    task_groups = group_task_csvs(csv_paths)
    if args.max_task is not None:
        task_groups = task_groups[: args.max_task]

    config = {
        "fs_hz": float(args.fs_hz),
        "interp_tol_ms": float(args.interp_tol_ms),
        "segment_sec": float(args.segment_sec),
        "window_stride_sec": float(args.window_stride_sec),
        "window_samples": int(window_samples),
        "stride_samples": int(stride_samples),
        "label_policy": str(args.label_policy),
        "omega": int(args.omega),
        "w_sec": float(args.w_sec),
        "freq_min_hz": float(args.freq_min_hz),
        "freq_max_hz": float(args.freq_max_hz),
        "image_size": int(args.image_size),
        "th_scmax": float(args.th_scmax),
        "kappa": float(args.kappa),
        "carrier_hz": float(args.carrier_hz),
        "feature_dtype": args.feature_dtype,
        "no_compression": bool(args.no_compression),
    }
    jobs = [
        ([str(path) for path in parts], rx, str(output_root), config, bool(args.resume))
        for parts in task_groups
        for rx in range(1, RX_COUNT + 1)
    ]
    feature_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    start_time = time.monotonic()

    def collect(index: int, result: dict[str, Any]) -> None:
        source_rows.append(result["source_row"])
        feature_rows.extend(result["feature_rows"])
        if index == 1 or index % 100 == 0 or index == len(jobs):
            elapsed = time.monotonic() - start_time
            rate = index / elapsed if elapsed > 0 else 0.0
            remaining = (len(jobs) - index) / rate if rate > 0 else float("nan")
            print(
                f"[{utc_now_iso()}] processed {index}/{len(jobs)} "
                f"rate={rate:.3f} task-rx/s eta={format_eta(remaining)} "
                f"features={len(feature_rows)}",
                flush=True,
            )

    if args.num_workers <= 1:
        for index, job in enumerate(jobs, start=1):
            collect(index, process_one(*job))
    else:
        with ProcessPoolExecutor(max_workers=args.num_workers) as executor:
            futures = [executor.submit(process_one, *job) for job in jobs]
            for index, future in enumerate(as_completed(futures), start=1):
                collect(index, future.result())

    source_rows.sort(key=lambda row: (row.get("task_id", ""), int(row.get("rx", 0))))
    feature_rows.sort(
        key=lambda row: (
            row.get("task_id", ""),
            int(row.get("rx", 0)),
            int(row.get("window_index", 0)),
        )
    )
    feature_manifest = output_root / "manifest_features.csv"
    source_manifest = output_root / "manifest_sources.csv"
    write_csv(feature_manifest, feature_rows, FEATURE_FIELDS)
    write_csv(source_manifest, source_rows, SOURCE_FIELDS)

    ok_rows = [row for row in feature_rows if row.get("status") in {"ok", "skipped_existing"}]
    label_counts: dict[str, int] = {}
    env_counts: dict[str, int] = {}
    activity_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    source_status_counts: dict[str, int] = {}
    boundary_crossing_count = 0
    contains_fall_count = 0
    for row in feature_rows:
        status_counts[str(row["status"])] = status_counts.get(str(row["status"]), 0) + 1
    for row in source_rows:
        source_status_counts[str(row["status"])] = source_status_counts.get(str(row["status"]), 0) + 1
    for row in ok_rows:
        label_counts[str(row["binary_label"])] = label_counts.get(str(row["binary_label"]), 0) + 1
        env_counts[str(row["env"])] = env_counts.get(str(row["env"]), 0) + 1
        activity_counts[str(row["activity"])] = activity_counts.get(str(row["activity"]), 0) + 1
        boundary_crossing_count += int(row["window_crosses_activity_boundary"])
        contains_fall_count += int(row["window_contains_fall"])

    summary = {
        "timestamp": utc_now_iso(),
        "dataset_root": str(args.dataset_root.resolve()),
        "output_root": str(output_root.resolve()),
        "manifest_features": str(feature_manifest),
        "manifest_sources": str(source_manifest),
        "config": config,
        "windowing_unit": "experiment_trial_task",
        "grouping_rule": "same env, subject, condition, and trial; activity CSVs sorted by timestamp_low start",
        "tail_policy": "drop_incomplete_tail_windows",
        "csv_count": len(csv_paths),
        "task_count": len(task_groups),
        "task_rx_count": len(source_rows),
        "sample_count": len(feature_rows),
        "ok_count": len(ok_rows),
        "feature_status_counts": status_counts,
        "source_status_counts": source_status_counts,
        "label_counts": label_counts,
        "activity_counts": activity_counts,
        "env_counts": env_counts,
        "boundary_crossing_window_count": boundary_crossing_count,
        "contains_fall_window_count": contains_fall_count,
        "compression": "np.savez" if args.no_compression else "np.savez_compressed",
    }
    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
