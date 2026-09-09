"""
세션 상태 스키마. 페이지별 프리픽스로 네임스페이스를 나누고, 값은 스칼라/튜플만 둔다
(캐시 키로 그대로 넘길 수 있어야 하므로).

위젯은 ``key=`` 를 달지 않는다. 현재 상태 값을 ``index=``/``value=``/``default=`` 로
읽어 쓰고, ``with st.sidebar:`` 블록 직후에 ``st.session_state.update({...})`` 한 번으로
되쓴다 — 기존 두 앱의 관례.
"""

from typing import Any, Dict, Optional, Tuple

import streamlit as st

#: 공유 표본 설계 (P1 에서 설정, P2~P5 가 그대로 사용)
DS_DEFAULTS: Dict[str, Any] = {
    "ds_envs": (1, 2, 3),
    "ds_activities": None,          # None = 전체 12개 활동
    "ds_n_per_cell": 20,
    "ds_seed": 0,
    "ds_strata": ("env", "fall_label"),
    "ds_balance_subjects": True,
}

#: amfall 파라미터 — config/preprocess.yaml 의 실제 값과 동일하게 초기화한다.
PP_DEFAULTS: Dict[str, Any] = {
    "pp_fs_hz": 320.0,
    "pp_tolerance_ms": 2.0,
    "pp_max_interp_gap_steps": 64,
    "pp_window_sec": 2.5,
    "pp_low_hz": 0.5,
    "pp_high_hz": 80.0,
    "pp_filter_order": 4,
    "pp_omega": 160,
    "pp_n_streams": 30,
    "pp_use_ant": (1, 2),           # 0-based -> 안테나 2, 3
    "pp_eigenvalue_threshold": None,
    "pp_max_pcs": 3,
    "pp_segment_mode": "first_window",
    # --- FallDeFi 피처 파라미터 (논문 기본값을 320 Hz 에 맞춘 값) ---
    "pp_fd_pc_mode": "variance95",
    "pp_fd_freq_res_hz": 2.0,
    "pp_fd_overlap": 0.90,
    "pp_fd_noise_k": 3.0,
    "pp_fd_torso_pct": 50.0,
}

#: config/preprocess.yaml 로 되돌리기 버튼이 복원하는 값 (PP_DEFAULTS 와 동일)
PP_CONFIG_YAML_VALUES: Dict[str, Any] = dict(PP_DEFAULTS)

AQ_DEFAULTS: Dict[str, Any] = {
    "aq_full_dataset": False,
    "aq_has_run": False,
    "aq_metric": "agc_mean",
}

CP_DEFAULTS: Dict[str, Any] = {
    "cp_has_run": False,
    "cp_antenna": "전체",
    "cp_value": "amp_mean",
}

QV_DEFAULTS: Dict[str, Any] = {
    "qv_has_run": False,
    "qv_focus_file": None,
    "qv_split_by_fall": False,
}

FD_DEFAULTS: Dict[str, Any] = {
    "fdp_has_run": False,
    "fdp_focus_file": None,
    "fdp_feature_set": "OF (전체)",
}

#: P7 교차 도메인 분류
XD_DEFAULTS: Dict[str, Any] = {
    "xd_has_run": False,
    "xd_n_fall_per_cell": 30,      # (env, activity) 셀당 낙상 파일 수 -> 3x2x30 = 180
    "xd_n_nonfall_per_cell": 6,    # 3x10x6 = 180 -> 정확히 50:50
    "xd_rxs": (1, 2, 3),
    "xd_n_test_subjects": 5,
    "xd_n_train_subjects": 5,
    "xd_n_replicates": 20,
    "xd_pool": "14x14",
    "xd_pca_dim": 20,
    "xd_n_boot": 1000,
    "xd_seed": 0,
    "xd_exclude_lowrate": False,
    "xd_focus_file": None,
}

SM_DEFAULTS: Dict[str, Any] = {
    "sm_has_run": False,
    "sm_feature_set": "전체",
    "sm_n_permutations": 20,
    "sm_seed": 0,
    "sm_matrix_value": "ks_d",
    "sm_curve_metric": "agc_mean",
}


def init_session_state() -> None:
    """아직 없는 키만 기본값으로 채운다."""
    for key, value in {
        **DS_DEFAULTS, **PP_DEFAULTS, **AQ_DEFAULTS,
        **CP_DEFAULTS, **QV_DEFAULTS, **FD_DEFAULTS, **XD_DEFAULTS, **SM_DEFAULTS,
    }.items():
        if key not in st.session_state:
            st.session_state[key] = value


def sampling_kwargs() -> Dict[str, Any]:
    """ds_* -> stratified_sample 인자 (스칼라/튜플만)."""
    acts = st.session_state["ds_activities"]
    return {
        "envs": tuple(st.session_state["ds_envs"]),
        "activities": tuple(acts) if acts else None,
        "n_per_cell": int(st.session_state["ds_n_per_cell"]),
        "seed": int(st.session_state["ds_seed"]),
        "strata": tuple(st.session_state["ds_strata"]),
        "balance_subjects": bool(st.session_state["ds_balance_subjects"]),
    }


def probe_kwargs() -> Dict[str, Any]:
    """pp_* -> probe_file 인자 (스칼라/튜플만, 캐시 키로 그대로 사용 가능)."""
    ua = st.session_state["pp_use_ant"]
    thr = st.session_state["pp_eigenvalue_threshold"]
    return {
        "fs_hz": float(st.session_state["pp_fs_hz"]),
        "tolerance_ms": float(st.session_state["pp_tolerance_ms"]),
        "max_interp_gap_steps": int(st.session_state["pp_max_interp_gap_steps"]),
        "window_sec": float(st.session_state["pp_window_sec"]),
        "low_hz": float(st.session_state["pp_low_hz"]),
        "high_hz": float(st.session_state["pp_high_hz"]),
        "filter_order": int(st.session_state["pp_filter_order"]),
        "omega": int(st.session_state["pp_omega"]),
        "n_streams": int(st.session_state["pp_n_streams"]),
        "use_ant": tuple(ua) if ua else None,
        "eigenvalue_threshold": float(thr) if thr is not None else None,
        "max_pcs": int(st.session_state["pp_max_pcs"]),
        "segment_mode": str(st.session_state["pp_segment_mode"]),
        "fd_pc_mode": str(st.session_state["pp_fd_pc_mode"]),
        "fd_freq_res_hz": float(st.session_state["pp_fd_freq_res_hz"]),
        "fd_overlap": float(st.session_state["pp_fd_overlap"]),
        "fd_noise_k": float(st.session_state["pp_fd_noise_k"]),
        "fd_torso_pct": float(st.session_state["pp_fd_torso_pct"]),
    }


def params_fingerprint() -> Dict[str, Any]:
    """디스크 캐시 해시의 입력. 파이프라인 파라미터만 포함하고 표본 설계는 뺀다 —
    표본이 커져도 이미 계산한 파일의 결과는 그대로 유효하기 때문이다."""
    return dict(probe_kwargs())


def meta_fingerprint() -> Dict[str, Any]:
    """메타데이터 pass 는 fs_hz 에만 의존한다."""
    return {"fs_hz": float(st.session_state["pp_fs_hz"]), "pass": "metadata"}


def reset_amfall_params() -> None:
    """config/preprocess.yaml 값으로 되돌린다."""
    for key, value in PP_CONFIG_YAML_VALUES.items():
        st.session_state[key] = value


def clear_run_flags() -> None:
    """표본이나 파라미터가 바뀌면 각 페이지의 실행 게이트를 닫는다."""
    for key in ("aq_has_run", "cp_has_run", "qv_has_run", "fdp_has_run",
                "xd_has_run", "sm_has_run"):
        st.session_state[key] = False


def echo_shared_settings() -> Dict[str, Any]:
    """사이드바에서 '지금 무엇이 적용 중인지' 보여주기 위한 요약."""
    return {
        "envs": list(st.session_state["ds_envs"]),
        "n_per_cell": st.session_state["ds_n_per_cell"],
        "strata": list(st.session_state["ds_strata"]),
        "seed": st.session_state["ds_seed"],
        "omega": st.session_state["pp_omega"],
        "use_ant": list(st.session_state["pp_use_ant"]) if st.session_state["pp_use_ant"] else "전체",
        "segment_mode": st.session_state["pp_segment_mode"],
    }
