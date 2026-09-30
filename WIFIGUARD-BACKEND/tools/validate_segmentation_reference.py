"""Validate the integrated segmentation stack against the supplier reference files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from csi_fall_segmentation.engine import SegmentationInferenceEngine
from csi_fall_segmentation.features import (
    DEFAULT_CONFIG,
    compute_feature_pairs,
    load_raw_csi,
    native_window_starts,
)


def _max_abs(actual: np.ndarray, expected: np.ndarray) -> float:
    return float(np.max(np.abs(np.asarray(actual) - np.asarray(expected))))


def validate(package_root: Path, checkpoint: Path, device: str) -> dict[str, Any]:
    raw_csv = package_root / "example_raw_csv" / "20260705_005402_RX1_S1_R1_T1_D1_csi.csv"
    raw_reference_path = package_root / "reference" / "raw_csv_smoke_2windows"
    feature_reference_path = package_root / "reference" / "feature_example"
    feature_archive_path = (
        package_root / "data" / "features" / "20260705_005402_RX1_S1_R1_T1_D1.npz"
    )

    amplitude, fs_hz, _ = load_raw_csi(raw_csv, DEFAULT_CONFIG)
    window_samples = int(round(DEFAULT_CONFIG["window_seconds"] * fs_hz))
    starts = native_window_starts(
        len(amplitude),
        fs_hz,
        DEFAULT_CONFIG["window_seconds"],
        DEFAULT_CONFIG["stride_seconds"],
    )[:2]
    windows = np.stack(
        [amplitude[int(start) : int(start) + window_samples] for start in starts]
    )
    s3, acf = compute_feature_pairs(windows, fs_hz, DEFAULT_CONFIG)

    with np.load(raw_reference_path / "generated_features.npz", allow_pickle=False) as ref:
        s3_error = _max_abs(s3, ref["s3"])
        acf_error = _max_abs(acf, ref["pca_acf_lag0p4s"])
        starts_match = bool(np.array_equal(starts, ref["window_start"]))

    engine = SegmentationInferenceEngine(checkpoint, device=device)
    raw_probabilities = engine.predict_segmentation_batch(s3, acf)
    with np.load(
        raw_reference_path / "segmentation_probabilities.npz", allow_pickle=False
    ) as reference:
        raw_probability_error = _max_abs(raw_probabilities, reference["probabilities"])
        raw_center_error = _max_abs(
            engine.predict_batch(s3, acf), reference["center_probability"]
        )

    with np.load(feature_archive_path, allow_pickle=False) as archive:
        feature_s3 = archive["s3"].astype(np.float32)
        feature_acf = archive["pca_acf_lag0p4s"].astype(np.float32)
    feature_probabilities = engine.predict_segmentation_batch(feature_s3, feature_acf)
    with np.load(
        feature_reference_path / "segmentation_probabilities.npz", allow_pickle=False
    ) as reference:
        feature_probability_error = _max_abs(
            feature_probabilities, reference["probabilities"]
        )
        feature_center_error = _max_abs(
            engine.predict_batch(feature_s3, feature_acf), reference["center_probability"]
        )

    checks = {
        "window_starts_match": starts_match,
        "s3_max_abs_error_at_most_0_0003": s3_error <= 3e-4,
        "acf_max_abs_error_at_most_0_0003": acf_error <= 3e-4,
        "raw_probability_max_abs_error_at_most_0_0003": raw_probability_error <= 3e-4,
        "raw_center_max_abs_error_at_most_0_0003": raw_center_error <= 3e-4,
        "feature_probability_max_abs_error_at_most_0_0003": feature_probability_error <= 3e-4,
        "feature_center_max_abs_error_at_most_0_0003": feature_center_error <= 3e-4,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "device": str(engine.device),
        "checkpoint_sha256": engine.checkpoint_sha256,
        "checks": checks,
        "measurements": {
            "raw_window_count": len(starts),
            "feature_example_window_count": len(feature_s3),
            "s3_max_abs_error": s3_error,
            "acf_max_abs_error": acf_error,
            "raw_probability_max_abs_error": raw_probability_error,
            "raw_center_max_abs_error": raw_center_error,
            "feature_probability_max_abs_error": feature_probability_error,
            "feature_center_max_abs_error": feature_center_error,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.package_root, args.checkpoint, args.device)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 2)


if __name__ == "__main__":
    main()
