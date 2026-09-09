#!/usr/bin/env python3
"""
CSI Preprocessing Pipeline (Windowing + Per-Segment Processing)

Applies windowing and per-segment preprocessing to Mendeley CSI dataset:
1. Segment each recording (3s window, 0.25s stride, timestamp-based)
2. For each segment:
   - Resample to regular grid (linear interpolation)
   - Apply bandpass filter (0.5-150 Hz)
   - Select streams globally (90 → n_streams, with Q-threshold filtering)
   - Select PCs by q(p) metric (max 3 PCs)
   - Sum selected PCs → normalized 1D representative signal

Usage:
    python scripts/preprocess.py --config config/preprocess.yaml
    python scripts/preprocess.py --config config/preprocess.yaml --max-files 5
"""

import argparse
import csv
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yaml
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dwt_coef.data_loader import get_file_index, load_csi_file
from src.dwt_coef.preprocessing import sliding_window_raw, preprocess_segment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CSI preprocessing pipeline (windowing + per-segment)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/preprocess.yaml"),
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Limit number of files to process (override config)",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> Dict[str, Any]:
    """Load YAML config file."""
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config


def filter_dataframe(
    df: pd.DataFrame,
    env: Optional[List[int]] = None,
    subjects: Optional[List[int]] = None,
    activities: Optional[List[int]] = None,
    fall_only: bool = False,
) -> pd.DataFrame:
    """Filter file index DataFrame by criteria."""
    result = df.copy()

    if env is not None:
        result = result[result["env"].isin(env)]

    if subjects is not None:
        result = result[result["subject"].isin(subjects)]

    if activities is not None:
        result = result[result["activity"].isin(activities)]

    if fall_only:
        result = result[result["is_fall"] == 1]

    return result.reset_index(drop=True)


def build_manifest_row(
    filepath: Path,
    env: int,
    subject: int,
    class_id: int,
    activity: int,
    trial: int,
    is_fall: bool,
    n_segments: int,
    stats_list: List[Dict[str, Any]],
    status: str = "ok",
    error_msg: str = "",
) -> Dict[str, Any]:
    """Build a row for manifest.csv."""
    if n_segments == 0 or len(stats_list) == 0:
        return {
            "filepath": str(filepath),
            "env": int(env),
            "subject": int(subject),
            "class_id": int(class_id),
            "activity": int(activity),
            "trial": int(trial),
            "is_fall": int(is_fall),
            "raw_samples": 0,
            "n_segments": 0,
            "mean_n_pcs": 0.0,
            "mean_qh": 0.0,
            "status": status,
            "error_msg": error_msg,
        }

    n_pcs_list = [s.get("selected_pc_count", 0) for s in stats_list]
    mean_n_pcs = float(np.mean(n_pcs_list)) if n_pcs_list else 0.0

    qh_list = []
    for s in stats_list:
        # New format: q_values stored in q_values_top (top N candidates)
        q_values_top = s.get("q_values_top", [])
        if q_values_top:
            qh_list.extend(q_values_top)
    mean_qh = float(np.mean(qh_list)) if qh_list else 0.0

    return {
        "filepath": str(filepath),
        "env": int(env),
        "subject": int(subject),
        "class_id": int(class_id),
        "activity": int(activity),
        "trial": int(trial),
        "is_fall": int(is_fall),
        "raw_samples": int(stats_list[0].get("raw_samples", 0)) if stats_list else 0,
        "n_segments": int(n_segments),
        "mean_n_pcs": float(mean_n_pcs),
        "mean_qh": float(mean_qh),
        "status": status,
        "error_msg": error_msg,
    }


def process_file(
    filepath: Path,
    config: Dict[str, Any],
    output_dir: Path,
) -> Dict[str, Any]:
    """
    Process single CSI file with windowing and per-segment preprocessing.

    Returns:
        manifest_row dict
    """
    try:
        sample = load_csi_file(filepath)

        env = sample["env"]
        subject = sample["subject"]
        class_id = sample["class_id"]
        activity = sample["activity"]
        trial = sample["trial"]
        is_fall = sample["is_fall"]

        csi = sample["csi"]
        timestamps = sample["timestamps"]

        window_cfg = config["window"]
        resample_cfg = config["resample"]
        filter_cfg = config["filter"]
        stream_cfg = config["stream_selection"]
        pca_cfg = config["pca"]

        segments = sliding_window_raw(
            csi,
            timestamps,
            window_sec=window_cfg["size_sec"],
            stride_sec=window_cfg["stride_sec"],
        )

        if len(segments) == 0:
            raise ValueError(f"No segments generated from {filepath}")

        signal_list = []
        stats_list = []

        for seg_id, (csi_seg, ts_seg) in enumerate(segments):
            rep_signal, stats = preprocess_segment(
                csi_seg,
                ts_seg,
                fs_hz=resample_cfg["fs_hz"],
                tolerance_ms=resample_cfg["tolerance_ms"],
                max_interp_gap_steps=resample_cfg["max_interp_gap_steps"],
                low_hz=filter_cfg["low_hz"],
                high_hz=filter_cfg["high_hz"],
                filter_order=filter_cfg["order"],
                omega=stream_cfg["omega"],
                n_streams=stream_cfg["n_streams"],
                use_ant=stream_cfg.get("use_ant"),
                eigenvalue_threshold=pca_cfg["eigenvalue_threshold"],
                max_pcs=pca_cfg.get("max_pcs", 3),
                window_sec=window_cfg["size_sec"],
            )
            signal_list.append(rep_signal)
            stats_list.append(stats)

        out_filename = f"E{env}_S{subject:02d}_C{class_id:02d}_A{activity:02d}_T{trial:02d}.npz"
        out_path = output_dir / out_filename

        output_dir.mkdir(parents=True, exist_ok=True)

        n_pcs_arr = np.array([s.get("selected_pc_count", 0) for s in stats_list], dtype=np.int32)

        np.savez_compressed(
            out_path,
            signal=np.stack(signal_list, axis=0).astype(np.float32),
            n_pcs_per_seg=n_pcs_arr,
            n_segments=np.asarray(len(segments), dtype=np.int32),
            window_size=np.asarray(signal_list[0].shape[0], dtype=np.int32) if signal_list else 0,
            env=np.asarray(env, dtype=np.int32),
            subject=np.asarray(subject, dtype=np.int32),
            class_id=np.asarray(class_id, dtype=np.int32),
            activity=np.asarray(activity, dtype=np.int32),
            trial=np.asarray(trial, dtype=np.int32),
            is_fall=np.asarray(is_fall, dtype=np.bool_),
        )

        manifest_row = build_manifest_row(
            filepath,
            env,
            subject,
            class_id,
            activity,
            trial,
            is_fall,
            len(segments),
            stats_list,
            status="ok",
        )

        return manifest_row

    except Exception as e:
        manifest_row = build_manifest_row(
            filepath,
            0,
            0,
            0,
            0,
            0,
            False,
            0,
            [],
            status="error",
            error_msg=str(e)[:200],
        )
        return manifest_row


def main() -> None:
    args = parse_args()

    config = load_config(args.config)

    data_config = config["data"]
    output_config = config["output"]

    mendeley_root = Path(data_config["mendeley_root"])
    output_dir = Path(output_config["path"])

    if not mendeley_root.exists():
        print(f"ERROR: Mendeley root not found: {mendeley_root}")
        sys.exit(1)

    print("=" * 70)
    print("CSI PREPROCESSING PIPELINE (Windowing + Per-Segment)")
    print("=" * 70)

    print("\n[1/5] Loading file index...")
    df_all = get_file_index(mendeley_root)
    print(f"  Total files: {len(df_all)}")

    print("\n[2/5] Filtering by criteria...")
    df_filtered = filter_dataframe(
        df_all,
        env=data_config.get("env"),
        subjects=data_config.get("subjects"),
        activities=data_config.get("activities"),
        fall_only=data_config.get("fall_only", False),
    )
    print(f"  After filtering: {len(df_filtered)}")

    max_files = args.max_files or config["run"].get("max_files")
    if max_files is not None:
        df_filtered = df_filtered.head(max_files)
        print(f"  After max_files limit: {len(df_filtered)}")

    print("\n[3/5] Processing files...")
    manifest_rows: List[Dict[str, Any]] = []
    start_time = time.monotonic()

    for idx, row in tqdm(
        df_filtered.iterrows(),
        total=len(df_filtered),
        desc="Processing",
        unit="file",
    ):
        filepath = Path(row["filepath"])
        manifest_row = process_file(filepath, config, output_dir)
        manifest_rows.append(manifest_row)

    elapsed = time.monotonic() - start_time

    print("\n[4/5] Saving manifest...")
    manifest_path = output_dir / "manifest.csv"
    output_dir.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "filepath",
        "env",
        "subject",
        "class_id",
        "activity",
        "trial",
        "is_fall",
        "raw_samples",
        "n_segments",
        "mean_n_pcs",
        "mean_qh",
        "status",
        "error_msg",
    ]

    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow(row)

    print(f"  Saved to: {manifest_path}")

    ok_count = sum(1 for row in manifest_rows if row["status"] == "ok")
    error_count = len(manifest_rows) - ok_count

    print("\n[5/5] Summary")
    print("=" * 70)
    print(f"Total processed: {len(manifest_rows)}")
    print(f"  OK:    {ok_count}")
    print(f"  Error: {error_count}")
    print(f"Elapsed time: {elapsed:.1f}s")
    if len(manifest_rows) > 0:
        print(f"Rate: {len(manifest_rows) / elapsed:.1f} files/s")
    print(f"Output directory: {output_dir.resolve()}")
    print("=" * 70)

    if error_count > 0:
        print("\nERRORS:")
        for row in manifest_rows:
            if row["status"] == "error":
                print(f"  {row['filepath']}: {row['error_msg']}")


if __name__ == "__main__":
    main()
