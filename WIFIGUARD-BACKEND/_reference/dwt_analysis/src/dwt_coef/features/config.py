"""모델 호환 피처 파라미터와 네이티브 fs 리샘플링.

`Guardian Angel Alert/backend/features/realtime.py` 에서 `FeatureConfig` 와
전처리 헬퍼만 옮겨왔다. 원본의 `extract_window_features` 는 링버퍼의 마지막
윈도우 하나만 처리하므로 가져오지 않는다 - 오프라인 슬라이딩 경로는
`src/dwt_coef/inhouse_features.py` 에 있다.

파라미터 값은 학습 시 설정과 일치해야 하므로 바꾸지 말 것.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FeatureConfig:
    """모델 호환 파라미터. 학습 시 설정과 일치해야 한다."""

    window_seconds: float = 3.0
    target_subcarriers: int = 30
    # 수집 CSV는 raw 245쌍에서 121,122를 제외하고 30개를 균등 선택했다.
    train_raw_pairs: int = 245
    train_drop_pair_indices: tuple[int, ...] = (121, 122)
    omega: int = 30
    moving_variance_radius_seconds: float = 0.4
    freq_min_hz: float = 1.0
    freq_max_hz: float = 170.0
    image_size: int = 224
    th_scmax: float = 1.0
    kappa: float = 1.0
    carrier_hz: float = 2.4e9
    acf_lag_seconds: float = 0.4
    acf_lag_output_bins: int = 128
    acf_time_bins: int = 64
    acf_clip_percentile: float = 99.5
    # 측정 fs 양자화 간격. 미세하게 다른 측정값을 격자에 스냅해 CWT scale
    # 캐시가 적중하게 한다. 오프라인 파이프라인도 recording 전체 중앙값
    # 하나를 쓰므로 의미상 동일하다.
    fs_quantize_hz: float = 0.25


def select_subcarrier_indices(n_available: int, config: FeatureConfig) -> np.ndarray:
    """학습과 동일한 서브캐리어 선택 규칙 (245쌍 -> 121/122 제외 -> 30개 균등).

    Args:
        n_available: 입력 진폭의 열 수. 학습 수집과 같은 245면 동일 인덱스가 나온다.
        config: FeatureConfig

    Returns:
        오름차순 정렬된 슬롯 인덱스 배열 (보통 30개)

    Raises:
        ValueError: 선택 가능한 서브캐리어가 없는 경우
    """
    dropped = set(config.train_drop_pair_indices) if n_available == config.train_raw_pairs else set()
    candidates = np.asarray([idx for idx in range(n_available) if idx not in dropped], dtype=np.int32)
    target = min(config.target_subcarriers, len(candidates))
    if target <= 0:
        raise ValueError(f"no subcarriers available (n_available={n_available})")
    positions = np.round(np.linspace(0, len(candidates) - 1, target)).astype(np.int32)
    return candidates[np.unique(positions)]


def measure_native_fs(times: np.ndarray) -> float:
    """타임스탬프 간격 중앙값에서 실효 샘플링 레이트를 구한다."""
    diffs = np.diff(times)
    positive = diffs[diffs > 0]
    if len(positive) == 0:
        return float("nan")
    return float(1.0 / np.median(positive))


def quantize_fs(fs_hz: float, config: FeatureConfig) -> float:
    """측정 fs 를 fs_quantize_hz 격자에 스냅한다 (CWT scale 캐시 적중률용)."""
    if not np.isfinite(fs_hz) or fs_hz <= 0:
        raise ValueError(f"invalid native fs {fs_hz}")
    if config.fs_quantize_hz > 0:
        return max(config.fs_quantize_hz, round(fs_hz / config.fs_quantize_hz) * config.fs_quantize_hz)
    return fs_hz


def resample_uniform(times: np.ndarray, amplitude: np.ndarray, fs_hz: float) -> np.ndarray:
    """타임스탬프 기준 균일 그리드 선형 보간 (오프라인 resample_recording과 동일).

    Args:
        times: 단조 증가 초 단위 타임스탬프 (N,)
        amplitude: (N, n_streams)
        fs_hz: 목표 샘플링 레이트

    Returns:
        (grid_count, n_streams) float32
    """
    grid_step = 1.0 / fs_hz
    grid_count = int(math.floor((times[-1] - times[0]) / grid_step)) + 1
    grid = times[0] + np.arange(grid_count, dtype=np.float64) * grid_step
    resampled = np.empty((grid_count, amplitude.shape[1]), dtype=np.float32)
    for stream_idx in range(amplitude.shape[1]):
        resampled[:, stream_idx] = np.interp(grid, times, amplitude[:, stream_idx]).astype(np.float32)
    return resampled


def drop_nonmonotonic(times: np.ndarray, amplitude: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """중복/역행 타임스탬프 제거 (오프라인 load_recording과 동일한 정리)."""
    unique_mask = np.ones(len(times), dtype=bool)
    unique_mask[1:] = np.diff(times) > 0
    return times[unique_mask], amplitude[unique_mask]


def ssqueezepy_available() -> bool:
    """ssqueezepy 경로가 쓰이는지 (False 면 common.fallback_cwt 근사 경로)."""
    try:
        import ssqueezepy  # noqa: F401

        return True
    except ModuleNotFoundError:
        return False
