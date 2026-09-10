"""recording 생성·캡처 — `ReplaySource` 가 재생할 `.npz` 를 만든다.

`python -m wifiguard_edge --transport replay` 는 **합성** 신호를 즉석에서 만든다.
그것으로 배선은 검증되지만 실행마다 값이 달라 회귀 비교를 할 수 없고, 실제 신호 분포도
아니다. 이 도구는 두 가지를 채운다.

    capture    실제 수신기(UART)에서 N초를 받아 저장한다 — 하드웨어가 있을 때 한 번만
    synth      결정론적 합성 recording 을 만든다 — 시드가 같으면 항상 같은 파일

저장 형식은 `times`(초, 단조 증가) + `amps`(프레임 × 서브캐리어) 두 배열이다.
`ReplaySource._load()` 가 이 두 키를 요구한다.

실행 (레포 루트에서):
    .venv/bin/python tools/replay_edge.py synth   --out recordings/synth.npz --seconds 120
    .venv/bin/python tools/replay_edge.py capture --out recordings/live.npz  --seconds 60
    .venv/bin/python -m wifiguard_edge --transport replay --replay-source recordings/synth.npz
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

try:
    from wifiguard_edge.csi.buffer import RingBuffer
except ModuleNotFoundError:  # 패키지 미설치 시 src/ 를 직접 잡는다
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from wifiguard_edge.csi.buffer import RingBuffer


def synth(seconds: float, fs_hz: float, subcarriers: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """`ReplaySource` 의 합성 규칙을 그대로 써서 결정론적 recording 을 만든다.

    같은 생성기를 쓰는 것이 중요하다 — 여기서 따로 만들면 재생 경로 두 개가 갈라지고,
    "합성으로는 되는데 파일로는 안 된다"는 종류의 차이가 생긴다.
    """
    from wifiguard_edge.transport.replay_source import ReplaySource

    src = ReplaySource(RingBuffer(1.0), "synthetic", subcarriers=subcarriers, fs_hz=fs_hz, seed=seed)
    n = int(round(seconds * fs_hz))
    times = np.arange(n, dtype=np.float64) / fs_hz
    amps = np.empty((n, subcarriers), dtype=np.float32)
    for i, t in enumerate(times):
        amps[i] = src._synthetic_amps(float(t))
    return times, amps


def capture(seconds: float, port: str | None, baud: int) -> tuple[np.ndarray, np.ndarray]:
    """실제 수신기에서 캡처한다. 하드웨어와 `pyserial` 이 필요하다."""
    from wifiguard_edge.csi.serial_reader import SerialReader

    ring = RingBuffer(max_seconds=seconds + 5.0, nominal_hz=200.0)
    reader = SerialReader(ring, port=port, baud=baud)
    reader.start()
    print(f"캡처 시작 — {seconds:.0f}초 (baud={baud})", flush=True)
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(1.0)
            print(f"  {reader.packet_count:6d} 프레임  연결={reader.running}", flush=True)
    finally:
        reader.stop()
        reader.join(timeout=3.0)

    window = ring.get_window(seconds)
    if window is None:
        raise SystemExit(
            "프레임을 하나도 받지 못했다. 수신기 연결과 baud 를 확인할 것 — "
            f"펌웨어 CONFIG_ESP_CONSOLE_UART_BAUDRATE 와 {baud} 가 같아야 한다."
        )
    return window


def save(path: Path, times: np.ndarray, amps: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, times=times, amps=amps)
    span = float(times[-1] - times[0]) if len(times) > 1 else 0.0
    fs = (len(times) - 1) / span if span > 0 else 0.0
    print(
        f"저장: {path}  ({len(times):,} 프레임 · {amps.shape[1]} 서브캐리어 · "
        f"{span:.1f}초 · 실효 {fs:.1f}Hz · {path.stat().st_size / 1024:.0f}KB)"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    s = sub.add_parser("synth", help="결정론적 합성 recording 생성")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--seconds", type=float, default=120.0)
    s.add_argument("--fs", type=float, default=166.75)
    s.add_argument("--subcarriers", type=int, default=245)
    s.add_argument("--seed", type=int, default=11)

    c = sub.add_parser("capture", help="실제 수신기에서 캡처 (하드웨어 필요)")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--seconds", type=float, default=60.0)
    c.add_argument("--port", default=None, help="비우면 자동 탐지")
    c.add_argument("--baud", type=int, default=2_000_000)

    args = ap.parse_args(argv)
    if args.mode == "synth":
        times, amps = synth(args.seconds, args.fs, args.subcarriers, args.seed)
    else:
        times, amps = capture(args.seconds, args.port, args.baud)
    save(args.out, times, amps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
