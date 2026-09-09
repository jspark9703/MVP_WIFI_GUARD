"""인하우스 CSI 레코딩 -> 3초 윈도우별 S3 스칼로그램 + PCA-ACF.

사라진 오프라인 스크립트 `build_inhouse_native_hz_midpoint_s3_acf.py` 의
recording-level 경로(`compute_native_features`)를 재구성한 것이다.
`features/config.py` 의 원본 `realtime.py` 는 `resampled[-window_samples:]` 로
마지막 윈도우 하나만 처리하므로, 슬라이딩 부분은 여기서 새로 구현한다.

파이프라인 (원본 순서 유지):
    load_inhouse_file -> 비단조 타임스탬프 제거 -> 네이티브 fs 측정/양자화
    -> 서브캐리어 30개 균등 선택 -> 균일 그리드 리샘플
    -> 3.0s / 0.25s 슬라이딩 -> 윈도우 라벨링
    -> 윈도우별: select_streams -> select_pc_signal -> S3 + PCA-ACF

라벨링 규칙 두 가지:
    "midpoint" - 낙상 구간의 중점이 윈도우 [start, end) 안에 있으면 fall.
                 학습 파이프라인(s3_acf_native_hz_midpoint)과 동일.
    "contains" - 낙상 구간이 윈도우에 통째로 포함되면 fall. 더 보수적이라 윈도우가 적다.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from .features.acf import compute_pca_acf
from .features.common import compute_s3_scalogram, select_pc_signal, select_streams
from .features.config import (
    FeatureConfig,
    measure_native_fs,
    quantize_fs,
    resample_uniform,
    select_subcarrier_indices,
)
from .features.stages import compute_scalogram_stages
from .inhouse_loader import load_inhouse_file

LABEL_RULES = ("midpoint", "contains")

DEFAULT_STRIDE_SEC = 0.25


def find_fall_intervals(is_fall_row: np.ndarray) -> List[Tuple[int, int]]:
    """is_fall_row 의 연속 True 구간 [start, end] 목록 (end 포함).

    자체 수집 데이터는 파일당 보통 1개의 연속 구간을 갖는다.

    Args:
        is_fall_row: (N,) bool

    Returns:
        (start_idx, end_idx) 튜플 리스트. 낙상이 없으면 빈 리스트.
    """
    flags = np.asarray(is_fall_row, dtype=bool)
    if not flags.any():
        return []
    padded = np.concatenate([[False], flags, [False]])
    edges = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    return [(int(s), int(e)) for s, e in zip(starts, ends)]


def window_starts(n_samples: int, window_samples: int, stride_samples: int) -> np.ndarray:
    """tail-drop 슬라이딩 윈도우 시작 인덱스.

    마지막 불완전 윈도우는 버린다 (원본 window_starts_no_tail 과 동일).
    """
    if n_samples < window_samples:
        return np.empty(0, dtype=np.int64)
    stride_samples = max(1, int(stride_samples))
    return np.arange(0, n_samples - window_samples + 1, stride_samples, dtype=np.int64)


def label_windows(
    starts: np.ndarray,
    window_samples: int,
    intervals: Sequence[Tuple[int, int]],
    rule: str = "midpoint",
) -> np.ndarray:
    """윈도우별 fall 여부를 판정한다.

    Args:
        starts: 윈도우 시작 인덱스 (리샘플된 그리드 기준)
        window_samples: 윈도우 길이 (샘플)
        intervals: 그리드 기준 낙상 구간 [(start, end)] (end 포함)
        rule: "midpoint" 또는 "contains"

    Returns:
        (len(starts),) bool

    Raises:
        ValueError: 알 수 없는 rule
    """
    if rule not in LABEL_RULES:
        raise ValueError(f"rule must be one of {LABEL_RULES}, got {rule!r}")

    labels = np.zeros(len(starts), dtype=bool)
    if not intervals:
        return labels

    ends = starts + window_samples  # 배타적 상한
    for i0, i1 in intervals:
        if rule == "midpoint":
            midpoint = (i0 + i1) // 2
            labels |= (starts <= midpoint) & (midpoint < ends)
        else:  # contains
            labels |= (starts <= i0) & (i1 < ends)
    return labels


def _map_intervals_to_grid(
    intervals: Sequence[Tuple[int, int]],
    times: np.ndarray,
    fs_hz: float,
    grid_len: int,
) -> List[Tuple[int, int]]:
    """원본 샘플 인덱스 구간을 리샘플된 그리드 인덱스로 변환한다.

    리샘플로 인덱스가 바뀌므로 원본 인덱스를 그대로 쓰면 안 된다.
    시각을 거쳐 변환한다: grid_idx = round((t - t0) * fs).
    """
    if not intervals:
        return []
    t0 = float(times[0])
    mapped: List[Tuple[int, int]] = []
    for i0, i1 in intervals:
        g0 = int(round((float(times[i0]) - t0) * fs_hz))
        g1 = int(round((float(times[i1]) - t0) * fs_hz))
        g0 = max(0, min(grid_len - 1, g0))
        g1 = max(0, min(grid_len - 1, g1))
        if g1 >= g0:
            mapped.append((g0, g1))
    return mapped


def _subsample_evenly(indices: np.ndarray, max_count: Optional[int]) -> np.ndarray:
    """균등 간격으로 최대 max_count 개만 남긴다 (양 끝 포함)."""
    if max_count is None or len(indices) <= max_count:
        return indices
    if max_count <= 0:
        return indices[:0]
    positions = np.round(np.linspace(0, len(indices) - 1, max_count)).astype(int)
    return indices[np.unique(positions)]


def load_resampled_recording(
    filepath: Union[str, Path],
    config: Optional[FeatureConfig] = None,
) -> Dict[str, Any]:
    """레코딩을 로드해 네이티브 fs 균일 그리드로 리샘플한다 (윈도우 분할 전까지).

    RAW CSI 시각화와 피처 추출이 공유하는 앞단이다. `_csi` 파일(Q7~Q12)처럼
    label 컬럼이 없는 경우 낙상 구간은 빈 리스트가 된다.

    Args:
        filepath: `data/raw/*.csv` 경로
        config: FeatureConfig

    Returns:
        dict:
            resampled           (M, n_selected) float32 - 균일 그리드 진폭
            time_sec            (M,) float64 - 그리드 시간축 (첫 패킷 기준)
            fs_hz               float - 양자화된 네이티브 fs
            selected_subcarriers (n_selected,) int32 - 원본 슬롯 인덱스
            raw_intervals       원본 인덱스 기준 낙상 구간
            grid_intervals      그리드 인덱스 기준 낙상 구간
            fall_intervals_sec  초 단위 낙상 구간
            + 파일 메타 (person, session, config, fresnel_a/b, take, file_label, has_label)
    """
    cfg = config or FeatureConfig()
    filepath = Path(filepath)

    data = load_inhouse_file(filepath)
    times = np.asarray(data["time_sec"], dtype=np.float64)
    amplitude = np.asarray(data["amplitude"], dtype=np.float32)
    is_fall_row = np.asarray(data["is_fall_row"], dtype=bool)

    # 비단조 타임스탬프 제거. 라벨에도 같은 마스크를 적용해야 인덱스가 어긋나지 않는다.
    monotonic = np.ones(len(times), dtype=bool)
    monotonic[1:] = np.diff(times) > 0
    times, amplitude, is_fall_row = times[monotonic], amplitude[monotonic], is_fall_row[monotonic]

    fs_hz = quantize_fs(measure_native_fs(times), cfg)
    selected_subcarriers = select_subcarrier_indices(amplitude.shape[1], cfg)
    resampled = resample_uniform(times, amplitude[:, selected_subcarriers], fs_hz)

    raw_intervals = find_fall_intervals(is_fall_row)
    grid_intervals = _map_intervals_to_grid(raw_intervals, times, fs_hz, len(resampled))

    return {
        "resampled": resampled,
        "time_sec": np.arange(len(resampled), dtype=np.float64) / fs_hz,
        "fs_hz": float(fs_hz),
        "selected_subcarriers": selected_subcarriers,
        "raw_intervals": raw_intervals,
        "grid_intervals": grid_intervals,
        "fall_intervals_sec": [(float(times[i0]), float(times[i1])) for i0, i1 in raw_intervals],
        "n_packets": int(len(times)),
        "duration_sec": float(times[-1] - times[0]),
        "source_file": str(filepath),
        "person": data["person"],
        "session": data["session"],
        "config": data["config"],
        "fresnel_a": data["fresnel_a"],
        "fresnel_b": data["fresnel_b"],
        "take": data["take"],
        "file_label": data["file_label"],
        "has_label": data["has_label"],
        "is_fall_take": data["is_fall_take"],
    }


def extract_recording_features(
    filepath: Union[str, Path],
    config: Optional[FeatureConfig] = None,
    rule: str = "midpoint",
    stride_sec: float = DEFAULT_STRIDE_SEC,
    fall_only: bool = True,
    max_windows: Optional[int] = None,
    stages: bool = False,
) -> Dict[str, Any]:
    """레코딩 1개에서 윈도우별 S3 + PCA-ACF 를 만든다.

    Args:
        filepath: `data/raw/*.csv` 경로
        config: FeatureConfig (기본값 = 학습 설정)
        rule: 윈도우 라벨링 규칙 ("midpoint" | "contains")
        stride_sec: 슬라이딩 간격 (초)
        fall_only: True 면 fall 로 라벨된 윈도우만 처리
        max_windows: 처리할 윈도우 수 상한 (균등 샘플). None = 전부
        stages: True 면 윈도우별 S0~S3 를 전부 반환 (Page 1 용, 메모리 4배)

    Returns:
        dict:
            s3            (K, 224, 224) float32
            acf           (K, 128, 64) float32
            signal        (K, window_samples) float32 - PCA motion signal
            stage_maps    stages=True 일 때만. {"s0":(K,224,224), ...}
            window_start_sec / window_center_sec (K,) float64
            is_fall       (K,) bool
            window_index  (K,) int64 - 전체 슬라이딩 중 원래 순번
            fs_hz, window_samples, n_windows_total, n_windows_fall
            fall_intervals_sec  [(start, end)] 초 단위
            per_window_stats  길이 K 의 dict 리스트
            + 파일 메타 (person, session, config, fresnel_a/b, take, file_label, source_file)

    Raises:
        ValueError: 파일이 3초보다 짧거나 라벨 규칙이 잘못된 경우
    """
    cfg = config or FeatureConfig()
    filepath = Path(filepath)

    recording = load_resampled_recording(filepath, cfg)
    resampled = recording["resampled"]
    fs_hz = recording["fs_hz"]
    selected_subcarriers = recording["selected_subcarriers"]

    window_samples = int(round(cfg.window_seconds * fs_hz))
    if len(resampled) < window_samples:
        raise ValueError(
            f"{filepath.name}: resampled length {len(resampled)} < window {window_samples}"
        )

    stride_samples = max(1, int(round(stride_sec * fs_hz)))
    starts = window_starts(len(resampled), window_samples, stride_samples)
    labels = label_windows(starts, window_samples, recording["grid_intervals"], rule=rule)

    keep = np.flatnonzero(labels) if fall_only else np.arange(len(starts))
    keep = _subsample_evenly(keep, max_windows)

    variance_radius = max(1, int(round(cfg.moving_variance_radius_seconds * fs_hz)))

    s3_list: List[np.ndarray] = []
    acf_list: List[np.ndarray] = []
    signal_list: List[np.ndarray] = []
    stage_lists: Dict[str, List[np.ndarray]] = {"s0": [], "s1": [], "s2": [], "s3": []}
    stats_list: List[Dict[str, Any]] = []

    for widx in keep:
        start = int(starts[widx])
        window = resampled[start : start + window_samples]

        selected_streams, _stream_q = select_streams(window, omega=cfg.omega, w_radius=variance_radius)
        signal, pc_stats = select_pc_signal(window, selected_streams, w_radius=variance_radius)

        if stages:
            stage_maps, cwt_stats = compute_scalogram_stages(
                signal=signal,
                fs_hz=fs_hz,
                freq_min_hz=cfg.freq_min_hz,
                freq_max_hz=cfg.freq_max_hz,
                image_size=cfg.image_size,
                th_scmax=cfg.th_scmax,
                kappa=cfg.kappa,
                carrier_hz=cfg.carrier_hz,
            )
            cwt_stats.pop("freqs_high_to_low", None)
            for name in stage_lists:
                stage_lists[name].append(stage_maps[name])
            s3 = stage_maps["s3"]
        else:
            s3, cwt_stats = compute_s3_scalogram(
                signal=signal,
                fs_hz=fs_hz,
                freq_min_hz=cfg.freq_min_hz,
                freq_max_hz=cfg.freq_max_hz,
                image_size=cfg.image_size,
                th_scmax=cfg.th_scmax,
                kappa=cfg.kappa,
                carrier_hz=cfg.carrier_hz,
            )

        acf = compute_pca_acf(
            signal,
            fs_hz=fs_hz,
            lag_seconds=cfg.acf_lag_seconds,
            time_bins=cfg.acf_time_bins,
            lag_output_bins=cfg.acf_lag_output_bins,
            clip_percentile=cfg.acf_clip_percentile,
        )

        s3_list.append(s3.astype(np.float32))
        acf_list.append(acf[0].astype(np.float32))  # (1,128,64) -> (128,64)
        signal_list.append(np.asarray(signal, dtype=np.float32))
        stats_list.append(
            {
                **pc_stats,
                **cwt_stats,
                "window_index": int(widx),
                "selected_stream_count": int(len(selected_streams)),
            }
        )

    def _stack(items: List[np.ndarray], shape: Tuple[int, ...]) -> np.ndarray:
        return np.stack(items, axis=0) if items else np.empty((0, *shape), dtype=np.float32)

    result: Dict[str, Any] = {
        "s3": _stack(s3_list, (cfg.image_size, cfg.image_size)),
        "acf": _stack(acf_list, (cfg.acf_lag_output_bins, cfg.acf_time_bins)),
        "signal": _stack(signal_list, (window_samples,)),
        "window_index": np.asarray(keep, dtype=np.int64),
        "window_start_sec": starts[keep] / fs_hz if len(keep) else np.empty(0),
        "window_center_sec": (starts[keep] + window_samples / 2.0) / fs_hz
        if len(keep)
        else np.empty(0),
        "is_fall": labels[keep] if len(keep) else np.empty(0, dtype=bool),
        "fs_hz": float(fs_hz),
        "window_samples": int(window_samples),
        "stride_samples": int(stride_samples),
        "n_windows_total": int(len(starts)),
        "n_windows_fall": int(labels.sum()),
        "rule": rule,
        "fall_intervals_sec": recording["fall_intervals_sec"],
        "selected_subcarriers": selected_subcarriers,
        "per_window_stats": stats_list,
        "source_file": recording["source_file"],
        "person": recording["person"],
        "session": recording["session"],
        "config": recording["config"],
        "fresnel_a": recording["fresnel_a"],
        "fresnel_b": recording["fresnel_b"],
        "take": recording["take"],
        "file_label": recording["file_label"],
        "has_label": recording["has_label"],
        "is_fall_take": recording["is_fall_take"],
    }
    if stages:
        result["stage_maps"] = {
            name: _stack(items, (cfg.image_size, cfg.image_size))
            for name, items in stage_lists.items()
        }
    return result


def _missing(value: Any) -> bool:
    """None 또는 NaN (파일 인덱스 DataFrame 경유 시 float NaN 이 된다)."""
    return value is None or (isinstance(value, float) and np.isnan(value))


def fresnel_group_label(fresnel_a: Any, fresnel_b: Any, mode: str = "a") -> str:
    """F 그룹 라벨 문자열. mode='a' -> "F1", mode='ab' -> "F1-2".

    F 는 `_labeled` 파일(Q1~Q6)에만 있다. 일상행동 `_csi` 파일(Q7~Q12)은
    프레넬 영역이 라벨되지 않아 "F?" 가 된다.
    """
    if _missing(fresnel_a):
        return "F?"
    if mode == "a":
        return f"F{int(fresnel_a)}"
    if _missing(fresnel_b):
        return f"F{int(fresnel_a)}-?"
    return f"F{int(fresnel_a)}-{int(fresnel_b)}"
