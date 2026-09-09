"""
Mendeley CWT S3 + PCA-ACF 배치 추출. streamlit 을 import 하지 않는다.

per-(파일, rx) 로 `src.dwt_coef.mendeley_cwt_acf.extract_cwt_acf` 를 돌리고, 배열은
`array_cache` 에 npz 로, 스칼라 메타는 인덱스 parquet 으로 쌓는다.

**추출 단계에는 누수가 없다**: S3 의 max 정규화도 ACF 의 percentile 클립도 전부 샘플
내부에서 끝난다. 교차 샘플 통계를 추정하는 것은 뒤의 `embed.make_pipeline` 뿐이고,
그건 CV 폴드 안에서만 적합된다.
"""

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.dwt_coef.features import FeatureConfig
from src.dwt_coef.mendeley_cwt_acf import extract_cwt_acf

from lib.array_cache import (
    append_index,
    cache_status,
    load_arrays,
    load_index,
    missing_keys,
    sample_key,
    save_sample,
)

#: rx 는 행을 늘리지만 **유효 표본(피험자 수)은 늘리지 않는다.** 예측은 파일 단위로
#: 합치고 CV 그룹은 피험자로 잡는다 — 이 상수는 UI 문구와 기본값을 위해 둔다.
RX_CHOICES: Tuple[int, ...] = (1, 2, 3)


@dataclass(frozen=True)
class DeepFeatureParams:
    """캐시 해시의 입력. 전부 스칼라라 그대로 params_hash 로 넘어간다."""
    fs_hz: float = 320.0
    interp_tol_ms: float = 2.0
    max_interp_gap_steps: int = 64
    wm_sec: float = 1.0
    segment_sec: float = 3.0
    omega: int = 30
    w_radius_sec: float = 0.4
    image_size: int = 224
    freq_min_hz: float = 1.0
    freq_max_hz: float = 170.0
    th_scmax: float = 1.0
    kappa: float = 1.0
    carrier_hz: float = 2.4e9
    acf_lag_seconds: float = 0.4
    acf_lag_output_bins: int = 128
    acf_time_bins: int = 64
    acf_clip_percentile: float = 99.5

    def to_feature_config(self) -> FeatureConfig:
        return FeatureConfig(
            window_seconds=self.segment_sec,
            omega=self.omega,
            moving_variance_radius_seconds=self.w_radius_sec,
            freq_min_hz=self.freq_min_hz,
            freq_max_hz=self.freq_max_hz,
            image_size=self.image_size,
            th_scmax=self.th_scmax,
            kappa=self.kappa,
            carrier_hz=self.carrier_hz,
            acf_lag_seconds=self.acf_lag_seconds,
            acf_lag_output_bins=self.acf_lag_output_bins,
            acf_time_bins=self.acf_time_bins,
            acf_clip_percentile=self.acf_clip_percentile,
        )

    def fingerprint(self) -> Dict[str, Any]:
        return {"pass": "cwt_acf", **asdict(self)}


def extract_one(filepath: str, rx: int, params: DeepFeatureParams,
                keep_signal: bool = False) -> Dict[str, Any]:
    return extract_cwt_acf(
        filepath, rx, config=params.to_feature_config(), fs_hz=params.fs_hz,
        interp_tol_ms=params.interp_tol_ms,
        max_interp_gap_steps=params.max_interp_gap_steps,
        wm_sec=params.wm_sec, keep_signal=keep_signal,
    )


def extract_batch(
    filepaths: Sequence[str],
    rxs: Sequence[int],
    params: DeepFeatureParams,
    cache_dir: str,
    hash8: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    extractor: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Tuple[np.ndarray, np.ndarray, pd.DataFrame, List[str]]:
    """
    캐시 조회 -> 미스만 계산 -> npz + 인덱스 병합 -> 요청 순서대로 배열을 쌓아 반환.

    한 파일이 깨져도 배치를 중단하지 않는다.

    Returns:
        (s3 (n,224,224) f16, acf (n,128,64) f16, rows DataFrame, errors)
    """
    run = extractor or (lambda fp, rx: extract_one(fp, rx, params))

    wanted = [(str(fp), int(rx)) for fp in filepaths for rx in rxs]
    keys = [sample_key(fp, rx) for fp, rx in wanted]
    key_to_pair = dict(zip(keys, wanted))

    miss = missing_keys(cache_dir, hash8, keys)
    errors: List[str] = []
    new_rows: List[Dict[str, Any]] = []

    total = len(miss)
    for i, key in enumerate(miss):
        fp, rx = key_to_pair[key]
        if progress_callback:
            progress_callback(i, total, key)
        try:
            res = run(fp, rx)
            save_sample(cache_dir, hash8, key, res["s3"], res["acf"])
            new_rows.append({"sample_key": key, **res["meta"]})
        except Exception as exc:  # noqa: BLE001 - 배치는 계속 진행되어야 한다
            errors.append(f"{key}: {type(exc).__name__}: {exc}")

    if progress_callback:
        progress_callback(total, total, "완료" if total else "캐시 적중")

    if new_rows:
        append_index(cache_dir, hash8, pd.DataFrame(new_rows))

    index = load_index(cache_dir, hash8)
    if index.empty:
        return (np.zeros((0, params.image_size, params.image_size), dtype=np.float16),
                np.zeros((0, params.acf_lag_output_bins, params.acf_time_bins), dtype=np.float16),
                pd.DataFrame(), errors)

    # 요청한 키 중 실제로 캐시에 있는 것만, 요청 순서를 유지해 정렬한다.
    have = set(index["sample_key"])
    ordered = [k for k in keys if k in have]
    rows = index.set_index("sample_key").loc[ordered].reset_index()

    s3, acf = load_arrays(cache_dir, hash8, ordered)
    return s3, acf, rows, errors


def batch_status(cache_dir: str, hash8: str, filepaths: Sequence[str],
                 rxs: Sequence[int]) -> Dict[str, Any]:
    keys = [sample_key(fp, rx) for fp in filepaths for rx in rxs]
    return cache_status(cache_dir, hash8, keys)


def qc_summary(rows: pd.DataFrame) -> pd.DataFrame:
    """
    환경별 피처 품질 요약 — 정확도를 보기 **전에** 확인해야 하는 표.

    crop 이 가장자리에 붙었거나(pad>0) S3 가 거의 비었/찼으면 그 샘플의 스케일로그램은
    다른 종류의 물건이다. 환경별로 이 비율이 다르면 "도메인 시프트"가 아니라
    추출 실패율 차이를 재고 있을 수 있다.
    """
    if rows.empty:
        return pd.DataFrame()

    def _degenerate(g: pd.DataFrame) -> float:
        r = g["s3_nonzero_ratio"]
        return float(((r < 0.01) | (r > 0.9)).mean())

    out = rows.groupby("env").apply(
        lambda g: pd.Series({
            "n": len(g),
            "crop_padded_rate": float(g["crop_padded"].mean()),
            "s3_degenerate_rate": _degenerate(g),
            "s3_nonzero_ratio": float(g["s3_nonzero_ratio"].median()),
            "s3_energy_above_80hz": float(g["s3_energy_above_80hz"].median()),
            "interp_ratio": float(g["interp_ratio"].median()),
            "nonpositive_ratio": float(g["nonpositive_ratio"].median()),
            "selected_stream_count": float(g["selected_stream_count"].median()),
            "signal_q": float(g["signal_q"].median()) if "signal_q" in g else float("nan"),
        }),
        include_groups=False,
    ).reset_index()
    return out
