"""Run one command and record process/GPU/resource peaks for capacity planning."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil

from resource_monitor import gpu_samples


def tree(process: psutil.Process) -> list[psutil.Process]:
    try:
        return [process, *process.children(recursive=True)]
    except psutil.Error:
        return [process]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=Path)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("command is required after --")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.samples:
        args.samples.parent.mkdir(parents=True, exist_ok=True)
    disk_before = psutil.disk_usage(str(args.cwd.resolve())).used
    gpu_before = gpu_samples()
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    child = subprocess.Popen(command, cwd=args.cwd)
    process = psutil.Process(child.pid)
    tracked: dict[int, psutil.Process] = {}
    for item in tree(process):
        try:
            tracked[item.pid] = item
            item.cpu_percent(interval=None)
        except psutil.Error:
            pass
    max_rss = 0
    max_cpu = 0.0
    max_gpu_memory = max((g.get("memory_used_mib") or 0 for g in gpu_before), default=0)
    max_gpu_util = max((g.get("utilization_percent") or 0 for g in gpu_before), default=0)
    sample_count = 0
    while child.poll() is None:
        discovered = tree(process)
        for item in discovered:
            if item.pid not in tracked:
                tracked[item.pid] = item
                try:
                    item.cpu_percent(interval=None)
                except psutil.Error:
                    pass
        processes = list(tracked.values())
        rss = 0
        cpu = 0.0
        for item in processes:
            try:
                rss += item.memory_info().rss
                cpu += item.cpu_percent(interval=None)
            except psutil.Error:
                continue
        gpus = gpu_samples()
        gpu_memory = max((g.get("memory_used_mib") or 0 for g in gpus), default=0)
        gpu_util = max((g.get("utilization_percent") or 0 for g in gpus), default=0)
        max_rss = max(max_rss, rss)
        max_cpu = max(max_cpu, cpu)
        max_gpu_memory = max(max_gpu_memory, gpu_memory)
        max_gpu_util = max(max_gpu_util, gpu_util)
        sample = {
            "epoch_seconds": time.time(),
            "process_rss_bytes": rss,
            "process_cpu_percent": cpu,
            "gpus": gpus,
        }
        if args.samples:
            with args.samples.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(sample) + "\n")
        sample_count += 1
        time.sleep(args.interval)
    elapsed = time.perf_counter() - started
    disk_after = psutil.disk_usage(str(args.cwd.resolve())).used
    baseline_gpu_memory = max((g.get("memory_used_mib") or 0 for g in gpu_before), default=0)
    summary = {
        "status": "passed" if child.returncode == 0 else "failed",
        "return_code": child.returncode,
        "command": command,
        "started_at": started_at.isoformat(),
        "elapsed_seconds": elapsed,
        "sample_count": sample_count,
        "peak_process_rss_bytes": max_rss,
        "peak_process_cpu_percent": max_cpu,
        "peak_process_cpu_cores": max_cpu / 100.0,
        "gpu_baseline_memory_mib": baseline_gpu_memory,
        "peak_gpu_memory_mib": max_gpu_memory,
        "peak_gpu_memory_delta_mib": max(0, max_gpu_memory - baseline_gpu_memory),
        "peak_gpu_utilization_percent": max_gpu_util,
        "disk_used_delta_bytes": disk_after - disk_before,
        "measurement_scope": "process tree for CPU/RSS; host GPU for GPU/VRAM; filesystem for storage delta",
    }
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(child.returncode)


if __name__ == "__main__":
    main()
