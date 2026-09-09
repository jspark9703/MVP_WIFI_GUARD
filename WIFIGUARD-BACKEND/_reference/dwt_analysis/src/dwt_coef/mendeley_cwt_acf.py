"""
Mendeley 데이터셋용 CWT S3 스케일로그램 + PCA-ACF 피처 드라이버.

## 왜 이 모듈이 필요한가

CWT/ACF 피처 커널은 이미 `src/dwt_coef/features/` 에 있고 in-house(ESP32-C5) 경로에서 쓰이지만,
**Mendeley 를 읽는 층이 없다.** 반대로 `src/feature_cwt_acf/amfall_losnlos_common.py` 에는
Mendeley 전용 로더가 있지만 그 파일은 git 미추적이고 `acf.py` 의 `from .common import` 가 깨져
있으며(`common.py`/`__init__.py` 없음) 아무 데서도 import 되지 않는 구세대 드롭이다.

그래서 이 모듈은 **커널은 `features/` 에서 가져오고, Mendeley 글루 3개만** 이식한다:
`load_rx_amplitude` / `resample_amplitude` / `crop_signal_around_activity`
(출처: `src/feature_cwt_acf/amfall_losnlos_common.py`, 동작 그대로).

두 구현이 같은 값을 내는지는 `verify_against_reference()` 로 확인할 수 있다 —
실측상 `select_streams` 결과는 동일하고 PC 신호·S3 는 `max|diff| = 0.0` 이다.

## Mendeley 특성 (320 Hz)

- CSV 는 13개 메타 컬럼 뒤에 rx 3개 × 서브캐리어 30개 = 90개 복소 CSI 문자열 (`"15+15i"`, `"-17+-23i"`).
- 파일 1개 = 활동 1 phase, 정확히 4.00초, 네이티브 320 Hz.
- `effective_freq_max = min(freq_max_hz, fs/2) = **160 Hz**` (설정값 170 은 나이퀴스트에서 잘린다).
- `lag_steps = round(0.4 × 320) = **128**` 이라 ACF lag 축 보간이 no-op 이다.
- 출력 형상: S3 `(224, 224)`, ACF `(128, 64)`.

`crop_signal_around_activity` 는 4초 전체를 보고 이동분산의 이동합 피크를 찾아 3초를 잘라낸다.
라벨을 쓰지 않으므로 교차 샘플 누수는 없지만, **스트리밍 배포보다 낙관적인 설정**이라는 점은
결과 해석 시 감안해야 한다.

NOTE: 신규 파일이며 `dvc.yaml` 의 어떤 스테이지 dep 에도 없다 (DVC 영향 없음).
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from .features import (
    FeatureConfig,
    compute_pca_acf,
    compute_s3_scalogram,
    select_pc_signal,
    select_streams,
)
from .features.common import moving_variance

#: Mendeley CSV 레이아웃 (Intel 5300)
CSI_START_COL: int = 13
RX_COUNT: int = 3
SUBCARRIERS_PER_RX: int = 30
DEFAULT_FS_HZ: float = 320.0

#: activity in {2, 5} 가 낙상 (A02 = 앉은 자세에서, A05 = 선 자세에서)
FALL_ACTIVITIES: Tuple[int, ...] = (2, 5)

#: 비낙상 중 hard negative — 낙상과 혼동되기 쉬운 활동
HARD_NEGATIVE_ACTIVITIES: Tuple[int, ...] = (3, 11)  # 눕기, 앉기


# --- Mendeley 글루 (feature_cwt_acf/amfall_losnlos_common.py 에서 이식) -------

def moving_sum(values: np.ndarray, radius: int) -> np.ndarray:
    """
    폭 `2*radius+1` 이동합. `features/common` 에는 moving_mean/moving_variance 만 있고
    이것만 없어서 참조 구현과 동일하게 이식한다 (crop 의 활동 피크 탐지에 쓰인다).
    """
    radius = max(0, int(radius))
    if radius == 0:
        return values.astype(np.float64)
    kernel = np.ones(2 * radius + 1, dtype=np.float64)
    return np.convolve(values.astype(np.float64), kernel, mode="same")


def load_timestamps(csv_path: Path) -> np.ndarray:
    """
    타임스탬프 열만 읽는다.

    공개 LOS/NLOS 파일 일부에 2^64 근처로 감긴 값이 들어 있어, 그대로 두면 리샘플러가
    거대한 시간 그리드를 만들려 한다. 32비트 마이크로초 카운터가 감긴 것으로 해석한다.
    """
    timestamps = np.loadtxt(
        csv_path, delimiter=",", skiprows=1, usecols=(0,), dtype=np.float64, ndmin=1,
    )
    huge_mask = timestamps > 1e18
    if np.any(huge_mask):
        timestamps = timestamps.copy()
        timestamps[huge_mask] = timestamps[huge_mask] - float(2**64) + float(2**32)
    return timestamps


def load_rx_amplitude(csv_path: Path, rx: int) -> Tuple[np.ndarray, np.ndarray]:
    """
    rx 1개의 서브캐리어 30개 진폭만 읽는다 (90개 복소 컬럼 전체를 파싱하지 않는다).

    Returns:
        (timestamps (N,) float64 us, amplitude (N, 30) float32)
    """
    if not 1 <= rx <= RX_COUNT:
        raise ValueError(f"rx must be 1..{RX_COUNT}, got {rx}")

    timestamps = load_timestamps(csv_path)
    start = CSI_START_COL + (rx - 1) * SUBCARRIERS_PER_RX
    raw = np.loadtxt(
        csv_path, delimiter=",", skiprows=1,
        usecols=tuple(range(start, start + SUBCARRIERS_PER_RX)), dtype=str, ndmin=2,
    )
    normalized = np.char.replace(raw, "+-", "-")
    normalized = np.char.replace(normalized, "i", "j")
    return timestamps, np.abs(normalized.astype(np.complex64)).astype(np.float32)


def resample_amplitude(
    timestamps: np.ndarray,
    amplitude: np.ndarray,
    grid_us: float,
    tolerance_us: float,
    max_interp_gap_steps: int = 64,
) -> Tuple[np.ndarray, Dict[str, int]]:
    """
    균일 그리드로 리샘플. gap 이 그리드 배수에 tolerance 안으로 맞고 스텝 수가
    `max_interp_gap_steps` 이하일 때만 선형 보간하고, 아니면 원본 샘플을 그대로 붙인다.

    Returns:
        (resampled (M, 30) float32, {interp_steps, fallback_steps, irregular_gaps, nonpositive_gaps})
    """
    if len(amplitude) == 0:
        return amplitude.astype(np.float32), {
            "interp_steps": 0, "fallback_steps": 0,
            "irregular_gaps": 0, "nonpositive_gaps": 0,
        }

    rows = [amplitude[0].astype(np.float32)]
    interp_steps = fallback_steps = irregular_gaps = nonpositive_gaps = 0

    for idx in range(1, len(amplitude)):
        gap = timestamps[idx] - timestamps[idx - 1]
        if gap <= 0:
            rows.append(amplitude[idx].astype(np.float32))
            fallback_steps += 1
            nonpositive_gaps += 1
            continue

        nearest_steps = max(1, int(round(gap / grid_us)))
        if (nearest_steps <= max_interp_gap_steps
                and abs(gap - nearest_steps * grid_us) <= tolerance_us):
            for step in range(1, nearest_steps):
                alpha = step / nearest_steps
                rows.append(((1.0 - alpha) * amplitude[idx - 1]
                             + alpha * amplitude[idx]).astype(np.float32))
                interp_steps += 1
            rows.append(amplitude[idx].astype(np.float32))
        else:
            rows.append(amplitude[idx].astype(np.float32))
            fallback_steps += 1
            irregular_gaps += 1

    return np.stack(rows, axis=0).astype(np.float32), {
        "interp_steps": interp_steps, "fallback_steps": fallback_steps,
        "irregular_gaps": irregular_gaps, "nonpositive_gaps": nonpositive_gaps,
    }


def crop_signal_around_activity(
    signal: np.ndarray,
    fs_hz: float,
    segment_sec: float,
    w_radius: int,
    wm_radius: int,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    이동분산의 이동합이 최대인 지점을 활동 피크로 보고 그 주위 `segment_sec` 를 잘라낸다.

    라벨을 쓰지 않는다. 다만 파일 전체(4초)를 보고 피크를 찾으므로 스트리밍 배포보다
    낙관적이다 — `pad_left`/`pad_right` 가 0 이 아니면 피크가 가장자리에 붙어 잘림에
    실패한 경우이므로 QC 지표로 쓴다.
    """
    target_len = int(round(segment_sec * fs_hz))
    if len(signal) == 0:
        return np.zeros(target_len, dtype=np.float32), {
            "segment_peak": 0, "crop_start": 0, "crop_end": 0,
            "pad_left": target_len, "pad_right": 0,
        }

    summed = moving_sum(moving_variance(np.abs(signal), w_radius), wm_radius)
    peak = int(np.argmax(summed))

    start = peak - target_len // 2
    end = start + target_len
    if start < 0:
        end -= start
        start = 0
    if end > len(signal):
        start = max(0, start - (end - len(signal)))
        end = len(signal)

    cropped = signal[start:end]
    pad_left = pad_right = 0
    if len(cropped) < target_len:
        deficit = target_len - len(cropped)
        pad_left = min(deficit, max(0, target_len // 2 - (peak - start)))
        pad_right = deficit - pad_left
        cropped = np.pad(cropped, (pad_left, pad_right), mode="edge")

    return cropped.astype(np.float32), {
        "segment_peak": peak, "crop_start": int(start), "crop_end": int(end),
        "pad_left": int(pad_left), "pad_right": int(pad_right),
        "segment_peak_frac": float(peak / max(len(signal), 1)),
    }


# --- 드라이버 ---------------------------------------------------------------

def parse_mendeley_name(filepath: str) -> Dict[str, Any]:
    """`E1_S01_C01_A02_T01` -> {env, subject, class_id, activity, trial, is_fall}."""
    import re

    stem = Path(filepath).stem
    m = re.match(r"^E(\d+)_S(\d+)_C(\d+)_A(\d+)_T(\d+)$", stem)
    if not m:
        raise ValueError(f"unexpected Mendeley filename: {stem}")
    env, subject, class_id, activity, trial = (int(g) for g in m.groups())
    return {
        "name": stem, "env": env, "subject": subject, "class_id": class_id,
        "activity": activity, "trial": trial,
        "is_fall": activity in FALL_ACTIVITIES,
        "hard_negative": activity in HARD_NEGATIVE_ACTIVITIES,
        # 같은 (env, subject, class_id, trial) 파일들은 하나의 물리 세션의 연속 phase 다.
        # CV 그룹은 피험자로 잡지만, 세션 canary 를 위해 키를 만들어 둔다.
        "session_id": f"E{env}_S{subject:02d}_C{class_id:02d}_T{trial:02d}",
    }


def extract_cwt_acf(
    filepath: str,
    rx: int,
    config: Optional[FeatureConfig] = None,
    fs_hz: float = DEFAULT_FS_HZ,
    interp_tol_ms: float = 2.0,
    max_interp_gap_steps: int = 64,
    wm_sec: float = 1.0,
    keep_signal: bool = False,
) -> Dict[str, Any]:
    """
    Mendeley CSV 1개 × rx 1개 -> CWT S3 스케일로그램 + PCA-ACF.

    파이프라인: rx 진폭 로드 -> 리샘플 -> select_streams(q) -> select_pc_signal(SVD 합)
    -> crop_signal_around_activity -> S3 + PCA-ACF.

    **모든 정규화가 샘플 내부에서 끝난다** (S3 는 자기 max 로, ACF 는 자기 percentile 로).
    교차 샘플 통계를 추정하지 않으므로 추출 단계에서 누수가 생길 여지가 없다.

    Returns:
        {"s3": (224,224) f32, "acf": (128,64) f32, "signal": (960,) f32 | None, "meta": {...}}
    """
    cfg = config or FeatureConfig()
    w_radius = max(1, int(round(cfg.moving_variance_radius_seconds * fs_hz)))
    wm_radius = max(1, int(round(wm_sec * fs_hz)))

    timestamps, amplitude = load_rx_amplitude(Path(filepath), rx)
    resampled, rs = resample_amplitude(
        timestamps, amplitude, grid_us=1e6 / fs_hz,
        tolerance_us=interp_tol_ms * 1000.0, max_interp_gap_steps=max_interp_gap_steps,
    )

    selected, stream_q = select_streams(resampled, omega=cfg.omega, w_radius=w_radius)
    signal, pc_stats = select_pc_signal(resampled, selected, w_radius=w_radius)
    cropped, crop_stats = crop_signal_around_activity(
        signal, fs_hz=fs_hz, segment_sec=cfg.window_seconds,
        w_radius=w_radius, wm_radius=wm_radius,
    )

    s3, cwt_stats = compute_s3_scalogram(
        cropped, fs_hz=fs_hz, freq_min_hz=cfg.freq_min_hz, freq_max_hz=cfg.freq_max_hz,
        image_size=cfg.image_size, th_scmax=cfg.th_scmax, kappa=cfg.kappa,
        carrier_hz=cfg.carrier_hz,
    )
    acf = compute_pca_acf(
        cropped, fs_hz=fs_hz, lag_seconds=cfg.acf_lag_seconds,
        time_bins=cfg.acf_time_bins, lag_output_bins=cfg.acf_lag_output_bins,
        clip_percentile=cfg.acf_clip_percentile,
    )[0]

    n_raw = max(len(amplitude), 1)
    n_res = max(len(resampled), 1)

    # 주파수 축은 row 0 = 최고주파수 (features/stages 규약). >80 Hz 대역 에너지는
    # 리샘플 보간 아티팩트를 잡아내는 QC 지표다.
    hi_rows = int(round(cfg.image_size * (1.0 - 80.0 / min(cfg.freq_max_hz, fs_hz / 2.0))))
    hi_rows = int(np.clip(hi_rows, 1, cfg.image_size - 1))

    meta: Dict[str, Any] = {
        "filepath": str(filepath), "rx": int(rx),
        **parse_mendeley_name(filepath),
        # 리샘플 QC
        "raw_rows": int(len(amplitude)), "resampled_rows": int(len(resampled)),
        "interp_steps": int(rs["interp_steps"]),
        "interp_ratio": float(rs["interp_steps"] / n_res),
        "fallback_ratio": float(rs["fallback_steps"] / max(n_raw - 1, 1)),
        "nonpositive_ratio": float(rs["nonpositive_gaps"] / max(n_raw - 1, 1)),
        "irregular_gaps": int(rs["irregular_gaps"]),
        # 스트림 / PC
        "selected_stream_count": int(len(selected)),
        "stream_q_max": float(np.max(stream_q)) if len(stream_q) else float("nan"),
        "stream_q_mean": float(np.mean(stream_q)) if len(stream_q) else float("nan"),
        **{k: v for k, v in pc_stats.items() if not isinstance(v, np.ndarray)},
        # crop QC
        **crop_stats,
        "crop_padded": bool(crop_stats["pad_left"] + crop_stats["pad_right"] > 0),
        "crop_len": int(len(cropped)),
        # CWT QC
        **{k: v for k, v in cwt_stats.items() if not isinstance(v, np.ndarray)},
        "s3_energy_above_80hz": float(np.mean(s3[:hi_rows, :])),
        "s3_total_energy": float(np.mean(s3)),
        "acf_abs_mean": float(np.mean(np.abs(acf))),
    }

    return {
        "s3": s3.astype(np.float32),
        "acf": acf.astype(np.float32),
        "signal": cropped.astype(np.float32) if keep_signal else None,
        "meta": meta,
    }


def verify_against_reference(filepath: str, rx: int,
                             config: Optional[FeatureConfig] = None,
                             fs_hz: float = DEFAULT_FS_HZ) -> Dict[str, Any]:
    """
    `src/feature_cwt_acf/amfall_losnlos_common.py` 참조 구현과 비트 동일한지 확인한다.

    참조 모듈은 패키지가 아니라서(`__init__.py` 없음) 경로를 직접 붙여 import 한다.
    참조가 없으면 `available=False` 로 조용히 넘어간다 — 참조는 git 미추적이라
    체크아웃에 따라 없을 수 있다.
    """
    import importlib.util
    import sys

    ref_path = Path(__file__).resolve().parent.parent / "feature_cwt_acf" / "amfall_losnlos_common.py"
    if not ref_path.exists():
        return {"available": False, "reason": f"참조 구현 없음: {ref_path}"}

    spec = importlib.util.spec_from_file_location("_amfall_ref", ref_path)
    if spec is None or spec.loader is None:
        return {"available": False, "reason": "참조 모듈 로드 실패"}
    ref = importlib.util.module_from_spec(spec)
    sys.modules["_amfall_ref"] = ref
    spec.loader.exec_module(ref)

    cfg = config or FeatureConfig()
    w_radius = max(1, int(round(cfg.moving_variance_radius_seconds * fs_hz)))
    wm_radius = max(1, int(round(1.0 * fs_hz)))

    ts_r, amp_r = ref.load_rx_amplitude(Path(filepath), rx)
    res_r, _ = ref.resample_amplitude(ts_r, amp_r, grid_us=1e6 / fs_hz, tolerance_us=2000.0)
    sel_r, _ = ref.select_streams(res_r, omega=cfg.omega, w_radius=w_radius)
    sig_r, _ = ref.select_pc_signal(res_r, sel_r, w_radius=w_radius)
    crop_r, _ = ref.crop_signal_around_activity(
        sig_r, fs_hz=fs_hz, segment_sec=cfg.window_seconds,
        w_radius=w_radius, wm_radius=wm_radius,
    )
    s3_r, _ = ref.compute_s3_scalogram(
        crop_r, fs_hz=fs_hz, freq_min_hz=cfg.freq_min_hz, freq_max_hz=cfg.freq_max_hz,
        image_size=cfg.image_size, th_scmax=cfg.th_scmax, kappa=cfg.kappa,
        carrier_hz=cfg.carrier_hz,
    )

    # 우리 쪽 커널(features/)로 같은 리샘플 입력에 스트림 선택을 돌려 비교한다.
    sel_ours, _ = select_streams(res_r, omega=cfg.omega, w_radius=w_radius)

    ours = extract_cwt_acf(filepath, rx, config=cfg, fs_hz=fs_hz, keep_signal=True)
    s3_diff = float(np.max(np.abs(ours["s3"] - s3_r)))
    sig_diff = float(np.max(np.abs(ours["signal"] - crop_r)))
    streams_match = bool(np.array_equal(np.sort(np.asarray(sel_ours)),
                                        np.sort(np.asarray(sel_r))))

    return {
        "available": True,
        "name": Path(filepath).stem, "rx": int(rx),
        "streams_match": streams_match,
        "n_streams_ours": int(len(sel_ours)), "n_streams_ref": int(len(sel_r)),
        "max_abs_signal_diff": sig_diff,
        "max_abs_s3_diff": s3_diff,
        "all_match": bool(streams_match and s3_diff == 0.0 and sig_diff == 0.0),
    }
