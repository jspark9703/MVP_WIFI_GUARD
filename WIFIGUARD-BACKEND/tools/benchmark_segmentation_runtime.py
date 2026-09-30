"""Benchmark the exact live feature and inference path without Kafka or hardware."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

import numpy as np


def amplitude(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(960, dtype=np.float32) / 320.0
    channels = np.arange(30, dtype=np.float32)[None]
    base = 20 + np.sin(2 * np.pi * (0.8 + channels / 60) * t[:, None])
    burst = np.exp(-((t - 1.5) / 0.15) ** 2)[:, None] * np.sin(2 * np.pi * 16 * t[:, None])
    return (base + burst * (0.2 + channels / 60) + rng.normal(0, 0.01, (960, 30))).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    parser.add_argument("--windows", type=int, default=4)
    parser.add_argument("--batches", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ssq-gpu", action="store_true")
    args = parser.parse_args()
    if args.ssq_gpu:
        os.environ["SSQ_GPU"] = "1"
    from csi_fall_segmentation.engine import SegmentationInferenceEngine
    from csi_fall_segmentation.online import SegmentationFeatureBuilder

    engine = SegmentationInferenceEngine(args.checkpoint, args.device)
    builder = SegmentationFeatureBuilder()
    builder.start()
    engine.warmup()
    feature_ms: list[float] = []
    inference_ms: list[float] = []
    total_started = time.perf_counter()
    probabilities: list[float] = []
    batch_feature_ms: list[float] = []
    batch_inference_ms: list[float] = []
    for batch in range(args.batches):
        started = time.perf_counter()
        features = builder.build_batch(
            [amplitude(batch * args.windows + index) for index in range(args.windows)], 320.0
        )
        feature_total_ms = (time.perf_counter() - started) * 1000
        batch_feature_ms.append(feature_total_ms)
        feature_ms.extend([feature_total_ms / args.windows] * args.windows)
        started = time.perf_counter()
        probabilities.extend(engine.predict_batch(features.s3, features.acf).tolist())
        inference_total_ms = (time.perf_counter() - started) * 1000
        batch_inference_ms.append(inference_total_ms)
        inference_ms.extend([inference_total_ms / args.windows] * args.windows)
    steady_feature = feature_ms[args.windows:] if args.batches > 1 else feature_ms
    steady_inference = inference_ms[args.windows:] if args.batches > 1 else inference_ms
    result = {
        "status": "passed",
        "device": str(engine.device),
        "ssq_gpu": args.ssq_gpu,
        "windows": args.windows,
        "batches": args.batches,
        "feature_ms": feature_ms,
        "inference_ms": inference_ms,
        "batch_feature_ms": batch_feature_ms,
        "batch_inference_ms": batch_inference_ms,
        "median_feature_ms": statistics.median(steady_feature),
        "p95_feature_ms": sorted(steady_feature)[max(0, int(0.95 * len(steady_feature)) - 1)],
        "median_inference_ms": statistics.median(steady_inference),
        "total_seconds": time.perf_counter() - total_started,
        "throughput_windows_per_second": (args.windows * args.batches) / max(time.perf_counter() - total_started, 1e-9),
        "realtime_4hz_sustained": (statistics.median(steady_feature) + statistics.median(steady_inference)) <= 250,
        "probabilities": probabilities,
        "model_version": engine.model_version,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
