#!/usr/bin/env python3
"""
DWT Feature Extraction Pipeline (Per-Segment)

Extracts DWT energy features from preprocessed per-segment signals.
Input:  data/preprocessed/*.npz (n_seg, window_size) 1D representative signals
Output: data/features/*.npz (n_seg, 7) DWT energy + entropy features

Usage:
    python scripts/featurization.py --config config/featurization.yaml
    python scripts/featurization.py --config config/featurization.yaml --max-files 10
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

from src.dwt_coef.dwt_features import compute_energy_entropy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DWT feature extraction pipeline (per-segment)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/featurization.yaml"),
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


def get_preprocessed_files(input_dir: Path) -> List[Path]:
    """List all preprocessed NPZ files."""
    files = sorted(input_dir.glob("E*_S*.npz"))
    return files


def filter_files(
    files: List[Path],
    env: Optional[List[int]] = None,
    subjects: Optional[List[int]] = None,
    activities: Optional[List[int]] = None,
    fall_only: bool = False,
) -> List[Path]:
    """Filter files by metadata (parsed from filename)."""
    filtered = []

    for filepath in files:
        filename = filepath.stem
        parts = filename.split("_")
        if len(parts) < 5:
            continue

        try:
            file_env = int(parts[0][1:])
            file_subject = int(parts[1][1:])
            file_activity = int(parts[3][1:])
            file_is_fall = file_activity in [2, 5]
        except (ValueError, IndexError):
            continue

        if env is not None and file_env not in env:
            continue

        if subjects is not None and file_subject not in subjects:
            continue

        if activities is not None and file_activity not in activities:
            continue

        if fall_only and not file_is_fall:
            continue

        filtered.append(filepath)

    return filtered


def parse_filename_metadata(filename: str) -> Dict[str, int]:
    """Extract metadata from filename: E{e}_S{s}_C{c}_A{a}_T{t}"""
    parts = filename.split("_")
    env = int(parts[0][1:])
    subject = int(parts[1][1:])
    class_id = int(parts[2][1:])
    activity = int(parts[3][1:])
    trial = int(parts[4][1:])
    is_fall = activity in [2, 5]

    return {
        "env": env,
        "subject": subject,
        "class_id": class_id,
        "activity": activity,
        "trial": trial,
        "is_fall": is_fall,
    }


def build_manifest_row(
    input_filepath: Path,
    output_filepath: Path,
    metadata: Dict[str, int],
    stats: Dict[str, Any],
    status: str = "ok",
    error_msg: str = "",
) -> Dict[str, Any]:
    """Build a row for manifest.csv."""
    return {
        "filepath": str(input_filepath),
        "feature_path": str(output_filepath),
        "env": int(metadata["env"]),
        "subject": int(metadata["subject"]),
        "class_id": int(metadata["class_id"]),
        "activity": int(metadata["activity"]),
        "trial": int(metadata["trial"]),
        "is_fall": int(metadata["is_fall"]),
        "n_segments": int(stats.get("n_segments", 0)),
        "mean_entropy": float(stats.get("mean_entropy", 0.0)),
        "mean_total_energy": float(stats.get("mean_total_energy", 0.0)),
        "status": status,
        "error_msg": error_msg,
    }


def process_file(
    input_filepath: Path,
    config: Dict[str, Any],
    output_dir: Path,
) -> Dict[str, Any]:
    """
    Extract DWT features from preprocessed per-segment signals.

    Returns:
        manifest_row dict
    """
    try:
        metadata = parse_filename_metadata(input_filepath.stem)

        data = np.load(input_filepath)
        signal = data["signal"].astype(np.float32)  # (n_seg, window_size)
        n_segments = signal.shape[0]

        # Get DWT config
        dwt_config = config.get("dwt", {})
        wavelet = dwt_config.get("wavelet", "db4")
        level = dwt_config.get("level", 5)
        include_entropy = dwt_config.get("include_entropy", True)

        features_list = []
        for seg_idx in range(n_segments):
            sig = signal[seg_idx, :]  # (window_size,)
            result = compute_energy_entropy(sig, wavelet=wavelet, level=level)

            # Extract energy values in order: cA, cD1, cD2, ..., cDlevel (exclude "total")
            energy_dict = result["energy"]
            energy_vals = [energy_dict["cA"]]
            for i in range(1, level + 1):
                energy_vals.append(energy_dict[f"cD{i}"])

            entropy = result["entropy"] if include_entropy else None
            if include_entropy:
                features_list.append(energy_vals + [entropy])
            else:
                features_list.append(energy_vals)

        features = np.array(features_list, dtype=np.float32)

        stats = {
            "n_segments": n_segments,
            "mean_entropy": float(np.mean(features[:, -1])) if include_entropy else 0.0,
            "mean_total_energy": float(np.mean(np.sum(features[:, :-1] if include_entropy else features, axis=1))),
        }

        output_filename = input_filepath.name
        output_filepath = output_dir / output_filename

        output_dir.mkdir(parents=True, exist_ok=True)

        # Build feature names dynamically based on level
        feature_names = ["E_cA"] + [f"E_cD{i}" for i in range(1, level + 1)]
        if include_entropy:
            feature_names.append("entropy")

        np.savez_compressed(
            output_filepath,
            features=features,
            feature_names=np.asarray(feature_names),
            n_segments=np.asarray(n_segments, dtype=np.int32),
            env=np.asarray(metadata["env"], dtype=np.int32),
            subject=np.asarray(metadata["subject"], dtype=np.int32),
            class_id=np.asarray(metadata["class_id"], dtype=np.int32),
            activity=np.asarray(metadata["activity"], dtype=np.int32),
            trial=np.asarray(metadata["trial"], dtype=np.int32),
            is_fall=np.asarray(metadata["is_fall"], dtype=np.bool_),
        )

        manifest_row = build_manifest_row(
            input_filepath,
            output_filepath,
            metadata,
            stats,
            status="ok",
        )

        return manifest_row

    except Exception as e:
        metadata = parse_filename_metadata(input_filepath.stem)
        manifest_row = build_manifest_row(
            input_filepath,
            output_dir / input_filepath.name,
            metadata,
            {},
            status="error",
            error_msg=str(e)[:200],
        )
        return manifest_row


def main() -> None:
    args = parse_args()

    config = load_config(args.config)

    input_dir = Path(config["input"]["path"])
    output_dir = Path(config["output"]["path"])

    if not input_dir.exists():
        print(f"ERROR: Input directory not found: {input_dir}")
        sys.exit(1)

    print("=" * 70)
    print("DWT FEATURE EXTRACTION PIPELINE (Per-Segment)")
    print("=" * 70)

    print("\n[1/5] Finding preprocessed files...")
    files_all = get_preprocessed_files(input_dir)
    print(f"  Total files: {len(files_all)}")

    print("\n[2/5] Filtering by criteria...")
    filter_config = config.get("filter", {})
    files_filtered = filter_files(
        files_all,
        env=filter_config.get("env"),
        subjects=filter_config.get("subjects"),
        activities=filter_config.get("activities"),
        fall_only=filter_config.get("fall_only", False),
    )
    print(f"  After filtering: {len(files_filtered)}")

    max_files = args.max_files or config["run"].get("max_files")
    if max_files is not None:
        files_filtered = files_filtered[:max_files]
        print(f"  After max_files limit: {len(files_filtered)}")

    print("\n[3/5] Extracting DWT features...")
    manifest_rows: List[Dict[str, Any]] = []
    start_time = time.monotonic()

    for idx, filepath in tqdm(
        enumerate(files_filtered),
        total=len(files_filtered),
        desc="Processing",
        unit="file",
    ):
        manifest_row = process_file(filepath, config, output_dir)
        manifest_rows.append(manifest_row)

    elapsed = time.monotonic() - start_time

    print("\n[4/5] Saving manifest...")
    manifest_path = output_dir / "manifest.csv"
    output_dir.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "filepath",
        "feature_path",
        "env",
        "subject",
        "class_id",
        "activity",
        "trial",
        "is_fall",
        "n_segments",
        "mean_entropy",
        "mean_total_energy",
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
