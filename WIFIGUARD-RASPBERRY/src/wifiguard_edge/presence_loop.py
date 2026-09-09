"""움직임(MV)/재실(Presence) 감지 독립 루프.

낙상 DL 추론(detector.FallDetector)과 완전히 무관하게 동작한다 — DL 모델이
로드되지 않았거나(--no-model, 체크포인트 없음, torch 미설치 등) 로드에 실패해도,
시리얼 수신기가 CSI를 흘려보내는 한 이 루프는 계속 돈다.

migration.md/occupation_pipline.md가 요구하는 "재실감지는 DL 모델과 완전히
독립적으로 동작해야 한다"는 원칙을 그대로 구현한 것 — 이전 구현은 이 계산을
FallDetector 안에 얹어 두어 --no-model일 때 재실/움직임 감지 전체가 죽어버리는
결함이 있었다. main.py가 detector 유무와 무관하게 이 루프를 항상 기동한다.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .csi.buffer import RingBuffer
from .presence import PresenceConfig, PresenceDetector, PresenceStatus, compute_final_signal

log = logging.getLogger("presence_loop")

STRIDE_SEC = 0.25


class PresenceLoop(threading.Thread):
    def __init__(
        self,
        ring: RingBuffer,
        config: PresenceConfig,
        stride_sec: float = STRIDE_SEC,
    ) -> None:
        super().__init__(daemon=True, name="presence-loop")
        self.ring = ring
        self.config = config
        self.stride_sec = stride_sec
        self.detector = PresenceDetector(
            mv_threshold=config.presence_mv_threshold,
            wander_baseline=config.wander_baseline,
            wander_ratio_threshold=config.wander_ratio_threshold,
            wander_min_duration_s=config.wander_min_duration_s,
            presence_timeout_s=config.presence_timeout_s,
        )

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last: PresenceStatus | None = None
        self._tick_count = 0
        self._skip_count = 0
        self._last_error: str | None = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info("presence loop start: stride=%.2fs", self.stride_sec)
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
                log.exception("presence tick failed")

    def _tick(self) -> None:
        cfg = self.config
        # 캘리브레이션(onboarding.run_calibration)이 갱신했을 수 있는 값을 매 틱 반영
        self.detector.set_thresholds(
            cfg.presence_mv_threshold,
            cfg.wander_baseline,
            cfg.wander_ratio_threshold,
            cfg.wander_min_duration_s,
        )
        self.detector.presence_timeout_s = cfg.presence_timeout_s

        mv_window = self.ring.get_window(cfg.window_sec)
        if mv_window is None:
            self._record_skip("no data")
            return
        mv_times_s, mv_amp = mv_window
        mv_result = compute_final_signal(
            mv_times_s * 1e6,
            mv_amp,
            window_sec=cfg.window_sec,
            stride_sec=cfg.stride_sec,
            fs_hz=cfg.fs_hz,
            omega=cfg.omega,
            n_streams=cfg.n_streams,
            bandpass_low=cfg.bandpass_low,
            bandpass_high=cfg.bandpass_high,
            bandpass_order=cfg.bandpass_order,
        )
        if mv_result is None:
            self._record_skip("insufficient window for MV")
            return

        wander_current = 0.0
        wander_window = self.ring.get_window(cfg.wander_window_sec)
        if wander_window is not None:
            w_times_s, w_amp = wander_window
            wander_result = compute_final_signal(
                w_times_s * 1e6,
                w_amp,
                window_sec=cfg.wander_window_sec,
                stride_sec=cfg.stride_sec,
                fs_hz=cfg.fs_hz,
                omega=cfg.wander_omega,
                n_streams=cfg.n_streams,
                bandpass_low=cfg.wander_prefilter_low,
                bandpass_high=cfg.wander_prefilter_high,
                bandpass_order=cfg.bandpass_order,
                compute_band_energy=True,
                energy_band_low=cfg.wander_bandpass_low,
                energy_band_high=cfg.wander_bandpass_high,
            )
            if wander_result is not None and wander_result.band_energy is not None:
                wander_current = wander_result.band_energy

        status = self.detector.update(time.time(), mv_result.mv_current, wander_current)
        with self._lock:
            self._last = status
            self._tick_count += 1
            self._last_error = None

    def _record_skip(self, reason: str) -> None:
        with self._lock:
            self._skip_count += 1
            self._last_error = reason

    def _payload(self) -> dict[str, Any]:
        """`_lock` 보유 없이 호출해도 안전 — 아래에서 잠깐씩만 잡는다.
        reference/fall_detect/API.md의 DetectionInfo와 동일한 필드명을 쓴다."""
        with self._lock:
            p = self._last
            cfg = self.config
            if p is None:
                return {
                    "presence_state": None,
                    "mv_current": None,
                    "presence_mv_threshold": cfg.presence_mv_threshold,
                    "wander_current": None,
                    "wander_baseline": cfg.wander_baseline,
                    "wander_ratio_threshold": cfg.wander_ratio_threshold,
                    "wander_ratio": None,
                    "wander_confirmed": None,
                    "last_activity_at": None,
                    "presence_just_changed": None,
                }
            return {
                "presence_state": p.state.value,
                "mv_current": p.mv_current,
                "presence_mv_threshold": p.mv_threshold,
                "wander_current": p.wander_current,
                "wander_baseline": p.wander_baseline,
                "wander_ratio_threshold": p.wander_ratio_threshold,
                "wander_ratio": p.wander_ratio,
                "wander_confirmed": p.wander_confirmed,
                "last_activity_at": p.last_activity_at,
                "presence_just_changed": p.just_changed,
            }

    def live_payload(self) -> dict[str, Any]:
        """/ws/live에 합쳐 보낼 필드."""
        return self._payload()

    def status(self) -> dict[str, Any]:
        with self._lock:
            tick_count = self._tick_count
            skip_count = self._skip_count
            last_error = self._last_error
        return {
            "enabled": True,
            "tick_count": tick_count,
            "skip_count": skip_count,
            "last_error": last_error,
            **self._payload(),
        }
