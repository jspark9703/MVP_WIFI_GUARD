#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from amfall_losnlos_common import (
    DEFAULT_FS_HZ,
    RX_COUNT,
    binary_to_index,
    compute_scalogram_stages,
    discover_losnlos_csvs,
    load_rx_amplitude,
    parse_losnlos_metadata,
    resample_amplitude,
    select_pc_signal,
    select_streams,
    utc_now_iso,
)


FEATURE_FIELDS = [
    "sample_id",
    "source_csv",
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
    "source_window_count",
    "tail_dropped_samples",
    "raw_rows",
    "resampled_rows",
    "interp_steps",
    "fallback_steps",
    "irregular_gaps",
    "nonpositive_gaps",
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
    "source_csv",
    "source_id",
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
    "status",
    "error",
    "raw_rows",
    "resampled_rows",
    "window_count",
    "window_samples",
    "stride_samples",
    "window_stride_sec",
    "tail_dropped_samples",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build S3 scalogram features from LOS/NLOS CSI with fixed 3s sliding "
            "windows and tail-dropping stride semantics."
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
        default=Path("dataset/processed/losnlos_s3_sliding3s_stride0p25"),
    )
    parser.add_argument("--fs-hz", type=float, default=DEFAULT_FS_HZ)
    parser.add_argument("--interp-tol-ms", type=float, default=2.0)
    parser.add_argument("--segment-sec", type=float, default=3.0)
    parser.add_argument("--window-stride-sec", type=float, default=0.25)
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
    parser.add_argument("--max-csv", type=int, default=None)
    parser.add_argument(
        "--num-workers",
        type=int,
        default=max(1, min(4, (os.cpu_count() or 2) - 1)),
    )
    return parser.parse_args()


def trial_block(trial: str) -> str:
    trial_num = int(trial[1:])
    if trial_num <= 6:
        return "early"
    if trial_num <= 13:
        return "mid"
    return "late"


def hard_negative(activity: str) -> int:
    return int(activity in {"A03", "A06", "A07", "A08", "A09", "A10", "A11", "A12"})


def window_starts_no_tail(source_len: int, window_samples: int, stride_samples: int) -> list[int]:
    if source_len < window_samples:
        return []
    stride_samples = max(1, int(stride_samples))
    last_full_start = source_len - window_samples
    return list(range(0, last_full_start + 1, stride_samples))


def format_eta(seconds: float) -> str:
    if not np.isfinite(seconds) or seconds < 0:
        return "unknown"
    seconds_i = int(round(seconds))
    hours, rem = divmod(seconds_i, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def sample_id_for(csv_path: Path, rx: int, window_index: int) -> str:
    return f"{parse_losnlos_metadata(csv_path).source_id}_rx{rx}_w{window_index:04d}"


def feature_path_for(output_root: Path, csv_path: Path, rx: int, window_index: int) -> Path:
    return output_root / "features" / f"{sample_id_for(csv_path, rx, window_index)}.npz"


def metadata_base(csv_path: Path, rx: int) -> dict[str, Any]:
    meta = parse_losnlos_metadata(csv_path)
    binary_label = meta.binary_label
    return {
        "source_csv": str(csv_path),
        "source_id": meta.source_id,
        "env": meta.env,
        "subject": meta.subject,
        "condition": meta.condition,
        "activity": meta.activity,
        "trial": meta.trial,
        "rx": int(rx),
        "binary_label": binary_label,
        "binary_label_idx": binary_to_index(binary_label),
        "multiclass_label": meta.activity,
        "multiclass_label_idx": int(meta.activity[1:]) - 1,
        "hard_negative": hard_negative(meta.activity),
        "trial_block": trial_block(meta.trial),
    }


def valid_existing_feature(path: Path, image_size: int) -> bool:
    if not path.exists():
        return False
    try:
        with np.load(path) as data:
            s3 = data["s3"]
            return s3.shape == (image_size, image_size) and bool(np.all(np.isfinite(s3)))
    except Exception:
        return False


def compute_window_s3(
    amplitude_window: np.ndarray,
    config: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    fs_hz = float(config["fs_hz"])
    w_radius = max(1, int(round(float(config["w_sec"]) * fs_hz)))
    selected_streams, stream_q = select_streams(
        amplitude_window,
        omega=int(config["omega"]),
        w_radius=w_radius,
    )
    signal, pc_stats = select_pc_signal(
        amplitude_window,
        selected_streams,
        w_radius=w_radius,
    )
    stages, cwt_stats = compute_scalogram_stages(
        signal=signal,
        fs_hz=fs_hz,
        freq_min_hz=float(config["freq_min_hz"]),
        freq_max_hz=float(config["freq_max_hz"]),
        image_size=int(config["image_size"]),
        th_scmax=float(config["th_scmax"]),
        kappa=float(config["kappa"]),
        carrier_hz=float(config["carrier_hz"]),
    )
    stats = {
        **pc_stats,
        **cwt_stats,
        "selected_stream_count": int(len(selected_streams)),
        "selected_stream_indices": ";".join(str(int(idx)) for idx in selected_streams),
        "stream_q_max": float(np.max(stream_q)) if len(stream_q) else 0.0,
        "stream_q_mean": float(np.mean(stream_q)) if len(stream_q) else 0.0,
    }
    return stages["s3"].astype(np.float32), stats


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
    source_window_count: int,
    tail_dropped_samples: int,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stats = stats or {}
    window_samples = int(config["window_samples"])
    stride_samples = int(config["stride_samples"])
    fs_hz = float(config["fs_hz"])
    row = {
        "sample_id": sample_id,
        "feature_path": str(feature_path),
        "status": status,
        "error": error,
        **base,
        "fs_hz": fs_hz,
        "segment_sec": float(config["segment_sec"]),
        "window_samples": window_samples,
        "window_stride_sec": float(config["window_stride_sec"]),
        "stride_samples": stride_samples,
        "window_index": int(window_index),
        "window_start": int(window_start),
        "window_end": int(window_start + window_samples),
        "window_start_sec": float(window_start / fs_hz),
        "window_end_sec": float((window_start + window_samples) / fs_hz),
        "source_window_count": int(source_window_count),
        "tail_dropped_samples": int(tail_dropped_samples),
        "raw_rows": source_stats.get("raw_rows", ""),
        "resampled_rows": source_stats.get("resampled_rows", ""),
        "interp_steps": source_stats.get("interp_steps", ""),
        "fallback_steps": source_stats.get("fallback_steps", ""),
        "irregular_gaps": source_stats.get("irregular_gaps", ""),
        "nonpositive_gaps": source_stats.get("nonpositive_gaps", ""),
        "feature_dtype": config["feature_dtype"],
    }
    row.update(
        {
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
        }
    )
    return row


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
        **base,
        "status": status,
        "error": error,
        "raw_rows": source_stats.get("raw_rows", ""),
        "resampled_rows": source_stats.get("resampled_rows", ""),
        "window_count": int(window_count),
        "window_samples": int(config["window_samples"]),
        "stride_samples": int(config["stride_samples"]),
        "window_stride_sec": float(config["window_stride_sec"]),
        "tail_dropped_samples": int(tail_dropped_samples),
    }


def process_one(csv_path_text: str, rx: int, output_root_text: str, config: dict[str, Any], resume: bool) -> dict[str, Any]:
    csv_path = Path(csv_path_text)
    output_root = Path(output_root_text)
    base = metadata_base(csv_path, rx)
    source_stats: dict[str, Any] = {}
    feature_rows: list[dict[str, Any]] = []
    try:
        timestamps, amplitude = load_rx_amplitude(csv_path, rx)
        resampled, resample_stats = resample_amplitude(
            timestamps=timestamps,
            amplitude=amplitude,
            grid_us=1_000_000.0 / float(config["fs_hz"]),
            tolerance_us=float(config["interp_tol_ms"]) * 1000.0,
        )
        source_stats = {
            **resample_stats,
            "raw_rows": int(len(amplitude)),
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
        for window_index, window_start in enumerate(starts):
            sample_id = sample_id_for(csv_path, rx, window_index)
            out_path = feature_path_for(output_root, csv_path, rx, window_index)
            if resume and valid_existing_feature(out_path, int(config["image_size"])):
                feature_rows.append(
                    feature_row(
                        base=base,
                        sample_id=sample_id,
                        feature_path=out_path,
                        status="skipped_existing",
                        error="",
                        config=config,
                        source_stats=source_stats,
                        window_index=window_index,
                        window_start=window_start,
                        source_window_count=len(starts),
                        tail_dropped_samples=tail_dropped,
                    )
                )
                continue

            window = resampled[window_start : window_start + int(config["window_samples"])]
            s3, stats = compute_window_s3(window, config)
            if s3.shape != (int(config["image_size"]), int(config["image_size"])):
                raise ValueError(f"unexpected S3 shape {s3.shape}")
            if not np.all(np.isfinite(s3)):
                raise ValueError("S3 contains NaN or Inf")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = out_path.with_name(out_path.name + f".{os.getpid()}.tmp")
            with tmp_path.open("wb") as handle:
                savez(
                    handle,
                    s3=s3.astype(dtype),
                    sample_id=np.asarray(sample_id),
                    source_csv=np.asarray(str(csv_path)),
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
                    binary_label_idx=np.asarray(base["binary_label_idx"], dtype=np.int32),
                    activity=np.asarray(base["activity"]),
                    env=np.asarray(base["env"]),
                    subject=np.asarray(base["subject"]),
                    condition=np.asarray(base["condition"]),
                    trial=np.asarray(base["trial"]),
                )
            tmp_path.replace(out_path)
            feature_rows.append(
                feature_row(
                    base=base,
                    sample_id=sample_id,
                    feature_path=out_path,
                    status="ok",
                    error="",
                    config=config,
                    source_stats=source_stats,
                    window_index=window_index,
                    window_start=window_start,
                    source_window_count=len(starts),
                    tail_dropped_samples=tail_dropped,
                    stats=stats,
                )
            )
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
    if args.max_csv is not None:
        csv_paths = csv_paths[: args.max_csv]

    config = {
        "fs_hz": float(args.fs_hz),
        "interp_tol_ms": float(args.interp_tol_ms),
        "segment_sec": float(args.segment_sec),
        "window_stride_sec": float(args.window_stride_sec),
        "window_samples": int(window_samples),
        "stride_samples": int(stride_samples),
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
        (str(csv_path), rx, str(output_root), config, bool(args.resume))
        for csv_path in csv_paths
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
                f"rate={rate:.3f} csv-rx/s eta={format_eta(remaining)} "
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

    source_rows.sort(
        key=lambda row: (
            row.get("source_csv", ""),
            int(row.get("rx", 0)),
        )
    )
    feature_rows.sort(
        key=lambda row: (
            row.get("source_csv", ""),
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
    status_counts: dict[str, int] = {}
    source_status_counts: dict[str, int] = {}
    for row in feature_rows:
        status_counts[str(row["status"])] = status_counts.get(str(row["status"]), 0) + 1
    for row in source_rows:
        source_status_counts[str(row["status"])] = source_status_counts.get(str(row["status"]), 0) + 1
    for row in ok_rows:
        label_counts[str(row["binary_label"])] = label_counts.get(str(row["binary_label"]), 0) + 1
        env_counts[str(row["env"])] = env_counts.get(str(row["env"]), 0) + 1

    summary = {
        "timestamp": utc_now_iso(),
        "dataset_root": str(args.dataset_root.resolve()),
        "output_root": str(output_root.resolve()),
        "manifest_features": str(feature_manifest),
        "manifest_sources": str(source_manifest),
        "config": config,
        "tail_policy": "drop_incomplete_tail_windows",
        "csv_count": len(csv_paths),
        "csv_rx_count": len(source_rows),
        "sample_count": len(feature_rows),
        "ok_count": len(ok_rows),
        "feature_status_counts": status_counts,
        "source_status_counts": source_status_counts,
        "label_counts": label_counts,
        "env_counts": env_counts,
        "compression": "np.savez" if args.no_compression else "np.savez_compressed",
    }
    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
