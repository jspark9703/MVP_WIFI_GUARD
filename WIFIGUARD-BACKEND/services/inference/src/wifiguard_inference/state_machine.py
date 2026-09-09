"""0.25초 주기 실시간 낙상 탐지 루프.

링버퍼에서 3초 윈도우를 꺼내 피처 추출 + 모델 추론을 수행하고,
임계값 + 인과 다수결(최근 5윈도우) 후처리로 상태를 판정한다.

후처리 주의: 검증에 쓴 mode5는 중심 윈도우 기준(미래 2윈도우 필요)이라
실시간에서는 확정에 0.5초가 추가된다. 여기서는 최근 5개 raw 판정의
다수결(인과)을 쓴다. 의미는 근사이며 연구단 권장안 확인 후 조정한다.

상태 매핑 (대시보드 도메인 용어와 동일):
  IDLE(대기)      다수결 음성, raw도 음성
  SUSPECT(의심)   raw 양성이지만 다수결 미확정
  FALL(낙상)      다수결 양성 -> 낙상 이벤트 확정
  COOLDOWN(냉각중) FALL 종료 후 cooldown_seconds 동안 재알람 억제

움직임(MV)/재실(Presence) 감지는 이 파일이 아니라 presence_loop.py가 완전히
독립된 스레드로 담당한다 — DL 모델(--no-model, 체크포인트 없음 등)과 무관하게
항상 동작해야 하기 때문. 이전에는 이 클래스 안에서 같이 계산했으나, 그러면
--no-model일 때 재실 감지 전체가 멎는 결함이 있어 분리했다. migration.md/
occupation_pipline.md 참고.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Callable

from csi.buffer import RingBuffer
from features import FeatureConfig, extract_window_features

if TYPE_CHECKING:
    # torch(무거운 의존성, --no-model 시엔 미설치일 수 있음)를 런타임에 끌어오지
    # 않도록 타입 체커에서만 보이게 한다 — from __future__ import annotations로
    # 아래 타입 힌트는 어차피 런타임에 평가되지 않는다. main.py가 이 파일을
    # 무조건 top-level import하므로(FallDetector 클래스 정의 자체가 필요),
    # 여기서 실제로 import하면 --no-model이 argparse까지 가기도 전에 서버
    # 전체가 죽는다(이미 한 번 실측: ModuleNotFoundError: No module named
    # 'torch').
    from inference import FallInferenceEngine

log = logging.getLogger("detector")

STRIDE_SEC = 0.25
DEFAULT_THRESHOLD = 0.468
MODE_SIZE = 5
COOLDOWN_SECONDS = 10.0
HISTORY_MAXLEN = 240  # 최근 60초 (0.25s x 240)


class FallDetector(threading.Thread):
    def __init__(
        self,
        ring: RingBuffer,
        engine: FallInferenceEngine,
        threshold: float = DEFAULT_THRESHOLD,
        stride_sec: float = STRIDE_SEC,
        cooldown_seconds: float = COOLDOWN_SECONDS,
        feature_config: FeatureConfig | None = None,
        on_fall: Callable[[int, float | None, float], None] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="fall-detector")
        self.ring = ring
        self.engine = engine
        self.threshold = threshold
        self.stride_sec = stride_sec
        self.cooldown_seconds = cooldown_seconds
        self.feature_config = feature_config or FeatureConfig()
        # FALL 확정 시 (fall_count, proba, 시각) 콜백. 블로킹 금지 (큐 적재 수준만 허용)
        self.on_fall = on_fall

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._recent_preds: deque[int] = deque(maxlen=MODE_SIZE)
        self._history: deque[dict[str, Any]] = deque(maxlen=HISTORY_MAXLEN)
        self._state = "IDLE"
        self._cooldown_until = 0.0
        self._fall_count = 0
        self._last_fall_time: float | None = None
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._inference_count = 0
        self._skip_count = 0
        self._latency_ema_ms: float | None = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info(
            "detector start: device=%s threshold=%.3f stride=%.2fs mode=causal%d",
            self.engine.device, self.threshold, self.stride_sec, MODE_SIZE,
        )
        next_tick = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.05))
                continue
            next_tick = max(next_tick + self.stride_sec, now)
            try:
                self._tick()
            except Exception:
                log.exception("detector tick failed")

    def _tick(self) -> None:
        started = time.monotonic()
        times, amps = self.ring.window(self.feature_config.window_seconds + 0.5)
        if times.size == 0:
            self._record_skip("no data")
            return
        try:
            features = extract_window_features(times, amps, self.feature_config)
        except ValueError as error:
            self._record_skip(str(error))
            return
        feature_ms = (time.monotonic() - started) * 1000.0

        infer_started = time.monotonic()
        proba = self.engine.predict(features.s3, features.acf)
        infer_ms = (time.monotonic() - infer_started) * 1000.0

        total_ms = (time.monotonic() - started) * 1000.0

        raw_pred = int(proba >= self.threshold)
        with self._lock:
            self._recent_preds.append(raw_pred)
            majority = (
                int(sum(self._recent_preds) * 2 > len(self._recent_preds))
                if len(self._recent_preds) == MODE_SIZE
                else 0
            )
            self._advance_state(raw_pred, majority, proba)
            self._inference_count += 1
            self._last_error = None
            self._latency_ema_ms = (
                total_ms
                if self._latency_ema_ms is None
                else 0.9 * self._latency_ema_ms + 0.1 * total_ms
            )
            result = {
                "t": time.time(),
                "proba_fall": round(proba, 4),
                "raw_pred": raw_pred,
                "majority_pred": majority,
                "state": self._state,
                "fs_hz": round(features.fs_hz, 2),
                "window_samples": features.window_samples,
                "feature_ms": round(feature_ms, 1),
                "infer_ms": round(infer_ms, 1),
                "total_ms": round(total_ms, 1),
            }
            self._last_result = result
            self._history.append(
                {"t": result["t"], "proba_fall": result["proba_fall"], "state": self._state}
            )
        if total_ms > self.stride_sec * 1000.0:
            log.warning("tick %.0fms exceeds stride %.0fms", total_ms, self.stride_sec * 1000.0)

    def _advance_state(self, raw_pred: int, majority: int, proba: float | None = None) -> None:
        now = time.monotonic()
        if self._state == "COOLDOWN":
            if now >= self._cooldown_until:
                self._state = "FALL" if majority else "SUSPECT" if raw_pred else "IDLE"
            return
        if majority:
            if self._state != "FALL":
                self._fall_count += 1
                self._last_fall_time = time.time()
                log.warning("FALL confirmed (#%d)", self._fall_count)
                self._emit_fall(proba)
            self._state = "FALL"
            return
        if self._state == "FALL":
            # 낙상 종료: 재알람 억제 냉각 구간으로
            self._state = "COOLDOWN"
            self._cooldown_until = now + self.cooldown_seconds
            return
        self._state = "SUSPECT" if raw_pred else "IDLE"

    def _emit_fall(self, proba: float | None) -> None:
        """FALL 확정 시점 콜백 호출. 콜백 오류가 탐지 루프를 깨지 않게 격리한다."""
        if self.on_fall is None:
            return
        try:
            self.on_fall(self._fall_count, proba, self._last_fall_time or time.time())
        except Exception:
            log.exception("on_fall 콜백 실패")

    def _record_skip(self, reason: str) -> None:
        with self._lock:
            self._skip_count += 1
            self._last_error = reason

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": True,
                "device": str(self.engine.device),
                "checkpoint_epoch": self.engine.epoch,
                "threshold": self.threshold,
                "postprocess": f"causal_mode{MODE_SIZE}",
                "state": self._state,
                "fall_count": self._fall_count,
                "last_fall_time": self._last_fall_time,
                "inference_count": self._inference_count,
                "skip_count": self._skip_count,
                "latency_ema_ms": round(self._latency_ema_ms, 1) if self._latency_ema_ms else None,
                "last_error": self._last_error,
                "last": self._last_result,
            }

    def live_payload(self) -> dict[str, Any]:
        """/ws/live에 합쳐 보낼 최소 필드."""
        with self._lock:
            last = self._last_result
            return {
                "detect_state": self._state,
                "proba_fall": last["proba_fall"] if last else None,
                "threshold": self.threshold,
                "fall_count": self._fall_count,
                "last_fall_time": self._last_fall_time,
            }

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)
