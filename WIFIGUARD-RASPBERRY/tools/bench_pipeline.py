"""피처 추출 지연 벤치마크 (엣지 기준선).

합성 CSI 윈도우로 윈도우 1개당 처리 시간을 측정한다. 목표는 스트라이드 250ms 이내
(명세 §4 Phase 3-1, Pi 에서 p90 기준).

**`--stages` 가 D1 의 근거를 재는 부분이다.** 엣지가 실제로 부담하는 것은
`extract_window_signal`(서브캐리어 선택 → 리샘플 → PCA)까지이고, 무거운
`features_from_signal`(CWT 스칼로그램 + ACF)은 클라우드 모델서버가 맡는다.
두 구간을 따로 재야 "엣지 예산을 지키는가"에 답할 수 있다.

실행 (레포 루트에서):
    .venv/bin/python tools/bench_pipeline.py                    # 기본 fs=91Hz, 40회
    .venv/bin/python tools/bench_pipeline.py --fs 166.75 --stages
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

try:
    from wifiguard_edge.features import (
        FeatureConfig,
        extract_window_features,
        extract_window_signal,
        features_from_signal,
    )
except ModuleNotFoundError:  # 패키지 미설치 시 src/ 를 직접 잡는다 (pytest 는 pyproject pythonpath 로 동일 처리)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from wifiguard_edge.features import (
        FeatureConfig,
        extract_window_features,
        extract_window_signal,
        features_from_signal,
    )


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


def _pct(values: list[float], q: float) -> float:
    return float(np.percentile(values, q))


def _line(label: str, values: list[float], budget_ms: float | None = None) -> None:
    tail = ""
    if budget_ms is not None:
        tail = f"   예산 {budget_ms:.0f}ms: {'예' if _pct(values, 90) < budget_ms else '아니오'}"
    print(
        f"  {label:<22} median {_pct(values, 50):7.1f}  p90 {_pct(values, 90):7.1f}"
        f"  max {max(values):7.1f}{tail}"
    )


def bench(fs_hz: float, subcarriers: int, iterations: int, stages: bool) -> None:
    config = FeatureConfig()

    # 첫 호출은 numba JIT / CWT 스케일 캐시 워밍업이라 측정에서 뺀다.
    times, amps = synthetic_window(fs_hz, config.window_seconds + 0.3, subcarriers, seed=-1 & 0xFFFF)
    extract_window_features(times, amps, config)

    signal_ms: list[float] = []
    tensor_ms: list[float] = []
    total_ms: list[float] = []
    signal_bytes = tensor_bytes = 0

    for i in range(iterations):
        times, amps = synthetic_window(fs_hz, config.window_seconds + 0.3, subcarriers, seed=i)

        t0 = time.monotonic()
        ws = extract_window_signal(times, amps, config)       # ← 엣지(Pi)가 부담하는 구간
        t1 = time.monotonic()
        feats = features_from_signal(ws.signal, ws.fs_hz, config)  # ← 클라우드 모델서버
        t2 = time.monotonic()

        signal_ms.append((t1 - t0) * 1000.0)
        tensor_ms.append((t2 - t1) * 1000.0)
        total_ms.append((t2 - t0) * 1000.0)
        signal_bytes = ws.signal.nbytes
        tensor_bytes = feats.s3.nbytes + feats.acf.nbytes

    print(f"fs={fs_hz}Hz subcarriers={subcarriers} n={iterations} window={config.window_seconds}s")
    if stages:
        print("  [엣지] Pi 가 실제로 부담하는 구간 — 서브캐리어 선택 → 리샘플 → PCA 합성")
        _line("signal ms", signal_ms, budget_ms=250)
        print("  [클라우드] 모델서버 구간 — CWT 스칼로그램 + PCA-ACF")
        _line("tensor ms", tensor_ms)
        print("  [합계] 분해 이전 extract_window_features 와 같은 일")
        _line("total ms", total_ms, budget_ms=250)
        share = 100.0 * _pct(signal_ms, 50) / max(_pct(total_ms, 50), 1e-9)
        print(f"\n  엣지 몫: 시간 {share:.1f}%   업링크 {signal_bytes:,}B / {tensor_bytes:,}B "
              f"= 1/{tensor_bytes / max(signal_bytes, 1):.0f}")
    else:
        _line("feature ms", total_ms, budget_ms=250)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fs", type=float, default=91.0, help="합성 수신률 Hz (실측 약 91)")
    ap.add_argument("--subcarriers", type=int, default=245)
    ap.add_argument("--iterations", type=int, default=40)
    ap.add_argument("--stages", action="store_true", help="엣지/클라우드 구간을 나눠 측정 (D1)")
    args = ap.parse_args()
    bench(args.fs, args.subcarriers, args.iterations, args.stages)


if __name__ == "__main__":
    main()
