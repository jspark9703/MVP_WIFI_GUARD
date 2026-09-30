"""Load, warm, and execute one deterministic live-model window."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np


def representative_amplitude(fs_hz: float = 320.0, seconds: float = 3.0) -> np.ndarray:
    count = int(round(fs_hz * seconds))
    t = np.arange(count, dtype=np.float32) / np.float32(fs_hz)
    carriers = np.arange(30, dtype=np.float32)[None, :]
    base = 20 + 0.15 * np.sin(2 * np.pi * (1.7 + carriers / 80) * t[:, None])
    burst = (
        np.exp(-np.square((t - 1.5) / 0.12))[:, None]
        * np.sin(2 * np.pi * 22 * t)[:, None]
        * (0.4 + carriers / 60)
    )
    return (base + burst).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--device", default="cpu", choices=["auto", "cpu", "cuda", "mps"])
    args = parser.parse_args()

    from wifiguard_inference.features import LiveFeatureBuilder
    from csi_fall_segmentation.engine import SegmentationInferenceEngine

    builder = LiveFeatureBuilder()
    builder.start()
    load_started = time.perf_counter()
    engine = SegmentationInferenceEngine(args.checkpoint, device=args.device)
    engine.warmup()
    load_ms = (time.perf_counter() - load_started) * 1000

    amplitude = representative_amplitude()
    feature_started = time.perf_counter()
    features = builder.build(amplitude, 320.0)
    feature_ms = (time.perf_counter() - feature_started) * 1000
    infer_started = time.perf_counter()
    probability = engine.predict(features.s3[0], features.acf[0])
    infer_ms = (time.perf_counter() - infer_started) * 1000
    if not 0 <= probability <= 1:
        raise SystemExit(f"invalid probability: {probability}")

    print(json.dumps({
        "status": "MODEL_CHECKPOINT_READY",
        "checkpoint": str(args.checkpoint.resolve()),
        "model_version": engine.model_version,
        "threshold": engine.threshold,
        "s3_shape": list(features.s3.shape),
        "pca_acf_shape": list(features.acf.shape),
        "probability": probability,
        "load_and_warm_ms": round(load_ms, 2),
        "feature_ms": round(feature_ms, 2),
        "infer_ms": round(infer_ms, 2),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"MODEL_CHECKPOINT_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
