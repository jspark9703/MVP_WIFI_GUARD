"""
값싼 메타데이터 pass — 90개 complex CSI 문자열 컬럼을 건너뛰고 링크 계층 정보만 읽는다.
실측 12~25 ms/파일 (전체 CSI 파싱은 ~0.2~0.5 s/파일). streamlit 을 import 하지 않는다.

scripts/07_agc_distribution.py 의 usecols 트릭을 일반화한 것으로, AGC 뿐 아니라
RSSI 3채널과 패킷 타이밍까지 뽑는다. RSSI 는 이 레포에서 지금까지 아무도 분석하지 않았다.
"""

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from lib.constants import (
    DEAD_NOISE_VALUE,
    META_COLUMNS,
    NATIVE_FS_HZ,
    NOMINAL_GAP_MS,
    RSSI_OFFSET_DB,
)


def total_rss_dbm(rssi_a: np.ndarray, rssi_b: np.ndarray, rssi_c: np.ndarray,
                  agc: np.ndarray) -> np.ndarray:
    """
    Intel 5300 RSSI 3채널 + AGC -> 실제 수신전력 dBm.

    Linux 802.11n CSI Tool 의 ``get_total_rss`` 와 동일:
        mag = Σ 10^(rssi_i/10)   (rssi_i == 0 인 채널은 '미보고'이므로 제외)
        dBm = 10*log10(mag) − 44 − agc

    raw RSSI 만 보면 환경 차이가 거의 없지만(E1/E2/E3 ≈ 37.6/37.8/37.3), AGC 가
    크게 다르기 때문에(37.9/43.5/38.5) 실제 수신전력은 ~5 dB 차이가 난다.
    즉 raw RSSI 를 그대로 그리면 시프트를 놓친다.
    """
    mag = np.zeros(len(agc), dtype=np.float64)
    for r in (rssi_a, rssi_b, rssi_c):
        r = np.asarray(r, dtype=np.float64)
        mag += np.where(r > 0, np.power(10.0, r / 10.0), 0.0)
    with np.errstate(divide="ignore"):
        dbm = 10.0 * np.log10(np.where(mag > 0, mag, np.nan))
    return dbm - RSSI_OFFSET_DB - np.asarray(agc, dtype=np.float64)


def _mode_or_nan(series: pd.Series) -> float:
    if series.empty:
        return float("nan")
    counts = series.value_counts()
    return float(counts.index[0])


def read_file_metadata(filepath: str, fs_hz: float = NATIVE_FS_HZ) -> Dict[str, Any]:
    """
    파일 1개의 링크 계층 요약을 반환한다 (전부 스칼라 -> parquet 한 줄).

    타임스탬프는 **절단하지 않는다**. data_loader.load_csi_file 은 손상 구간을
    잘라내지만, 여기서는 손상 자체가 관측 대상이므로 원본 그대로 분석한다.
    """
    nominal_gap_us = NOMINAL_GAP_MS * 1000.0

    df = pd.read_csv(filepath, usecols=META_COLUMNS)
    n = len(df)

    ts = df["timestamp_low"].to_numpy(dtype=np.float64)
    gaps = np.diff(ts) if n > 1 else np.zeros(0)
    pos = gaps[gaps > 0]

    if pos.size:
        median_gap_us = float(np.median(pos))
        mean_gap_us = float(np.mean(pos))
        gap_cv = float(np.std(pos) / mean_gap_us) if mean_gap_us > 0 else float("nan")
        gap_p95_us = float(np.percentile(pos, 95))
        frac_gap_gt_2x = float(np.mean(pos > 2.0 * nominal_gap_us))
        duration_sec = float(np.sum(pos) / 1e6)
    else:
        median_gap_us = mean_gap_us = gap_p95_us = float("nan")
        gap_cv = frac_gap_gt_2x = float("nan")
        duration_sec = 0.0

    n_nonpositive = int(np.sum(gaps <= 0)) if gaps.size else 0
    est_fs_hz = float(1e6 / median_gap_us) if median_gap_us and median_gap_us > 0 else float("nan")

    agc = df["agc"].to_numpy(dtype=np.float64)
    rss = total_rss_dbm(df["rssi_a"], df["rssi_b"], df["rssi_c"], agc)

    rssi_means = {c: float(df[c].mean()) for c in ("rssi_a", "rssi_b", "rssi_c")}
    best_ant = int(np.argmax([rssi_means["rssi_a"], rssi_means["rssi_b"], rssi_means["rssi_c"]]))

    noise_unique = int(df["noise"].nunique())

    return {
        "filepath": str(filepath),
        "n_packets": int(n),
        "duration_sec": duration_sec,
        "span_sec": float((ts[-1] - ts[0]) / 1e6) if n > 1 else 0.0,
        # --- 패킷 타이밍 (P2 헤드라인) ---
        "median_gap_ms": median_gap_us / 1000.0 if median_gap_us == median_gap_us else float("nan"),
        "mean_gap_ms": mean_gap_us / 1000.0 if mean_gap_us == mean_gap_us else float("nan"),
        "gap_cv": gap_cv,
        "gap_p95_ms": gap_p95_us / 1000.0 if gap_p95_us == gap_p95_us else float("nan"),
        "frac_gap_gt_2x": frac_gap_gt_2x,
        "n_nonpositive_gaps": n_nonpositive,
        "frac_nonpositive": float(n_nonpositive / gaps.size) if gaps.size else 0.0,
        "est_fs_hz": est_fs_hz,
        "is_lowrate": bool(est_fs_hz < 0.5 * fs_hz) if est_fs_hz == est_fs_hz else False,
        # --- AGC ---
        "agc_mean": float(np.mean(agc)), "agc_std": float(np.std(agc)),
        "agc_min": float(np.min(agc)), "agc_max": float(np.max(agc)),
        # --- RSSI / 실제 수신전력 ---
        "rssi_a_mean": rssi_means["rssi_a"], "rssi_b_mean": rssi_means["rssi_b"],
        "rssi_c_mean": rssi_means["rssi_c"],
        "rssi_a_std": float(df["rssi_a"].std()), "rssi_b_std": float(df["rssi_b"].std()),
        "rssi_c_std": float(df["rssi_c"].std()),
        "rssi_spread": float(max(rssi_means.values()) - min(rssi_means.values())),
        "rssi_best_ant": best_ant,
        "rss_dbm_mean": float(np.nanmean(rss)), "rss_dbm_std": float(np.nanstd(rss)),
        # --- 죽은 컬럼 증명 ---
        "noise_mean": float(df["noise"].mean()),
        "noise_n_unique": noise_unique,
        "noise_is_dead": bool(noise_unique == 1 and float(df["noise"].iloc[0]) == DEAD_NOISE_VALUE),
        # --- 링크 설정 ---
        "rate_mode": _mode_or_nan(df["rate"]),
        "nrx_mode": _mode_or_nan(df["Nrx"]),
        "ntx_mode": _mode_or_nan(df["Ntx"]),
    }


def metadata_batch(
    filepaths: Sequence[str],
    fs_hz: float = NATIVE_FS_HZ,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    reader: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    파일 목록을 순회한다. 한 파일이 깨져도 배치 전체를 중단시키지 않는다
    (streamlit_inhouse_app 의 extract_batch 패턴).

    Args:
        reader: 파일 1개를 읽는 함수. Streamlit 레이어가 캐시된 버전을 주입한다.
    """
    read = reader or read_file_metadata
    total = len(filepaths)
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    for i, fp in enumerate(filepaths):
        name = str(fp).replace("\\", "/").rsplit("/", 1)[-1]
        if progress_callback:
            progress_callback(i, total, name)
        try:
            rows.append(read(str(fp), fs_hz))
        except Exception as exc:  # noqa: BLE001 - 배치는 계속 진행되어야 한다
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    if progress_callback:
        progress_callback(total, total, "완료")

    return pd.DataFrame(rows), errors
