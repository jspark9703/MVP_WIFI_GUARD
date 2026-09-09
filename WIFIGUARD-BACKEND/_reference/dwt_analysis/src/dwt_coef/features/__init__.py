"""CWT S3 스칼로그램 + PCA-ACF 피처.

`common.py` / `acf.py` 는 `Guardian Angel Alert/backend/features/` 에서 byte-identical
복사본이다 (원본 연구 프로젝트 ACF_Scalogram_FeatureExtraction 은 이 머신에 없음).
수정하지 말 것 - 학습된 모델의 피처 분포와 어긋난다.

`config.py` (파라미터/리샘플)와 `stages.py` (S0~S3 시각화용)는 이 레포에서 추가했다.
"""

from . import acf, common, config, stages
from .acf import compute_pca_acf
from .common import compute_s3_scalogram, select_pc_signal, select_streams
from .config import (
    FeatureConfig,
    drop_nonmonotonic,
    measure_native_fs,
    quantize_fs,
    resample_uniform,
    select_subcarrier_indices,
    ssqueezepy_available,
)
from .stages import STAGE_DESCRIPTIONS, STAGE_NAMES, compute_scalogram_stages

__all__ = [
    "acf",
    "common",
    "config",
    "stages",
    "compute_pca_acf",
    "compute_s3_scalogram",
    "compute_scalogram_stages",
    "select_pc_signal",
    "select_streams",
    "FeatureConfig",
    "drop_nonmonotonic",
    "measure_native_fs",
    "quantize_fs",
    "resample_uniform",
    "select_subcarrier_indices",
    "ssqueezepy_available",
    "STAGE_NAMES",
    "STAGE_DESCRIPTIONS",
]
