"""Environment-backed settings for the live inference worker."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _float_or_none(name: str) -> float | None:
    value = os.environ.get(name, "").strip()
    return float(value) if value else None


@dataclass(frozen=True)
class InferenceSettings:
    kafka_bootstrap: str
    kafka_group: str
    checkpoint_path: Path
    model_device: str = "auto"
    threshold_override: float | None = None

    @classmethod
    def from_env(cls) -> "InferenceSettings":
        return cls(
            kafka_bootstrap=os.environ.get("KAFKA_BOOTSTRAP", ""),
            kafka_group=os.environ.get("KAFKA_INFERENCE_GROUP", "wifiguard-inference-v1"),
            checkpoint_path=Path(os.environ.get("MODEL_CHECKPOINT", "weights/model.pt")),
            model_device=os.environ.get("MODEL_DEVICE", "auto"),
            threshold_override=_float_or_none("MODEL_THRESHOLD"),
        )

    def validate(self, *, require_checkpoint: bool = True, require_kafka: bool = True) -> None:
        if require_kafka and not self.kafka_bootstrap:
            raise ValueError("KAFKA_BOOTSTRAP is required")
        if require_checkpoint and not self.checkpoint_path.is_file():
            raise FileNotFoundError(
                f"MODEL_CHECKPOINT not found: {self.checkpoint_path}. "
                "Mount the trained model read-only; weights are not included in this repository."
            )
        if self.model_device not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError(f"unsupported MODEL_DEVICE={self.model_device!r}")
        if self.threshold_override is not None and not 0 <= self.threshold_override <= 1:
            raise ValueError("MODEL_THRESHOLD must be between 0 and 1")
