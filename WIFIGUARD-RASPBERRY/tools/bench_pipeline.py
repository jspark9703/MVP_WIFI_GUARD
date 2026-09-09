"""피처 추출 지연 벤치마크 (엣지 기준선).

합성 CSI 윈도우로 윈도우 1개당 **피처 추출** 시간을 측정한다.
낙상 추론은 클라우드 소관이므로(PORTING.md §1-3) 이 스크립트는 추론을 재지 않는다.
목표: 스트라이드 250ms 이내 (명세 §4 Phase 3-1, Pi에서 p90 기준).

실행 (레포 루트에서):
    .venv/bin/python tools/bench_pipeline.py            # 기본 fs=91Hz, 40회
    .venv/bin/python tools/bench_pipeline.py --fs 166.67
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

try:
    from wifiguard_edge.features import FeatureConfig, extract_window_features
except ModuleNotFoundError:  # 패키지 미설치 시 src/ 를 직접 잡는다 (pytest 는 pyproject pythonpath 로 동일 처리)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from wifiguard_edge.features import FeatureConfig, extract_window_features


def synthetic_window(fs_hz: float, seconds: float, subcarriers: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = int(round(seconds * fs_hz))
    times = np.arange(n) / fs_hz + rng.normal(0, 0.0005, size=n)
    times = np.sort(times)
    t = np.arange(n) / fs_hz
    base = 20 + 5 * np.sin(2 * np.pi * 0.7 * t)[:, None]
    spike = 8 * np.exp(-((t - seconds / 2) ** 2) / 0.02)[:, None]
    amps = base + spike * rng.uniform(0.3, 1.0, size=(1, subcarriers)) + rng.normal(
        0, 1.5, size=(n, subcarriers)
    )
    return times.astype(np.float64), amps.astype(np.float32)


def bench(fs_hz: float, subcarriers: int, iterations: int) -> None:
    config = FeatureConfig()

    # 첫 호출은 numba JIT / CWT 스케일 캐시 워밍업이라 측정에서 뺀다.
    times, amps = synthetic_window(fs_hz, config.window_seconds + 0.3, subcarriers, seed=-1 & 0xFFFF)
    extract_window_features(times, amps, config)

    feature_ms: list[float] = []
    for i in range(iterations):
        times, amps = synthetic_window(fs_hz, config.window_seconds + 0.3, subcarriers, seed=i)
        t0 = time.monotonic()
        extract_window_features(times, amps, config)
        feature_ms.append((time.monotonic() - t0) * 1000.0)

    def pct(values: list[float], q: float) -> float:
        return float(np.percentile(values, q))

    total = feature_ms  # 엣지 예산은 피처 추출만 (추론은 클라우드)
    print(f"fs={fs_hz}Hz subcarriers={subcarriers} n={iterations} window={config.window_seconds}s")
    print(f"  feature ms: median {pct(feature_ms, 50):7.1f}  p90 {pct(feature_ms, 90):7.1f}  max {max(feature_ms):7.1f}")
    print(f"  stride 250ms 이내: {'예' if pct(total, 90) < 250 else '아니오'} (p90 기준)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fs", type=float, default=91.0, help="합성 수신률 Hz (실측 약 91)")
    ap.add_argument("--subcarriers", type=int, default=245)
    ap.add_argument("--iterations", type=int, default=40)
    args = ap.parse_args()
    bench(args.fs, args.subcarriers, args.iterations)


if __name__ == "__main__":
    main()
