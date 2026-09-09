"""디노이즈 중간 단계(S0~S3)를 모두 반환하는 스칼로그램 계산.

`common.compute_s3_scalogram` 은 최종 s3 만 반환하므로 시각화용으로는 부족하다.
원본 `amfall_losnlos_common.compute_scalogram_stages` 를 그대로 옮기되,
scale 계산은 `common._cached_wavelet_scales` 를 재사용한다 (freq_to_scale 이
호출당 ~0.5초라 캐시가 필수).

수치는 `compute_s3_scalogram` 과 동일해야 한다 - `stages["s3"]` 가
`compute_s3_scalogram(...)[0]` 과 일치하는지 테스트로 확인할 것.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np

from .common import (
    _cached_wavelet_scales,
    fallback_cwt,
    general_denoise,
    horizontal_denoise,
    q_metric,
    resize_time_axis,
    vertical_denoise,
)

STAGE_NAMES = ("s0", "s1", "s2", "s3")

STAGE_DESCRIPTIONS = {
    "s0": "정규화된 CWT 크기 |W(a,b)|",
    "s1": "general denoise - 행 평균 * th_sc 미만 제거",
    "s2": "vertical denoise - 주파수축 연속성 필터",
    "s3": "horizontal denoise - 최소 지속시간 필터",
}


def compute_scalogram_stages(
    signal: np.ndarray,
    fs_hz: float,
    freq_min_hz: float,
    freq_max_hz: float,
    image_size: int,
    th_scmax: float,
    kappa: float,
    carrier_hz: float,
    wavelet_name: str = "gmw",
) -> Tuple[Dict[str, np.ndarray], Dict[str, Any]]:
    """S0~S3 디노이즈 단계를 모두 반환한다.

    Args:
        signal: 1D PCA motion signal
        fs_hz: 샘플링 레이트 (네이티브 fs)
        freq_min_hz / freq_max_hz: 주파수 범위. 상한은 Nyquist 로 클램프됨
        image_size: 주파수 bin 수이자 출력 시간축 bin 수 (224)
        th_scmax / kappa / carrier_hz: 디노이즈 파라미터

    Returns:
        (stages, meta) - stages 는 각 (image_size, image_size) float32,
        meta 는 signal_q / effective_freq_max_hz / 단계별 nonzero 비율
    """
    effective_freq_max = min(freq_max_hz, fs_hz / 2.0 - 1e-6)
    freqs_low_to_high = np.linspace(freq_min_hz, effective_freq_max, image_size, dtype=np.float64)
    freqs = freqs_low_to_high[::-1]
    try:
        from ssqueezepy import cwt

        wavelet, scales = _cached_wavelet_scales(
            wavelet_name, len(signal), fs_hz, freqs_low_to_high, effective_freq_max, image_size
        )
        wx, _ = cwt(
            signal.astype(np.float64),
            wavelet=wavelet,
            scales=scales,
            fs=fs_hz,
            l1_norm=True,
            astensor=False,
        )
    except ModuleNotFoundError:
        wx = fallback_cwt(signal=signal.astype(np.float64), freqs=freqs, fs_hz=fs_hz)

    s0 = np.abs(wx).astype(np.float32)
    max_value = float(np.max(s0))
    if max_value > 0:
        s0 /= max_value
    signal_q = q_metric(signal, max(1, int(round(0.4 * fs_hz))))
    s1 = general_denoise(s0, signal_q, th_scmax=th_scmax)
    d_hz_per_step = 170.0 / fs_hz
    s2 = vertical_denoise(s1, freqs, d_hz_per_step=d_hz_per_step)
    s3 = horizontal_denoise(s2, freqs, fs_hz=fs_hz, carrier_hz=carrier_hz, kappa=kappa)

    stages = {
        "s0": resize_time_axis(s0, image_size).astype(np.float32),
        "s1": resize_time_axis(s1, image_size).astype(np.float32),
        "s2": resize_time_axis(s2, image_size).astype(np.float32),
        "s3": resize_time_axis(s3, image_size).astype(np.float32),
    }
    meta = {
        "signal_q": signal_q,
        "effective_freq_max_hz": float(effective_freq_max),
        "s0_nonzero_ratio": float(np.mean(s0 > 0)),
        "s1_nonzero_ratio": float(np.mean(s1 > 0)),
        "s2_nonzero_ratio": float(np.mean(s2 > 0)),
        "s3_nonzero_ratio": float(np.mean(s3 > 0)),
        "freqs_high_to_low": freqs.astype(np.float32),
    }
    return stages, meta
