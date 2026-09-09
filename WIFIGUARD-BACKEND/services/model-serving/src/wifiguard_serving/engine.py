"""best_model.pt 체크포인트 로드와 단일 윈도우 추론.

infer_validation.py 의 전처리(정규화 상수 적용)와 동일한 경로를 실시간
단건 입력에 맞게 감쌌다. feature_a = S3, feature_b = PCA-ACF.
"""

from __future__ import annotations

import os
import pathlib
from pathlib import Path

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
        config = checkpoint["model_config"]
        self.image_size = int(config["image_size"])
        self.normalization = checkpoint["normalization"]
        self.epoch = int(checkpoint.get("epoch", -1))
        self.checkpoint_path = checkpoint_path
        self.model = DualBranchResNet(
            backbone=str(config["backbone"]),
            embedding_dim=int(config["embedding_dim"]),
            hidden_dim=int(config["fusion_hidden_dim"]),
            dropout=float(config["dropout"]),
        )
        self.model.load_state_dict(checkpoint["model_state_dict"], strict=True)
        self.device = select_device(device)
        self.model.to(self.device)
        self.model.eval()

    def warmup(self) -> None:
        """첫 추론의 커널 컴파일 지연을 미리 치른다."""
        s3 = np.zeros((self.image_size, self.image_size), dtype=np.float32)
        acf = np.zeros((1, 128, 64), dtype=np.float32)
        self.predict(s3, acf)

    @torch.no_grad()
    def predict(self, s3: np.ndarray, acf: np.ndarray) -> float:
        """단일 윈도우 낙상 확률을 반환한다. s3 (224,224), acf (1,128,64)."""
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
