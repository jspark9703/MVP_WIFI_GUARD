"""
파이프라인 단계별 q 측정 (비싼 pass, ~0.2~0.5 s/파일 — 대부분 complex CSI 파싱 비용).
streamlit 을 import 하지 않는다.

``preprocessing.preprocess_segment`` 의 단계 순서를 **그대로** 재현하되
(extract_amplitude → resample → 고정길이 절단/패딩 → bandpass → select_streams →
select_pcs(동일 omega) → PC 합 → z-score), 각 단계에서 q 를 측정해 둔다.
프로덕션은 최종 신호와 요약 통계만 반환하므로 단계별 변화를 볼 수 없다.

q landscape 1회(90 스트림)는 실측 ~16 ms 이므로 3개 단계 전부 계산해도
파싱 비용에 묻힌다. 따라서 아끼지 않고 전부 계산한다.
"""

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.dwt_coef.data_loader import load_csi_file
from src.dwt_coef.preprocessing import (
    _compute_q,
    _moving_variance,
    bandpass_filter,
    extract_amplitude,
    resample_signal,
)
from src.dwt_coef.falldefi_features import (
    OF_KEYS as FD_OF_KEYS,
    extract_falldefi_features,
    variance95_pcs,
)
from src.dwt_coef.q_diagnostics import (
    pc_q_landscape,
    stream_q_landscape,
    verify_against_reference,
)
from src.dwt_coef.session_builder import ACTIVITY_NAME_MAP

from lib.constants import FALL_LABELS, NATIVE_FS_HZ

SEGMENT_MODES: Tuple[str, ...] = ("first_window", "whole_file")


# --- landscape 요약 스칼라 ---------------------------------------------------

def _q_map(amp3d: np.ndarray, omega: int) -> np.ndarray:
    """(n_ant, n_sub) q 히트맵. 샘플이 MV 창보다 짧으면 NaN."""
    n, n_ant, n_sub = amp3d.shape
    if n < 2 * omega + 3:
        return np.full((n_ant, n_sub), np.nan, dtype=np.float32)
    out = np.zeros((n_ant, n_sub), dtype=np.float32)
    for a in range(n_ant):
        for s in range(n_sub):
            out[a, s] = _compute_q(amp3d[:, a, s], omega)
    return out


def _gini(x: np.ndarray) -> float:
    """정규화된 분포의 지니계수. landscape 가 한 스트림에 몰릴수록 1 에 가깝다."""
    v = np.sort(np.asarray(x, dtype=np.float64))
    n = v.size
    if n == 0 or v.sum() <= 0:
        return float("nan")
    idx = np.arange(1, n + 1)
    return float((2.0 * np.sum(idx * v)) / (n * np.sum(v)) - (n + 1.0) / n)


def _norm_entropy(p: np.ndarray) -> float:
    """정규화 섀넌 엔트로피 (0=한 곳에 집중, 1=완전 평탄). 스케일 free 라 env 간 비교 가능."""
    v = np.asarray(p, dtype=np.float64)
    v = v[v > 0]
    if v.size <= 1:
        return float("nan")
    return float(-np.sum(v * np.log(v)) / np.log(v.size))


def _effective_rank(evr: np.ndarray) -> float:
    """고유값 분포의 유효 랭크 = exp(엔트로피). 몇 개의 PC 가 실질적으로 살아있는가."""
    v = np.asarray(evr, dtype=np.float64)
    v = v[v > 0]
    if v.size == 0:
        return float("nan")
    return float(np.exp(-np.sum(v * np.log(v))))


def _safe(x: float) -> float:
    return float(x) if np.isfinite(x) else float("nan")


# --- 메인 프로브 -------------------------------------------------------------

def probe_file(
    filepath: str,
    fs_hz: float = NATIVE_FS_HZ,
    tolerance_ms: float = 2.0,
    max_interp_gap_steps: int = 64,
    window_sec: float = 2.5,
    low_hz: float = 0.5,
    high_hz: float = 80.0,
    filter_order: int = 4,
    omega: int = 160,
    n_streams: int = 30,
    use_ant: Optional[Tuple[int, ...]] = (1, 2),
    eigenvalue_threshold: Optional[float] = None,
    max_pcs: int = 3,
    segment_mode: str = "first_window",
    fd_pc_mode: str = "variance95",
    fd_freq_res_hz: float = 2.0,
    fd_overlap: float = 0.90,
    fd_noise_k: float = 3.0,
    fd_torso_pct: float = 50.0,
    keep_signals: bool = False,
) -> Dict[str, Any]:
    """
    파일 1개를 amfall 파이프라인에 통과시키며 단계별 q 를 측정한다.

    Args:
        segment_mode: "first_window" 는 preprocess_segment 와 동일하게
            int(fs*window_sec) 샘플로 절단/패딩한다(프로덕션 수치와 직접 비교 가능).
            "whole_file" 은 절단 없이 4초 전체를 쓴다(통계적으로 더 안정).
        keep_signals: True 면 신호/맵 배열을 함께 반환한다(단일 파일 추적 페이지용,
            디스크 캐시에는 절대 쓰지 않는다).

    Returns:
        {"record": 평탄한 스칼라 dict, "q_long": DF, "sub_long": DF, "pc_long": DF,
         "signals": dict | None}
    """
    if segment_mode not in SEGMENT_MODES:
        raise ValueError(f"segment_mode must be one of {SEGMENT_MODES}, got {segment_mode!r}")

    d = load_csi_file(filepath)

    # --- S0: raw 진폭 (리샘플 전, 불규칙 타임스탬프) ---
    amp, ts = extract_amplitude(d["csi"], d["timestamps"])
    n_raw = amp.shape[0]
    ts_f = np.asarray(ts, dtype=np.float64)
    raw_gaps = np.diff(ts_f) if n_raw > 1 else np.zeros(0)
    raw_pos = raw_gaps[raw_gaps > 0]
    q_raw_map = _q_map(amp, omega)

    # --- S1: 리샘플 ---
    amp_r, rs = resample_signal(ts_f, amp, fs_hz, tolerance_ms, max_interp_gap_steps)

    # FallDeFi 피처는 event duration 이 피처 자체라 first_window 절단을 적용하면
    # 지속시간이 인위적으로 상한에 걸린다. 따라서 절단 전 전체 길이 사본을 남긴다.
    amp_r_full = amp_r

    fixed = int(fs_hz * window_sec)
    truncated = padded = False
    pad_samples = 0
    n_after_resample = int(amp_r.shape[0])
    if segment_mode == "first_window":
        if amp_r.shape[0] > fixed:
            amp_r = amp_r[:fixed]
            truncated = True
        elif amp_r.shape[0] < fixed:
            pad_samples = fixed - amp_r.shape[0]
            amp_r = np.pad(amp_r, ((0, pad_samples), (0, 0), (0, 0)), mode="edge")
            padded = True

    q_res_map = _q_map(amp_r, omega)

    # --- S2: 대역통과 ---
    amp_f = bandpass_filter(amp_r, fs_hz, low_hz, high_hz, filter_order)
    q_filt_map = _q_map(amp_f, omega)

    # --- S3: 스트림 선택 (전체 가시성) ---
    s = stream_q_landscape(amp_f, omega, n_streams, use_ant)

    # --- S4: PC 선택 (전체 가시성) ---
    p = pc_q_landscape(s["H_S"], omega, eigenvalue_threshold, max_pcs)

    # --- FallDeFi (Palipana et al. 2017) Table 1 피처 ---
    # 대역통과 **이전** 신호를 쓴다: 0.5-80 Hz 를 걸면 80 Hz 위가 비어
    # entropy_30_max 와 잡음 추정 대역이 모두 무의미해진다.
    fd_flat = amp_r_full.reshape(amp_r_full.shape[0], -1)
    if fd_pc_mode == "amfall_q":
        fd_pcs = p["pcs"]                       # amfall 이 q 로 고른 PC (대역통과 후)
        fd_pc_info = {"n_pcs": int(fd_pcs.shape[1]) if fd_pcs.ndim == 2 else 0,
                      "var_explained": float("nan")}
    else:                                        # "variance95" — 논문의 PC 선택 규칙
        fd_pcs, fd_pc_info = variance95_pcs(fd_flat)

    fd = extract_falldefi_features(
        fd_pcs, fs_hz, freq_res_hz=fd_freq_res_hz, overlap_ratio=fd_overlap,
        noise_k=fd_noise_k, torso_percentile=fd_torso_pct,
        return_arrays=keep_signals,
    )
    fd_arrays = fd.pop("arrays", None)

    # --- S5: 대표 신호 ---
    rep = p["pcs"].sum(axis=1).astype(np.float32)
    rep_mean, rep_std = float(np.mean(rep)), float(np.std(rep))
    rep = (rep - rep_mean) / rep_std if rep_std > 1e-10 else rep - rep_mean
    final_q = _compute_q(rep, omega) if rep.size >= 2 * omega + 3 else float("nan")

    # --- 선택된 스트림의 안테나/서브캐리어 분포 ---
    sel_origins = s["selected_origins"]
    n_sel = max(len(sel_origins), 1)
    sel_ants = np.array([a for a, _ in sel_origins]) if sel_origins else np.zeros(0)
    sel_subs = np.array([sub for _, sub in sel_origins]) if sel_origins else np.zeros(0)

    q_top = s["q_top"]
    q_filt_flat = q_filt_map[np.isfinite(q_filt_map)]
    q_raw_flat = q_raw_map[np.isfinite(q_raw_map)]
    q_res_flat = q_res_map[np.isfinite(q_res_map)]

    q_filt_max = _safe(np.max(q_filt_flat)) if q_filt_flat.size else float("nan")
    q_raw_max = _safe(np.max(q_res_flat)) if q_res_flat.size else float("nan")
    q_pc_max = _safe(np.max(p["q_pc"])) if p["q_pc"].size else float("nan")

    amp_ant_means = amp.mean(axis=(0, 2))  # (n_ant,)
    amp_flat = amp.reshape(-1)

    record: Dict[str, Any] = {
        # --- 신원 ---
        "filepath": str(filepath),
        "name": str(d["filepath"]).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0],
        "env": int(d["env"]), "subject": int(d["subject"]), "class_id": int(d["class_id"]),
        "activity": int(d["activity"]), "trial": int(d["trial"]),
        "is_fall": bool(d["is_fall"]),
        "fall_label": FALL_LABELS[bool(d["is_fall"])],
        "activity_name": ACTIVITY_NAME_MAP.get(int(d["activity"]), f"A{d['activity']}"),
        # --- raw ---
        "raw_packets": int(n_raw),
        "raw_duration_sec": float(np.sum(raw_pos) / 1e6) if raw_pos.size else 0.0,
        "raw_median_gap_ms": float(np.median(raw_pos) / 1000.0) if raw_pos.size else float("nan"),
        "raw_est_fs_hz": float(1e6 / np.median(raw_pos)) if raw_pos.size else float("nan"),
        # --- 리샘플 (rs 키: raw_samples/resampled_samples/interp_steps/
        #              fallback_steps/irregular_gaps/nonpositive_gaps) ---
        "resampled_samples": n_after_resample,
        "interp_steps": int(rs["interp_steps"]),
        "fallback_steps": int(rs["fallback_steps"]),
        "irregular_gaps": int(rs["irregular_gaps"]),
        "nonpositive_gaps": int(rs["nonpositive_gaps"]),
        "interp_ratio": float(rs["interp_steps"] / max(n_after_resample, 1)),
        "fallback_ratio": float(rs["fallback_steps"] / max(n_raw - 1, 1)),
        "nonpositive_ratio": float(rs["nonpositive_gaps"] / max(n_raw - 1, 1)),
        "length_ratio": float(n_after_resample / max(n_raw, 1)),
        "truncated": truncated, "padded": padded, "pad_samples": int(pad_samples),
        "n_samples_used": int(amp_f.shape[0]),
        # --- 진폭 ---
        "amp_mean": _safe(np.mean(amp_flat)), "amp_std": _safe(np.std(amp_flat)),
        "amp_cv": _safe(np.std(amp_flat) / np.mean(amp_flat)) if np.mean(amp_flat) > 0 else float("nan"),
        "amp_ant1_mean": _safe(amp_ant_means[0]),
        "amp_ant2_mean": _safe(amp_ant_means[1]),
        "amp_ant3_mean": _safe(amp_ant_means[2]),
        "amp_ant_ratio_21": _safe(amp_ant_means[1] / amp_ant_means[0]) if amp_ant_means[0] > 0 else float("nan"),
        "amp_sub_std_mean": _safe(np.mean(amp.mean(axis=0).std(axis=1))),
        "amp_dynamic_range_db": _safe(
            20.0 * np.log10(np.max(amp_flat) / np.min(amp_flat[amp_flat > 0]))
        ) if np.any(amp_flat > 0) else float("nan"),
        # --- 필터 ---
        "filt_std": _safe(np.std(amp_f)),
        "filt_energy_ratio": _safe(np.sum(amp_f.astype(np.float64) ** 2) /
                                   np.sum(amp_r.astype(np.float64) ** 2))
        if np.sum(amp_r.astype(np.float64) ** 2) > 0 else float("nan"),
        # --- q (핵심) ---
        "q_raw_pre_resample_max": _safe(np.max(q_raw_flat)) if q_raw_flat.size else float("nan"),
        "q_raw_max": q_raw_max,
        "q_raw_mean": _safe(np.mean(q_res_flat)) if q_res_flat.size else float("nan"),
        "q_filt_max": q_filt_max,
        "q_filt_mean": _safe(np.mean(q_filt_flat)) if q_filt_flat.size else float("nan"),
        "q_filt_median": _safe(np.median(q_filt_flat)) if q_filt_flat.size else float("nan"),
        "q_filt_p90": _safe(np.percentile(q_filt_flat, 90)) if q_filt_flat.size else float("nan"),
        "q_filt_std": _safe(np.std(q_filt_flat)) if q_filt_flat.size else float("nan"),
        "q_gain_max": _safe(q_filt_max / q_raw_max) if q_raw_max and q_raw_max > 0 else float("nan"),
        "q_gain_mean": _safe(np.mean(q_filt_flat) / np.mean(q_res_flat))
        if q_res_flat.size and np.mean(q_res_flat) > 0 else float("nan"),
        # --- 스트림 선택 ---
        "candidate_stream_count": int(s["candidate_count"]),
        "q_top1": _safe(q_top[0]) if q_top.size else float("nan"),
        "q_top5_mean": _safe(np.mean(q_top[:5])) if q_top.size else float("nan"),
        "q_top30_mean": _safe(np.mean(q_top)) if q_top.size else float("nan"),
        "q_selected_mean": _safe(np.mean(q_top[: s["selected_count"]])) if s["selected_count"] else float("nan"),
        "Q_top1": _safe(s["Q_top"][0]) if s["Q_top"].size else float("nan"),
        "q_entropy_norm": _norm_entropy(s["Q_top"]),
        "q_gini": _gini(s["Q_top"]),
        "selected_stream_count": int(s["selected_count"]),
        "q_threshold": float(s["q_threshold"]),
        "stream_fallback_used": bool(s["fallback_used"]),
        "sel_ant0_frac": float(np.mean(sel_ants == 0)) if sel_ants.size else 0.0,
        "sel_ant1_frac": float(np.mean(sel_ants == 1)) if sel_ants.size else 0.0,
        "sel_ant2_frac": float(np.mean(sel_ants == 2)) if sel_ants.size else 0.0,
        "sel_sub_mean": _safe(np.mean(sel_subs)) if sel_subs.size else float("nan"),
        "sel_sub_std": _safe(np.std(sel_subs)) if sel_subs.size else float("nan"),
        # --- PCA ---
        "pc_candidate_count": int(p["candidate_count"]),
        "pc_selected_count": int(p["selected_pc_count"]),
        "pc_capped": bool(p["capped_by_max_pcs"]),
        "pc_fallback_used": bool(p["fallback_used"]),
        "eig_top1_ratio": _safe(np.max(p["explained_variance_ratio"]))
        if p["explained_variance_ratio"].size else float("nan"),
        "eig_top3_ratio": _safe(np.sum(np.sort(p["explained_variance_ratio"])[-3:]))
        if p["explained_variance_ratio"].size else float("nan"),
        "eig_effective_rank": _effective_rank(p["explained_variance_ratio"]),
        "pc_q_threshold": float(p["pc_q_threshold"]),
        "q_pc_max": q_pc_max,
        "q_pc_mean": _safe(np.mean(p["q_pc"])) if p["q_pc"].size else float("nan"),
        "Q_pc_top1": _safe(np.max(p["Q_pc"])) if p["Q_pc"].size else float("nan"),
        "q_pc_over_q_stream": _safe(q_pc_max / q_filt_max)
        if q_filt_max and q_filt_max > 0 else float("nan"),
        # --- 최종 ---
        "final_q": _safe(final_q),
        "final_kurtosis": _safe(
            np.mean(((rep - np.mean(rep)) / (np.std(rep) + 1e-12)) ** 4) - 3.0
        ) if rep.size else float("nan"),
        "rep_scale_std": rep_std,
        # --- FallDeFi Table 1 (fd_*) ---
        "fd_pc_mode": str(fd_pc_mode),
        "fd_pc_var_explained": float(fd_pc_info.get("var_explained", float("nan"))),
        **{k: v for k, v in fd.items()},
    }

    meta_cols = {k: record[k] for k in
                 ("filepath", "env", "subject", "activity", "is_fall", "fall_label")}

    # --- long 프레임 ---
    origins = s["origins_all"]
    rank_of_pool = np.empty(s["candidate_count"], dtype=np.int64)
    rank_of_pool.fill(-1)
    for rank, pool_i in enumerate(s["top_pool_indices"]):
        rank_of_pool[pool_i] = rank

    q_long = pd.DataFrame({
        "antenna": [a for a, _ in origins],
        "subcarrier": [sub for _, sub in origins],
        "q_filt": s["q_all"],
        "rank_filt": rank_of_pool,
        "in_top_n": rank_of_pool >= 0,
        # 선택 집합은 q 내림차순의 PREFIX 다 -> rank < selected_count 로 판정한다.
        "selected": (rank_of_pool >= 0) & (rank_of_pool < s["selected_count"]),
        "Q_filt": [
            float(s["Q_top"][r]) if r >= 0 else np.nan for r in rank_of_pool
        ],
        "q_raw": [float(q_res_map[a, sub]) for a, sub in origins],
    })
    for k, v in meta_cols.items():
        q_long[k] = v

    n_ant, n_sub = q_filt_map.shape
    sub_long = pd.DataFrame({
        "antenna": np.repeat(np.arange(n_ant), n_sub),
        "subcarrier": np.tile(np.arange(n_sub), n_ant),
        "amp_mean": amp.mean(axis=0).reshape(-1),
        "amp_std": amp.std(axis=0).reshape(-1),
        "filt_std": amp_f.std(axis=0).reshape(-1),
        "q_filt_map": q_filt_map.reshape(-1),
    })
    sub_long["amp_cv"] = sub_long["amp_std"] / sub_long["amp_mean"].replace(0, np.nan)
    for k, v in meta_cols.items():
        sub_long[k] = v

    sel_set = set(int(i) for i in p["selected_pc_indices"])
    pc_long = pd.DataFrame({
        "pc_eigen_index": p["candidate_indices"].astype(int),
        # eigh 는 오름차순이므로 후보 안에서의 '큰 고유값 순' 순위를 따로 준다.
        "pc_rank": np.argsort(np.argsort(-p["eigenvalues"][p["candidate_indices"]])),
        "eigenvalue": p["eigenvalues"][p["candidate_indices"]],
        "evr": p["explained_variance_ratio"][p["candidate_indices"]],
        "q_pc": p["q_pc"],
        "Q_pc": p["Q_pc"],
        "selected": [int(i) in sel_set for i in p["candidate_indices"]],
    })
    for k, v in meta_cols.items():
        pc_long[k] = v

    out: Dict[str, Any] = {
        "record": record, "q_long": q_long, "sub_long": sub_long, "pc_long": pc_long,
        "signals": None,
    }

    if keep_signals:
        # 리샘플러가 만들어낸 보간 샘플을 시각화에서 구분할 수 있도록,
        # 원본 샘플이 놓인 그리드 위치를 근사해 함께 넘긴다.
        grid_us = 1e6 / fs_hz
        if raw_pos.size:
            approx_orig = np.clip(
                np.round((ts_f - ts_f[0]) / grid_us).astype(int), 0, amp_f.shape[0] - 1
            )
        else:
            approx_orig = np.zeros(0, dtype=int)

        best = int(np.nanargmax(q_filt_map)) if np.any(np.isfinite(q_filt_map)) else 0
        b_ant, b_sub = divmod(best, n_sub)

        out["signals"] = {
            "raw_time_sec": (ts_f - ts_f[0]) / 1e6,
            "raw_amp": amp[:, b_ant, b_sub],
            "resampled": amp_r[:, b_ant, b_sub],
            "filtered": amp_f[:, b_ant, b_sub],
            "rep": rep,
            "rep_mv": _moving_variance(rep, omega) if rep.size >= 2 * omega + 3 else np.zeros(0),
            "original_sample_idx": approx_orig,
            "best_antenna": int(b_ant), "best_subcarrier": int(b_sub),
            "fs_hz": float(fs_hz),
            "q_raw_map": q_raw_map, "q_res_map": q_res_map, "q_filt_map": q_filt_map,
            "eigenvalues": p["eigenvalues"], "evr": p["explained_variance_ratio"],
            "eig_threshold": p["eigenvalue_threshold_used"],
            "q_pc": p["q_pc"], "Q_pc": p["Q_pc"], "pc_q_threshold": p["pc_q_threshold"],
            "pc_candidate_indices": p["candidate_indices"],
            "pc_selected_indices": p["selected_pc_indices"],
            "q_top": q_top, "Q_top": s["Q_top"],
            "stream_q_threshold": s["q_threshold"],
            "selected_count": s["selected_count"],
            "top_origins": s["top_origins"],
            "falldefi": fd_arrays,
            "fd_pc_info": fd_pc_info,
        }

    return out


def verify_file(
    filepath: str,
    fs_hz: float = NATIVE_FS_HZ,
    tolerance_ms: float = 2.0,
    max_interp_gap_steps: int = 64,
    window_sec: float = 2.5,
    low_hz: float = 0.5,
    high_hz: float = 80.0,
    filter_order: int = 4,
    omega: int = 160,
    n_streams: int = 30,
    use_ant: Optional[Tuple[int, ...]] = (1, 2),
    eigenvalue_threshold: Optional[float] = None,
    max_pcs: int = 3,
    segment_mode: str = "first_window",
) -> Dict[str, Any]:
    """
    이 앱이 쓰는 q_diagnostics 재작성본이 프로덕션 select_streams/select_pcs 와
    동일한 선택을 하는지 실제 파일로 확인한다. probe_file 과 완전히 같은 전처리를
    거친 뒤 비교한다 — 중복 구현이 감춰지지 않고 감사되게 하는 안전망.
    """
    d = load_csi_file(filepath)
    amp, ts = extract_amplitude(d["csi"], d["timestamps"])
    amp_r, _ = resample_signal(np.asarray(ts, dtype=np.float64), amp,
                               fs_hz, tolerance_ms, max_interp_gap_steps)

    if segment_mode == "first_window":
        fixed = int(fs_hz * window_sec)
        if amp_r.shape[0] > fixed:
            amp_r = amp_r[:fixed]
        elif amp_r.shape[0] < fixed:
            amp_r = np.pad(amp_r, ((0, fixed - amp_r.shape[0]), (0, 0), (0, 0)), mode="edge")

    amp_f = bandpass_filter(amp_r, fs_hz, low_hz, high_hz, filter_order)

    out = verify_against_reference(amp_f, omega, n_streams, use_ant,
                                   eigenvalue_threshold, max_pcs)
    out["name"] = str(d["filepath"]).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    out["env"] = int(d["env"])
    return out


def stage_rows(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """record -> 단계별 long 행 (P4 §4.2 의 스테이지 선그래프용)."""
    stages = [
        ("raw_amp", record.get("q_raw_pre_resample_max")),
        ("resampled", record.get("q_raw_max")),
        ("filtered", record.get("q_filt_max")),
        ("stream_top1", record.get("q_top1")),
        ("stream_selected_mean", record.get("q_selected_mean")),
        ("pc_max", record.get("q_pc_max")),
        ("final", record.get("final_q")),
    ]
    return [
        {
            "filepath": record["filepath"], "env": record["env"],
            "subject": record["subject"], "activity": record["activity"],
            "is_fall": record["is_fall"], "fall_label": record["fall_label"],
            "stage": name, "q": float(val) if val is not None else np.nan,
        }
        for name, val in stages
    ]


def probe_batch(
    filepaths: Sequence[str],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    prober: Optional[Callable[..., Dict[str, Any]]] = None,
    **probe_kwargs: Any,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, List[str]]:
    """
    파일 목록을 순회한다. 캐시된 per-file 함수를 주입할 수 있도록 ``prober`` 를 받는다.
    한 파일이 깨져도 배치를 중단하지 않는다.

    Returns:
        (records, q_long, sub_long, pc_long, errors)
    """
    run = prober or probe_file
    total = len(filepaths)
    records: List[Dict[str, Any]] = []
    q_parts: List[pd.DataFrame] = []
    sub_parts: List[pd.DataFrame] = []
    pc_parts: List[pd.DataFrame] = []
    errors: List[str] = []

    for i, fp in enumerate(filepaths):
        name = str(fp).replace("\\", "/").rsplit("/", 1)[-1]
        if progress_callback:
            progress_callback(i, total, name)
        try:
            res = run(str(fp), **probe_kwargs)
            records.append(res["record"])
            q_parts.append(res["q_long"])
            sub_parts.append(res["sub_long"])
            pc_parts.append(res["pc_long"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    if progress_callback:
        progress_callback(total, total, "완료")

    def _cat(parts: List[pd.DataFrame]) -> pd.DataFrame:
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    return pd.DataFrame(records), _cat(q_parts), _cat(sub_parts), _cat(pc_parts), errors
