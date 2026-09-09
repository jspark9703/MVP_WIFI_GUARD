"""@st.cache_data 래퍼.

캐시 키는 스칼라/튜플만 쓴다 (numpy 배열 금지). FeatureConfig 는 frozen
dataclass 라 해시 가능하지만, 파라미터를 개별 스칼라로 펼쳐야 사이드바
위젯 하나만 바꿨을 때 나머지 캐시가 유지된다.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from src.dwt_coef.features import FeatureConfig
from src.dwt_coef.inhouse_features import (
    extract_recording_features,
    fresnel_group_label,
    load_resampled_recording,
)
from src.dwt_coef.inhouse_loader import get_inhouse_file_index


@st.cache_data(show_spinner=False)
def get_file_index_cached(raw_root: str, labeled_only: bool = True) -> pd.DataFrame:
    """data/raw/ 파일 인덱스. F 조건은 _labeled 파일(Q1~Q6)에만 있다."""
    index = get_inhouse_file_index(raw_root)
    if labeled_only:
        index = index[index["has_label"]].reset_index(drop=True)
        # _csi 파일의 None 때문에 float64 가 된 F 컬럼을 int 로 되돌린다.
        for col in ("fresnel_a", "fresnel_b"):
            index[col] = index[col].astype(int)
    index["filepath_str"] = index["filepath"].astype(str)
    index["name"] = index["filepath"].apply(lambda p: Path(p).stem)
    return index


def _build_config(
    target_subcarriers: int,
    omega: int,
    th_scmax: float,
    kappa: float,
    acf_lag_seconds: float,
) -> FeatureConfig:
    return FeatureConfig(
        target_subcarriers=target_subcarriers,
        omega=omega,
        th_scmax=th_scmax,
        kappa=kappa,
        acf_lag_seconds=acf_lag_seconds,
    )


@st.cache_data(show_spinner=False, max_entries=64)
def extract_recording_cached(
    filepath: str,
    rule: str,
    stride_sec: float,
    max_windows: int,
    target_subcarriers: int,
    omega: int,
    th_scmax: float,
    kappa: float,
    acf_lag_seconds: float,
    stages: bool = False,
    fall_only: bool = True,
) -> Dict[str, Any]:
    """레코딩 1개의 피처. 페이지에서 진행률을 직접 그리도록 스피너는 끈다.

    fall_only=False 면 낙상 라벨과 무관하게 전 구간 윈도우를 처리한다
    (Page 4 - 일상행동 파일은 낙상 윈도우가 아예 없다).
    """
    config = _build_config(target_subcarriers, omega, th_scmax, kappa, acf_lag_seconds)
    return extract_recording_features(
        filepath,
        config=config,
        rule=rule,
        stride_sec=stride_sec,
        max_windows=max_windows if max_windows > 0 else None,
        fall_only=fall_only,
        stages=stages,
    )


def extract_batch(
    filepaths: List[str],
    group_mode: str,
    progress_callback=None,
    **feature_kwargs: Any,
) -> Tuple[np.ndarray, np.ndarray, pd.DataFrame, List[str]]:
    """여러 레코딩의 윈도우를 모아 하나의 배열로 쌓는다.

    레코딩 단위 캐시(extract_recording_cached)를 그대로 쓰므로, 파일 목록이
    바뀌어도 이미 계산된 레코딩은 즉시 반환된다.

    Args:
        filepaths: 처리할 CSV 경로 문자열 목록
        group_mode: "a" (F1/F2/F3) 또는 "ab" (F1-1..F3-3)
        progress_callback: (done, total, name) -> None
        **feature_kwargs: extract_recording_cached 에 넘길 스칼라 파라미터

    Returns:
        (s3 (K,224,224), acf (K,128,64), meta DataFrame, errors)
        meta 컬럼: source_file, name, person, take, fresnel_a, fresnel_b,
                   f_group, window_index, window_start_sec, signal_q,
                   s3_nonzero_ratio, effective_freq_max_hz, fs_hz
    """
    s3_chunks: List[np.ndarray] = []
    acf_chunks: List[np.ndarray] = []
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    for done, filepath in enumerate(filepaths):
        name = Path(filepath).stem
        if progress_callback is not None:
            progress_callback(done, len(filepaths), name)
        try:
            result = extract_recording_cached(filepath, **feature_kwargs)
        except Exception as exc:  # 한 파일 실패가 전체를 막지 않도록
            errors.append(f"{name}: {exc}")
            continue
        if len(result["s3"]) == 0:
            errors.append(f"{name}: 낙상 윈도우 없음")
            continue

        s3_chunks.append(result["s3"])
        acf_chunks.append(result["acf"])
        for i, stats in enumerate(result["per_window_stats"]):
            rows.append(
                {
                    "source_file": result["source_file"],
                    "name": name,
                    "person": result["person"],
                    "take": result["take"],
                    "fresnel_a": result["fresnel_a"],
                    "fresnel_b": result["fresnel_b"],
                    "f_group": fresnel_group_label(
                        result["fresnel_a"], result["fresnel_b"], mode=group_mode
                    ),
                    "file_label": result["file_label"],
                    "window_index": int(result["window_index"][i]),
                    "window_start_sec": float(result["window_start_sec"][i]),
                    "fs_hz": result["fs_hz"],
                    "signal_q": float(stats.get("signal_q", np.nan)),
                    "s3_nonzero_ratio": float(stats.get("s3_nonzero_ratio", np.nan)),
                    "effective_freq_max_hz": float(stats.get("effective_freq_max_hz", np.nan)),
                }
            )

    if progress_callback is not None:
        progress_callback(len(filepaths), len(filepaths), "done")

    if not s3_chunks:
        empty_s3 = np.empty((0, 224, 224), dtype=np.float32)
        empty_acf = np.empty((0, 128, 64), dtype=np.float32)
        return empty_s3, empty_acf, pd.DataFrame(rows), errors

    return (
        np.concatenate(s3_chunks, axis=0),
        np.concatenate(acf_chunks, axis=0),
        pd.DataFrame(rows),
        errors,
    )


@st.cache_data(show_spinner="RAW CSI 로드 중...", max_entries=8)
def load_recording_raw_cached(filepath: str, target_subcarriers: int) -> Dict[str, Any]:
    """RAW CSI 시각화용 리샘플 결과. 피처 추출과 같은 앞단을 공유한다."""
    config = FeatureConfig(target_subcarriers=target_subcarriers)
    return load_resampled_recording(filepath, config)


@st.cache_data(show_spinner=False)
def scalogram_freq_axis(fs_hz: float, freq_min_hz: float, freq_max_hz: float, image_size: int) -> np.ndarray:
    """S3 행에 대응하는 주파수 (행 0 = 고주파). Nyquist 로 클램프된다."""
    effective_max = min(freq_max_hz, fs_hz / 2.0 - 1e-6)
    return np.linspace(freq_min_hz, effective_max, image_size, dtype=np.float64)[::-1]
