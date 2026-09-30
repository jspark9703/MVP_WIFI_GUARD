"""Convert observed profiler summaries into an explicit AWS-neutral capacity envelope."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


GIB = 1024**3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, nargs="+", required=True)
    parser.add_argument("--dataset-registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--headroom", type=float, default=1.5)
    parser.add_argument("--retained-checkpoints", type=int, default=10)
    args = parser.parse_args()
    if args.headroom < 1:
        raise ValueError("headroom must be >= 1")
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in args.profiles]
    registry = json.loads(args.dataset_registry.read_text(encoding="utf-8"))
    observed = {
        "peak_cpu_cores": max(float(row["peak_process_cpu_cores"]) for row in rows),
        "peak_process_memory_gib": max(float(row["peak_process_rss_bytes"]) for row in rows) / GIB,
        "peak_gpu_memory_delta_gib": max(float(row["peak_gpu_memory_delta_mib"]) for row in rows) / 1024,
        "peak_gpu_utilization_percent": max(float(row["peak_gpu_utilization_percent"]) for row in rows),
        "dataset_gib": (registry["feature_bytes"] + registry["label_bytes"]) / GIB,
        "checkpoint_gib": registry["checkpoint_bytes"] / GIB,
    }
    required = {
        "vcpu": max(2, math.ceil(observed["peak_cpu_cores"] * args.headroom)),
        "ram_gib": max(4, math.ceil(observed["peak_process_memory_gib"] * args.headroom)),
        "gpu_vram_gib": (
            max(0, math.ceil(observed["peak_gpu_memory_delta_gib"] * args.headroom))
        ),
        "persistent_storage_gib": max(
            10,
            math.ceil(
                (observed["dataset_gib"] + observed["checkpoint_gib"] * args.retained_checkpoints)
                * args.headroom
            ),
        ),
    }
    result = {
        "status": "estimated",
        "observed_local": observed,
        "aws_capacity_envelope": required,
        "headroom_multiplier": args.headroom,
        "retained_checkpoints": args.retained_checkpoints,
        "interpretation": {
            "training": "Choose an EC2/SageMaker GPU node meeting or exceeding all four envelope fields.",
            "inference": "Benchmark separately at target device concurrency before right-sizing autoscaling.",
            "storage": "Envelope includes dataset plus retained checkpoints, not logs, backups, or Kafka retention.",
            "cost": "No price claim is made; instance and storage prices must be checked for the deployment region.",
        },
        "limitations": [
            "GPU numbers are host-level deltas and may include overlap with other GPU workloads.",
            "One bounded local run does not establish production concurrency or tail latency.",
            "Bundled recordings were used in training and do not validate model generalization.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
