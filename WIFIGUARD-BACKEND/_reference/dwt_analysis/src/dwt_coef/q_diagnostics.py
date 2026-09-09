"""
Full-visibility restatement of ``preprocessing.select_streams()`` / ``select_pcs()``.

amfall 의 스트림/PC 선택은 프로덕션 경로에서 "무엇이 선택됐는가"만 반환하고
"무엇이 탈락했는가"는 버린다. 구체적으로:

- ``select_streams`` 는 후보 60/90개 전체의 q 를 계산해 놓고 상위 ``n_streams`` 개만
  ``q_values_top`` 으로 반환한다. 탈락한 후보의 q 와, 선택되지 않은 상위 후보의
  ``(antenna, subcarrier)`` origin 은 복구 불가능하다.
- ``select_pcs`` 는 고유값 스펙트럼 / ``q_pc_values`` / ``Q_normalized_pc`` /
  PC 임계값 ``1/(n_c-1)`` 을 전부 버리고 카운트·인덱스 4개 키만 반환한다.
  즉 반환값만으로는 PC 단계 q 시각화가 아예 불가능하다.

도메인 시프트 분석은 정확히 그 버려진 값들을 필요로 하므로, 여기서 선택 로직을
"전부 보이는" 형태로 다시 쓴다.

설계 원칙 — **q 커널은 import 하고, 선택 부기(bookkeeping)만 재작성한다.**
표류할 수 있는 유일한 부분은 ``_compute_q`` 의 moving-variance 정의
(``(2W+1)/2W`` 재스케일)이므로 그것은 ``preprocessing`` 에서 그대로 가져온다.
나머지(argsort / 정규화 / 임계 / 폴백 / 캡)는 순수 부기다.
``scripts/streamlit_app/lib/cache.py`` 가 이미 ``_moving_variance`` 를 private
import 하는 선례가 있다.

부동소수점까지 프로덕션과 일치시키기 위해 다음을 그대로 재현한다:
``np.float32`` 누산, ``+ 1e-10`` 엡실론, ``np.argsort(-q)`` 타이브레이크,
``np.linalg.eigh`` 의 오름차순 반환 순서.

``verify_against_reference()`` 가 로컬 구현과 프로덕션을 나란히 돌려 등가성을
확인한다 — 중복이 감춰지지 않고 감사(audit)되게 하기 위한 안전망이다.

NOTE: 이 파일은 신규 파일이며 ``dvc.yaml`` 의 어떤 스테이지 dep 에도 등록되어
있지 않다. ``preprocessing.py`` 를 수정하면 ``preprocess`` → ``featurize``
스테이지가 stale 이 되므로(4500 파일 재실행), 그 파일은 건드리지 않는다.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .preprocessing import _compute_q, select_pcs, select_streams


def stream_q_landscape(
    amplitude_3d: np.ndarray,
    omega: int = 160,
    n_streams: int = 30,
    use_ant: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """
    ``select_streams`` 와 동일한 선택을 수행하되 중간 산출물을 전부 반환한다.

    Args:
        amplitude_3d: 진폭 배열, shape (N, n_antenna, n_subcarrier)
        omega: moving variance 반창폭
        n_streams: Q-임계 적용 전 고려할 상위 스트림 수
        use_ant: 사용할 안테나 인덱스(0-based); None = 전체

    Returns:
        dict:
            antennas_used:        List[int]              사용된 안테나(정렬·중복제거)
            candidate_count:      int                    후보 스트림 수 (n_ant * n_sub)
            origins_all:          List[(ant, sub)]       후보 풀 전체의 origin, 풀 순서
            q_all:                (n_cand,) float32      후보 풀 전체의 q  <- 프로덕션은 버림
            order:                (n_cand,) int          q 내림차순 정렬 인덱스
            top_n:                int                    min(n_streams, n_cand)
            top_pool_indices:     (top_n,) int           상위 top_n 의 풀 인덱스
            top_origins:          List[(ant, sub)]       상위 top_n 의 origin  <- 프로덕션은 버림
            q_top:                (top_n,) float32       q_values_top 과 동일
            Q_top:                (top_n,) float32       Q_values_normalized 와 동일
            q_threshold:          float                  1/top_n
            selected_count:       int
            selected_is_prefix:   bool                   아래 주석 참조
            selected_pool_indices:(n_sel,) int           풀 인덱스 (selected_stream_indices 와 동일)
            selected_origins:     List[(ant, sub)]
            H_S:                  (N, n_sel) float32
            fallback_used:        bool                   Q-임계를 통과한 후보가 0이라 폴백했는가
    """
    if amplitude_3d.ndim != 3:
        raise ValueError(f"amplitude_3d must be 3-D (N, n_ant, n_sub), got {amplitude_3d.shape}")

    _, n_antenna, n_subcarrier = amplitude_3d.shape

    if use_ant is None:
        ant = list(range(n_antenna))
    else:
        ant = sorted(set(int(a) for a in use_ant))
    if not ant:
        raise ValueError("use_ant resolved to an empty antenna list")
    if min(ant) < 0 or max(ant) >= n_antenna:
        raise ValueError(f"use_ant {ant} out of range for {n_antenna} antennas")

    origins: List[Tuple[int, int]] = [(a, s) for a in ant for s in range(n_subcarrier)]
    H_flat = np.stack([amplitude_3d[:, a, s] for a, s in origins], axis=1).astype(np.float32)
    n_cand = H_flat.shape[1]

    # float32 누산까지 프로덕션과 동일하게 유지한다.
    q_all = np.zeros(n_cand, dtype=np.float32)
    for i in range(n_cand):
        q_all[i] = _compute_q(H_flat[:, i], omega)

    order = np.argsort(-q_all)
    n_top = min(n_streams, n_cand)
    top_idx = order[:n_top]
    q_top = q_all[top_idx]

    Q_top = q_top / (np.sum(q_top) + 1e-10)
    q_threshold = 1.0 / max(n_top, 1)

    selected_mask = Q_top >= q_threshold
    fallback_used = bool(np.sum(selected_mask) == 0)
    if fallback_used:
        selected_mask = np.zeros(n_top, dtype=bool)
        selected_mask[int(np.argmax(q_top))] = True

    sel_local = np.where(selected_mask)[0]
    sel_pool = top_idx[sel_local]

    return {
        "antennas_used": ant,
        "candidate_count": int(n_cand),
        "origins_all": origins,
        "q_all": q_all,
        "order": order,
        "top_n": int(n_top),
        "top_pool_indices": top_idx,
        "top_origins": [origins[i] for i in top_idx],
        "q_top": q_top,
        "Q_top": Q_top,
        "q_threshold": float(q_threshold),
        "selected_count": int(sel_local.size),
        # Q-임계는 내림차순 정렬된 배열에 상수 임계로 적용되므로 선택 집합은 항상
        # q_top/Q_top 의 PREFIX 다 (폴백도 이미 내림차순인 배열의 argmax = 0 이라 여전히 prefix).
        # 따라서 막대 i 의 선택 여부는 (i < selected_count) 로 판정해야 하며,
        # selected_pool_indices 는 '풀' 인덱스 공간이라 q_top 을 인덱싱하는 데 쓰면 안 된다.
        "selected_is_prefix": bool(np.array_equal(sel_local, np.arange(sel_local.size))),
        "selected_pool_indices": sel_pool,
        "selected_origins": [origins[i] for i in sel_pool],
        "H_S": H_flat[:, sel_pool].astype(np.float32),
        "fallback_used": fallback_used,
    }


def pc_q_landscape(
    H_S: np.ndarray,
    omega: int = 160,
    eigenvalue_threshold: Optional[float] = None,
    max_pcs: int = 3,
) -> Dict[str, Any]:
    """
    ``select_pcs`` 와 동일한 선택을 수행하되 고유값 스펙트럼과 PC별 q 를 전부 반환한다.

    Returns:
        dict:
            n_input_streams:          int
            eigenvalues:              (n_sel,) float      eigh 오름차순 그대로
            eigenvalue_threshold_used:float               None 이었으면 max(D)*1e-3 로 해소된 값
            explained_variance_ratio: (n_sel,) float
            candidate_indices:        (n_c,) int          임계 통과 고유값 인덱스
            candidate_count:          int
            q_pc:                     (n_c,) float32      후보 PC별 q   <- 프로덕션은 버림
            Q_pc:                     (n_c,) float32      정규화 Q      <- 프로덕션은 버림
            pc_q_threshold:           float               1/(n_c-1)     <- 프로덕션은 버림
            selected_pc_indices:      (n_pc,) int         고유 인덱스 (select_pcs 와 동일)
            selected_pc_count:        int
            capped_by_max_pcs:        bool
            fallback_used:            bool
            pcs:                      (N, n_pc) float32
            top_pc_loading_abs:       (n_sel,) float      q 최대 PC 의 |고유벡터| 로딩
    """
    if H_S.ndim != 2:
        raise ValueError(f"H_S must be 2-D (N, n_selected), got {H_S.shape}")

    _, n_selected = H_S.shape
    if n_selected < 1:
        empty_f = np.zeros(0, dtype=np.float32)
        return {
            "n_input_streams": 0,
            "eigenvalues": np.zeros(0, dtype=np.float64),
            "eigenvalue_threshold_used": float("nan"),
            "explained_variance_ratio": np.zeros(0, dtype=np.float64),
            "candidate_indices": np.zeros(0, dtype=int),
            "candidate_count": 0,
            "q_pc": empty_f,
            "Q_pc": empty_f,
            "pc_q_threshold": float("nan"),
            "selected_pc_indices": np.zeros(0, dtype=int),
            "selected_pc_count": 0,
            "capped_by_max_pcs": False,
            "fallback_used": False,
            "pcs": H_S.astype(np.float32),
            "top_pc_loading_abs": np.zeros(0, dtype=np.float64),
        }

    cov_matrix = np.cov(H_S.T)
    if cov_matrix.ndim == 0:
        cov_matrix = cov_matrix.reshape(1, 1)

    eigenvalues, eigenvectors = np.linalg.eigh(cov_matrix)  # ASCENDING order

    thr = float(np.max(eigenvalues)) * 1e-3 if eigenvalue_threshold is None else float(eigenvalue_threshold)

    candidate_indices = np.where(eigenvalues >= thr)[0]
    if len(candidate_indices) == 0:
        candidate_indices = np.array([int(np.argmax(eigenvalues))])

    P_candidates = H_S @ eigenvectors[:, candidate_indices]

    q_pc = np.zeros(len(candidate_indices), dtype=np.float32)
    for idx in range(len(candidate_indices)):
        q_pc[idx] = _compute_q(P_candidates[:, idx], omega)

    n_c = len(candidate_indices)
    pc_q_threshold = 1.0 / max(n_c - 1, 1)
    Q_pc = q_pc / (np.sum(q_pc) + 1e-10)

    selected_mask_pc = Q_pc >= pc_q_threshold
    fallback_used = bool(np.sum(selected_mask_pc) == 0)
    if fallback_used:
        selected_mask_pc = np.zeros(n_c, dtype=bool)
        selected_mask_pc[int(np.argmax(q_pc))] = True

    sel_local = np.where(selected_mask_pc)[0]
    sel_global = candidate_indices[sel_local]

    capped = bool(len(sel_global) > max_pcs)
    if capped:
        top = np.argsort(-q_pc[sel_local])[:max_pcs]
        sel_global = sel_global[top]

    evr = eigenvalues / (np.sum(eigenvalues) + 1e-10)
    best_pc = int(candidate_indices[int(np.argmax(q_pc))])

    return {
        "n_input_streams": int(n_selected),
        "eigenvalues": eigenvalues,
        "eigenvalue_threshold_used": thr,
        "explained_variance_ratio": evr,
        "candidate_indices": candidate_indices,
        "candidate_count": int(n_c),
        "q_pc": q_pc,
        "Q_pc": Q_pc,
        "pc_q_threshold": float(pc_q_threshold),
        "selected_pc_indices": sel_global,
        "selected_pc_count": int(len(sel_global)),
        "capped_by_max_pcs": capped,
        "fallback_used": fallback_used,
        "pcs": (H_S @ eigenvectors[:, sel_global]).astype(np.float32),
        "top_pc_loading_abs": np.abs(eigenvectors[:, best_pc]),
    }


def verify_against_reference(
    amplitude_3d: np.ndarray,
    omega: int = 160,
    n_streams: int = 30,
    use_ant: Optional[Sequence[int]] = None,
    eigenvalue_threshold: Optional[float] = None,
    max_pcs: int = 3,
) -> Dict[str, Any]:
    """
    로컬 재작성본과 프로덕션 ``select_streams``/``select_pcs`` 를 나란히 실행해
    등가성을 확인한다. Streamlit P4 §4.6 에 렌더되어 중복이 감사되게 한다.

    Returns:
        dict: streams_match, q_top_match, max_abs_q_diff, selected_count_match,
              pcs_match, pc_count_match, selected_is_prefix, all_match
    """
    local_s = stream_q_landscape(amplitude_3d, omega, n_streams, use_ant)
    ref_HS, ref_s = select_streams(
        amplitude_3d, omega, n_streams, list(use_ant) if use_ant is not None else None
    )

    local_p = pc_q_landscape(local_s["H_S"], omega, eigenvalue_threshold, max_pcs)
    _, ref_p = select_pcs(ref_HS, omega, eigenvalue_threshold, max_pcs)

    ref_q_top = np.asarray(ref_s["q_values_top"], dtype=np.float32)
    max_abs_q_diff = (
        float(np.max(np.abs(local_s["q_top"] - ref_q_top))) if ref_q_top.size else 0.0
    )

    streams_match = local_s["selected_origins"] == [tuple(o) for o in ref_s["selected_stream_origins"]]
    q_top_match = bool(np.allclose(local_s["q_top"], ref_q_top, atol=1e-5))
    selected_count_match = local_s["selected_count"] == ref_s["selected_stream_count"]
    pcs_match = local_p["selected_pc_indices"].tolist() == list(ref_p["selected_pc_indices"])
    pc_count_match = local_p["selected_pc_count"] == ref_p["selected_pc_count"]

    return {
        "streams_match": bool(streams_match),
        "q_top_match": q_top_match,
        "max_abs_q_diff": max_abs_q_diff,
        "selected_count_match": bool(selected_count_match),
        "pcs_match": bool(pcs_match),
        "pc_count_match": bool(pc_count_match),
        "selected_is_prefix": bool(local_s["selected_is_prefix"]),
        "all_match": bool(
            streams_match
            and q_top_match
            and selected_count_match
            and pcs_match
            and pc_count_match
            and local_s["selected_is_prefix"]
        ),
    }
