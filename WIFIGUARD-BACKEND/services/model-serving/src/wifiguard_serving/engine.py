"""신뢰된 체크포인트 로드와 단일 라이브 윈도우 추론.

지원하는 체크포인트는 두 종류다.

* 기존 ``best_model.pt``: ``model_config/model_state_dict`` 형식
* 통합된 ``csi-fall-pipeline``: ``config/model`` 형식. 라이브 경로와 피처가
  동일한 ``feature=legacy_map, cwt=true`` 조합만 허용한다.

실제 가중치는 저장소에 넣지 않는다. 운영에서는 ``MODEL_CHECKPOINT``가 가리키는
읽기 전용 볼륨으로 주입한다.
"""

from __future__ import annotations

import os
import pathlib
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import torch

from .model import DualBranchResNet

DEFAULT_CHECKPOINT = (
    Path(__file__).resolve().parents[2] / "Window3BestModelInference" / "weights" / "best_model.pt"
)


def select_device(requested: str = "auto") -> torch.device:
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable")
        return torch.device("cuda")
    if requested == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS requested but unavailable")
        return torch.device("mps")
    if requested == "cpu":
        return torch.device("cpu")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class FallInferenceEngine:
    def __init__(self, checkpoint_path: Path | str = DEFAULT_CHECKPOINT, device: str = "auto") -> None:
        checkpoint_path = Path(checkpoint_path)
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"checkpoint not found: {checkpoint_path}")
        # 체크포인트가 Linux에서 저장돼 PosixPath로 피클링된 경우 Windows에서
        # 언피클링이 실패하므로 로드 중에만 임시로 WindowsPath로 대체한다.
        posix_path_backup = pathlib.PosixPath
        if os.name == "nt":
            pathlib.PosixPath = pathlib.WindowsPath
        try:
            checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        finally:
            pathlib.PosixPath = posix_path_backup
        self.checkpoint_path = checkpoint_path
        self.device = select_device(device)
        self._schema: str
        self.threshold: float
        self.epoch = int(checkpoint.get("epoch", -1))

        if {"model_config", "model_state_dict", "normalization"} <= checkpoint.keys():
            self._load_legacy(checkpoint)
        elif {"config", "model", "normalization"} <= checkpoint.keys():
            self._load_pipeline(checkpoint)
        else:
            raise ValueError(
                "unsupported checkpoint schema: expected model_config/model_state_dict "
                "or config/model/normalization"
            )

        self.model.to(self.device)
        self.model.eval()
        digest = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()[:12]
        version = checkpoint.get("version", 1)
        self.model_version = f"{self._schema}:v{version}:{digest}"

    def _load_legacy(self, checkpoint: dict[str, Any]) -> None:
        config = checkpoint["model_config"]
        self._schema = "dual-branch-resnet"
        self.image_size = int(config["image_size"])
        self.normalization = checkpoint["normalization"]
        self.threshold = float(checkpoint.get("threshold", 0.468))
        self.model = DualBranchResNet(
            backbone=str(config["backbone"]),
            embedding_dim=int(config["embedding_dim"]),
            hidden_dim=int(config["fusion_hidden_dim"]),
            dropout=float(config["dropout"]),
        )
        self.model.load_state_dict(checkpoint["model_state_dict"], strict=True)

    def _load_pipeline(self, checkpoint: dict[str, Any]) -> None:
        from csi_fall.models import FallModel

        config = checkpoint["config"]
        if config.get("feature") != "legacy_map" or config.get("cwt") is not True:
            raise ValueError(
                "live SignalMsg compatibility requires checkpoint config "
                "feature='legacy_map' and cwt=true"
            )
        self._schema = "csi-fall-pipeline"
        self.image_size = int(config.get("image_size", 224))
        self.normalization = checkpoint["normalization"]
        self.threshold = float(checkpoint.get("threshold", 0.5))
        self.model = FallModel(
            kind="legacy_map",
            cwt=True,
            backbone=str(config.get("backbone", "compact")),
            pretrained=False,
        )
        self.model.load_state_dict(checkpoint["model"], strict=True)

    def warmup(self) -> None:
        """첫 추론의 커널 컴파일 지연을 미리 치른다."""
        s3 = np.zeros((self.image_size, self.image_size), dtype=np.float32)
        acf = np.zeros((1, 128, 64), dtype=np.float32)
        self.predict(s3, acf)

    @torch.no_grad()
    def predict(self, s3: np.ndarray, acf: np.ndarray) -> float:
        """단일 윈도우 낙상 확률을 반환한다. s3 (224,224), acf (1,128,64)."""
        if s3.shape != (self.image_size, self.image_size):
            raise ValueError(f"unexpected S3 shape {s3.shape}")
        if acf.shape != (1, 128, 64):
            raise ValueError(f"unexpected ACF shape {acf.shape}")
        if self._schema == "csi-fall-pipeline":
            return self._predict_pipeline(s3, acf)

        s3_norm = self.normalization["feature_a"]
        acf_norm = self.normalization["feature_b"]
        s3_in = (s3.astype(np.float32)[None, None, :, :] - float(s3_norm["mean"])) / max(
            float(s3_norm["std"]), 1e-6
        )
        acf_in = (acf.astype(np.float32)[None, :, :, :] - float(acf_norm["mean"])) / max(
            float(acf_norm["std"]), 1e-6
        )
        output = self.model(
            torch.from_numpy(s3_in).to(self.device),
            torch.from_numpy(acf_in).to(self.device),
            image_size=self.image_size,
        )
        proba = torch.softmax(output["logits"], dim=1)[0, 1]
        return float(proba.cpu())

    def _predict_pipeline(self, s3: np.ndarray, acf: np.ndarray) -> float:
        s3_norm = self.normalization["cwt"]
        acf_norm = self.normalization["secondary"]
        s3_in = (s3.astype(np.float32)[None, None] - float(s3_norm["mean"])) / max(
            float(s3_norm["std"]), 1e-6
        )
        acf_in = (acf.astype(np.float32)[None] - float(acf_norm["mean"])) / max(
            float(acf_norm["std"]), 1e-6
        )
        logits, _embedding, _auxiliary = self.model(
            torch.from_numpy(acf_in).to(self.device),
            torch.from_numpy(s3_in).to(self.device),
        )
        return float(torch.softmax(logits, dim=1)[0, 1].cpu())
