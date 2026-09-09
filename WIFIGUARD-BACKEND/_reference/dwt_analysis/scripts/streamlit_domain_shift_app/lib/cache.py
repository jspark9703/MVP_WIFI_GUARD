"""
캐시 레이어. lib 중 streamlit 을 import 하는 두 모듈 중 하나다 (나머지 하나는
render.py). 그 외 lib 모듈은 순수 함수로 유지해 REPL/노트북에서도 그대로 쓸 수 있게 한다.

캐시 키는 스칼라/튜플만 쓴다 (numpy 배열 금지). 파라미터는 dataclass 로 묶지 않고
개별 스칼라로 펼쳐서, 사이드바 위젯 하나를 바꿨을 때 전부가 무효화되지 않게 한다.
값싼 캐시 함수는 서로를 호출해 조합한다 (기존 streamlit_app/lib/cache.py 관례).

2단 캐시:
  1. per-file ``@st.cache_data`` (메모리) — 같은 세션 내 재실행이 즉시
  2. 파일 단위 parquet (디스크) — Streamlit 재시작/표본 확대에도 살아남음
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st

from src.dwt_coef.data_loader import get_file_index

from lib.disk_cache import (
    append_cached_long,
    append_cached_rows,
    cache_path,
    load_cached_rows,
    load_long_rows,
    params_hash,
    write_params_json,
)
from lib.metadata_pass import metadata_batch, read_file_metadata
from lib.sampling import add_group_columns, stratified_sample
from lib.deep_features import DeepFeatureParams, extract_batch as _cwtacf_extract_batch, extract_one
from lib.deep_features import batch_status as _cwtacf_status
from lib.stage_probe import probe_batch, probe_file, verify_file


# --- 인덱스 / 정적 산출물 ----------------------------------------------------

@st.cache_data(show_spinner=False)
def get_file_index_cached(mendeley_root: str) -> pd.DataFrame:
    return add_group_columns(get_file_index(mendeley_root))


@st.cache_data(show_spinner=False)
def load_csv_artifact_cached(path: str) -> pd.DataFrame:
    """이미 계산된 산출물(agc_manifest 등). 없으면 빈 DataFrame — 페이지는 섹션을 숨긴다."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(show_spinner=False)
def sample_cached(
    mendeley_root: str,
    envs: Tuple[int, ...],
    activities: Optional[Tuple[int, ...]],
    n_per_cell: int,
    seed: int,
    strata: Tuple[str, ...],
    balance_subjects: bool,
) -> pd.DataFrame:
    file_index = get_file_index_cached(mendeley_root)
    return stratified_sample(
        file_index, envs=envs, activities=activities, n_per_cell=n_per_cell,
        seed=seed, strata=strata, balance_subjects=balance_subjects,
    )


# --- 값싼 메타데이터 pass ----------------------------------------------------

@st.cache_data(show_spinner=False, max_entries=2048)
def metadata_file_cached(filepath: str, fs_hz: float) -> Dict[str, Any]:
    return read_file_metadata(filepath, fs_hz)


def metadata_batch_cached(
    filepaths: Sequence[str],
    fs_hz: float,
    cache_dir: str,
    fingerprint: Dict[str, Any],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    캐시되지 **않은** 래퍼 — 디스크 조회 -> 미스만 계산 -> parquet 병합.
    표본을 늘려도 이미 계산한 파일은 다시 읽지 않는다.
    """
    hash8 = params_hash(fingerprint)
    path = cache_path(cache_dir, "meta", hash8)
    hits, missing = load_cached_rows(path, filepaths)

    errors: List[str] = []
    fresh = pd.DataFrame()
    if missing:
        fresh, errors = metadata_batch(
            missing, fs_hz, progress_callback=progress_callback,
            reader=metadata_file_cached,
        )
        if not fresh.empty:
            append_cached_rows(path, fresh)
            write_params_json(cache_dir, hash8, fingerprint)
    elif progress_callback:
        progress_callback(len(filepaths), len(filepaths), "캐시 적중")

    parts = [df for df in (hits, fresh) if not df.empty]
    return (pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()), errors


# --- 비싼 CSI pass -----------------------------------------------------------

@st.cache_data(show_spinner=False, max_entries=512)
def probe_file_cached(
    filepath: str,
    fs_hz: float,
    tolerance_ms: float,
    max_interp_gap_steps: int,
    window_sec: float,
    low_hz: float,
    high_hz: float,
    filter_order: int,
    omega: int,
    n_streams: int,
    use_ant: Optional[Tuple[int, ...]],
    eigenvalue_threshold: Optional[float],
    max_pcs: int,
    segment_mode: str,
    fd_pc_mode: str,
    fd_freq_res_hz: float,
    fd_overlap: float,
    fd_noise_k: float,
    fd_torso_pct: float,
) -> Dict[str, Any]:
    """배치용 — 배열은 반환하지 않는다(keep_signals=False)."""
    return probe_file(
        filepath, fs_hz=fs_hz, tolerance_ms=tolerance_ms,
        max_interp_gap_steps=max_interp_gap_steps, window_sec=window_sec,
        low_hz=low_hz, high_hz=high_hz, filter_order=filter_order,
        omega=omega, n_streams=n_streams, use_ant=use_ant,
        eigenvalue_threshold=eigenvalue_threshold, max_pcs=max_pcs,
        segment_mode=segment_mode,
        fd_pc_mode=fd_pc_mode, fd_freq_res_hz=fd_freq_res_hz,
        fd_overlap=fd_overlap, fd_noise_k=fd_noise_k, fd_torso_pct=fd_torso_pct,
        keep_signals=False,
    )


@st.cache_data(show_spinner="파일 1개 단계 재현 중...", max_entries=32)
def single_file_stages_cached(
    filepath: str,
    fs_hz: float,
    tolerance_ms: float,
    max_interp_gap_steps: int,
    window_sec: float,
    low_hz: float,
    high_hz: float,
    filter_order: int,
    omega: int,
    n_streams: int,
    use_ant: Optional[Tuple[int, ...]],
    eigenvalue_threshold: Optional[float],
    max_pcs: int,
    segment_mode: str,
    fd_pc_mode: str,
    fd_freq_res_hz: float,
    fd_overlap: float,
    fd_noise_k: float,
    fd_torso_pct: float,
) -> Dict[str, Any]:
    """단일 파일 추적 페이지용 — 신호/맵 배열까지 반환한다(디스크에는 저장 안 함)."""
    return probe_file(
        filepath, fs_hz=fs_hz, tolerance_ms=tolerance_ms,
        max_interp_gap_steps=max_interp_gap_steps, window_sec=window_sec,
        low_hz=low_hz, high_hz=high_hz, filter_order=filter_order,
        omega=omega, n_streams=n_streams, use_ant=use_ant,
        eigenvalue_threshold=eigenvalue_threshold, max_pcs=max_pcs,
        segment_mode=segment_mode,
        fd_pc_mode=fd_pc_mode, fd_freq_res_hz=fd_freq_res_hz,
        fd_overlap=fd_overlap, fd_noise_k=fd_noise_k, fd_torso_pct=fd_torso_pct,
        keep_signals=True,
    )


@st.cache_data(show_spinner=False, max_entries=128)
def verify_file_cached(
    filepath: str,
    fs_hz: float,
    tolerance_ms: float,
    max_interp_gap_steps: int,
    window_sec: float,
    low_hz: float,
    high_hz: float,
    filter_order: int,
    omega: int,
    n_streams: int,
    use_ant: Optional[Tuple[int, ...]],
    eigenvalue_threshold: Optional[float],
    max_pcs: int,
    segment_mode: str,
    fd_pc_mode: str,
    fd_freq_res_hz: float,
    fd_overlap: float,
    fd_noise_k: float,
    fd_torso_pct: float,
) -> Dict[str, Any]:
    """
    q_diagnostics 재작성본 ≡ 프로덕션 select_streams/select_pcs 등가성 확인.

    fd_* 는 호출부가 probe_kwargs() 를 통째로 넘기기 때문에 받기만 하고 아래로
    전달하지 않는다 — FallDeFi 피처는 스트림/PC 선택에 관여하지 않는다.
    (캐시 키에는 남아 있어 파라미터가 바뀌면 새로 계산된다.)
    """
    return verify_file(
        filepath, fs_hz=fs_hz, tolerance_ms=tolerance_ms,
        max_interp_gap_steps=max_interp_gap_steps, window_sec=window_sec,
        low_hz=low_hz, high_hz=high_hz, filter_order=filter_order,
        omega=omega, n_streams=n_streams, use_ant=use_ant,
        eigenvalue_threshold=eigenvalue_threshold, max_pcs=max_pcs,
        segment_mode=segment_mode,
    )


def probe_batch_cached(
    filepaths: Sequence[str],
    cache_dir: str,
    probe_params: Dict[str, Any],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, List[str]]:
    """
    캐시되지 **않은** 래퍼. records/q_long/sub_long/pc_long 네 종류를 각각
    별도 parquet 에 쌓는다.

    Returns:
        (records, q_long, sub_long, pc_long, errors)
    """
    hash8 = params_hash(probe_params)
    paths = {k: cache_path(cache_dir, k, hash8) for k in ("record", "q", "sub", "pc")}

    hits, missing = load_cached_rows(paths["record"], filepaths)

    errors: List[str] = []
    fresh_rec = fresh_q = fresh_sub = fresh_pc = pd.DataFrame()

    if missing:
        fresh_rec, fresh_q, fresh_sub, fresh_pc, errors = probe_batch(
            missing, progress_callback=progress_callback,
            prober=lambda fp, **kw: probe_file_cached(fp, **kw),
            **probe_params,
        )
        if not fresh_rec.empty:
            append_cached_rows(paths["record"], fresh_rec)
            append_cached_long(paths["q"], fresh_q)
            append_cached_long(paths["sub"], fresh_sub)
            append_cached_long(paths["pc"], fresh_pc)
            write_params_json(cache_dir, hash8, probe_params)
    elif progress_callback:
        progress_callback(len(filepaths), len(filepaths), "캐시 적중")

    # 모든 파일이 실패하면 concat 할 것이 없다 — 예외 대신 빈 결과를 돌려주고
    # 호출부가 errors 를 표시하게 한다.
    parts = [d for d in (hits, fresh_rec) if not d.empty]
    records = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    # long 프레임은 위에서 이미 디스크에 병합됐으므로 디스크에서만 읽으면 된다.
    # (다시 concat 하면 중복이 생기고 전 컬럼 drop_duplicates 가 비싸진다)
    return (records,
            load_long_rows(paths["q"], filepaths),
            load_long_rows(paths["sub"], filepaths),
            load_long_rows(paths["pc"], filepaths),
            errors)


def probe_cache_status(cache_dir: str, probe_params: Dict[str, Any],
                       filepaths: Sequence[str]) -> Dict[str, Any]:
    """페이지가 '지금 몇 개가 공짜인지' 캡션으로 알려주기 위한 조회 (계산 없음)."""
    hash8 = params_hash(probe_params)
    hits, missing = load_cached_rows(cache_path(cache_dir, "record", hash8), filepaths)
    return {"params_hash": hash8, "cached": len(hits), "missing": len(missing),
            "total": len(filepaths)}


def meta_cache_status(cache_dir: str, fingerprint: Dict[str, Any],
                      filepaths: Sequence[str]) -> Dict[str, Any]:
    hash8 = params_hash(fingerprint)
    hits, missing = load_cached_rows(cache_path(cache_dir, "meta", hash8), filepaths)
    return {"params_hash": hash8, "cached": len(hits), "missing": len(missing),
            "total": len(filepaths)}


# --- 교차 도메인 분류용 CWT/ACF (P7) ----------------------------------------

@st.cache_data(show_spinner=False)
def sample_fall_nonfall_cached(
    mendeley_root: str,
    envs: Tuple[int, ...],
    fall_activities: Tuple[int, ...],
    nonfall_activities: Tuple[int, ...],
    n_fall_per_cell: int,
    n_nonfall_per_cell: int,
    seed: int,
    balance_subjects: bool = True,
) -> pd.DataFrame:
    """
    `(env, activity)` 로 층화해 낙상/비낙상을 따로 뽑고 합친다.

    `(env, fall_label)` 로 뽑으면 비낙상 안의 활동 구성이 환경마다 달라질 수 있고,
    그러면 **활동 격차를 방 격차로 오독**한다. 활동 단위로 층화하면 환경 간 활동
    주변분포가 같아진다.
    """
    file_index = get_file_index_cached(mendeley_root)
    falls = stratified_sample(
        file_index, envs=envs, activities=fall_activities,
        n_per_cell=n_fall_per_cell, seed=seed,
        strata=("env", "activity"), balance_subjects=balance_subjects,
    )
    nonfalls = stratified_sample(
        file_index, envs=envs, activities=nonfall_activities,
        n_per_cell=n_nonfall_per_cell, seed=seed,
        strata=("env", "activity"), balance_subjects=balance_subjects,
    )
    parts = [d for d in (falls, nonfalls) if not d.empty]
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


@st.cache_data(show_spinner="파일 1개 CWT/ACF 계산 중...", max_entries=32)
def cwt_acf_file_cached(filepath: str, rx: int, **params_scalars: Any) -> Dict[str, Any]:
    """단일 파일 시각화용 — 신호까지 함께 반환한다(디스크 캐시에는 넣지 않는다)."""
    return extract_one(filepath, rx, DeepFeatureParams(**params_scalars), keep_signal=True)


def cwt_acf_batch_cached(
    filepaths: Sequence[str],
    rxs: Sequence[int],
    params: DeepFeatureParams,
    cache_dir: str,
    hash8: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
):
    """
    캐시되지 **않은** 래퍼 — `extract_batch` 가 npz 디스크 캐시를 직접 관리한다.
    (@st.cache_data 를 씌우면 파일 목록이 키가 되어 표본을 늘릴 때 이전 작업을 버린다)
    """
    return _cwtacf_extract_batch(
        filepaths, rxs, params, cache_dir, hash8, progress_callback=progress_callback,
    )


def cwt_acf_status(cache_dir: str, hash8: str, filepaths: Sequence[str],
                   rxs: Sequence[int]) -> Dict[str, Any]:
    return _cwtacf_status(cache_dir, hash8, filepaths, rxs)
