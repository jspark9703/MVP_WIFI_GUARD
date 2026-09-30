"""Per-device causal fall state derived from model probabilities."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from uuid import UUID

from wifiguard_contracts.kafka import InferenceResult

MODE_SIZE = 5


@dataclass(frozen=True)
class FallUpdate:
    fields: dict
    new_fall: bool


@dataclass
class _Tracker:
    cooldown_seconds: float
    recent: deque[int] = field(default_factory=lambda: deque(maxlen=MODE_SIZE))
    state: str = "IDLE"
    cooldown_until: float = 0.0
    fall_count: int = 0
    last_fall_time: float | None = None
    seen: deque[tuple[int, str]] = field(default_factory=lambda: deque(maxlen=256))

    def apply(self, result: InferenceResult) -> FallUpdate | None:
        identity = (result.seq, result.ts.isoformat())
        if identity in self.seen:
            return None
        self.seen.append(identity)

        raw = int(result.proba_fall >= result.threshold)
        if result.decision is None:
            self.recent.append(raw)
            majority = len(self.recent) == MODE_SIZE and sum(self.recent) >= 3
            applied_postprocess = "causal_mode5"
        else:
            # segmentation_a/b는 모델 패키지가 현재·과거 window만 사용해 이미
            # 인과 판정을 끝냈다. 여기에 mode-5를 다시 적용하면 의미와 지연이 바뀐다.
            majority = bool(result.decision)
            applied_postprocess = result.postprocess
        now = result.inferred_at.timestamp()
        new_fall = False

        if self.state == "COOLDOWN" and now < self.cooldown_until:
            pass
        elif majority:
            if self.state != "FALL":
                self.fall_count += 1
                self.last_fall_time = result.ts.timestamp()
                new_fall = True
            self.state = "FALL"
        elif self.state == "FALL":
            self.state = "COOLDOWN"
            self.cooldown_until = now + self.cooldown_seconds
        else:
            self.state = "SUSPECT" if raw else "IDLE"

        return FallUpdate(
            fields={
                "detect_state": self.state,
                "proba_fall": result.proba_fall,
                "threshold": result.threshold,
                "postprocess": applied_postprocess,
                "fall_count": self.fall_count,
                "last_fall_time": self.last_fall_time,
            },
            new_fall=new_fall,
        )


class FallStateManager:
    """Thread-safe collection of independent per-device state machines."""

    def __init__(self, cooldown_seconds: float = 10.0) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._trackers: dict[UUID, _Tracker] = {}

    def apply(self, result: InferenceResult) -> FallUpdate | None:
        with self._lock:
            tracker = self._trackers.setdefault(
                result.device_id, _Tracker(cooldown_seconds=self.cooldown_seconds)
            )
            return tracker.apply(result)
