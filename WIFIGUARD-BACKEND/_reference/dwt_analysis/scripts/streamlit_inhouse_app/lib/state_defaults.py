"""세션 상태 스키마.

접두사로 페이지를 구분한다:
    ft_  - 세 페이지가 공유하는 FeatureConfig 파라미터
    si_  - Page 1 Segment Inspector
    fc_  - Page 2 Fresnel Compare
    sep_ - Page 3 Separability

값은 전부 스칼라/튜플이라 그대로 @st.cache_data 키로 쓸 수 있다.
"""

from typing import Any, Dict

import streamlit as st

# 학습 설정과 일치하는 기본값. 바꾸면 학습된 모델의 피처 분포와 어긋난다.
FT_DEFAULTS: Dict[str, Any] = {
    "ft_rule": "midpoint",  # midpoint | contains
    "ft_stride_sec": 0.25,
    "ft_max_windows": 3,  # 파일당 처리할 낙상 윈도우 수 (약 0.73s/윈도우)
    "ft_target_subcarriers": 30,
    "ft_omega": 30,
    "ft_th_scmax": 1.0,
    "ft_kappa": 1.0,
    "ft_acf_lag_seconds": 0.4,
}

SI_DEFAULTS: Dict[str, Any] = {
    "si_file": None,
    "si_window": 0,
    "si_stage": "s3",
}

FC_DEFAULTS: Dict[str, Any] = {
    "fc_group_mode": "a",  # "a" -> F1/F2/F3, "ab" -> F1-1..F3-3
    "fc_persons": (1, 2, 3),
    "fc_has_run": False,
}

SEP_DEFAULTS: Dict[str, Any] = {
    "sep_pca_dim": 20,
    "sep_seed": 42,
    "sep_has_run": False,
}

SW_DEFAULTS: Dict[str, Any] = {
    "sw_kind": "일상행동 (Q7~Q12)",
    "sw_person": "전체",
    "sw_take": "전체",
    "sw_file": None,
    "sw_window": 0,
    "sw_max_windows": 40,  # 전 구간 윈도우 중 균등 샘플 (약 0.75초/윈도우)
    "sw_has_run": False,
}


def init_session_state() -> None:
    """누락된 키만 기본값으로 채운다."""
    for key, value in {
        **FT_DEFAULTS,
        **SI_DEFAULTS,
        **FC_DEFAULTS,
        **SEP_DEFAULTS,
        **SW_DEFAULTS,
    }.items():
        if key not in st.session_state:
            st.session_state[key] = value


def feature_kwargs() -> Dict[str, Any]:
    """세션의 ft_* 값을 캐시 함수 인자로 펼친다 (스칼라만)."""
    return {
        "rule": st.session_state["ft_rule"],
        "stride_sec": float(st.session_state["ft_stride_sec"]),
        "max_windows": int(st.session_state["ft_max_windows"]),
        "target_subcarriers": int(st.session_state["ft_target_subcarriers"]),
        "omega": int(st.session_state["ft_omega"]),
        "th_scmax": float(st.session_state["ft_th_scmax"]),
        "kappa": float(st.session_state["ft_kappa"]),
        "acf_lag_seconds": float(st.session_state["ft_acf_lag_seconds"]),
    }
