"""In-house ESP32-C5 CSI data loader.

`data/raw/` 에 수집된 자체 CSI CSV 를 파싱한다. Mendeley (Intel 5300, 3안테나 x 30
서브캐리어, `csi_1_{rx}_{sc}` wide 컬럼) 전용인 `data_loader.py` 로는 읽을 수 없는
완전히 다른 스키마이므로 독립 모듈로 분리한다.

파일명 규칙
-----------
    P{person}S{session}C{config}[F{a}-{b}]Q{take}_{labeled|csi}.csv

    P : 참여자 (사람 변화)
    S : 공간 (공간 변화)
    C : Tx/Rx 배치 구성
    F : 프레넬 그리드 위치 - `_labeled` 파일에만 존재하고 고유하지 않음 (현재 미사용)
    Q : 행동 순열 인덱스 (Q1~Q6 = 낙상 순열, Q7~Q12 = 비낙상 순열)

CSV 스키마
----------
    host_ts_utc, dev_timestamp, seq, mac, rssi, rate, channel,
    noise_floor, rx_format, sig_len, csi_len, csi[, label]

    dev_timestamp : 디바이스 클럭 (마이크로초, uint32 - 약 71분마다 wrap)
    csi           : JSON int8 배열 490개 = 245개 (I, Q) 쌍
    label         : none / a10 / a11 / a12  (`_labeled` 파일에만 존재)

    host_ts_utc 는 PC 측 벽시계로 1ms 해상도밖에 없으므로, 시간축은 항상
    dev_timestamp 를 사용한다. seq 는 +1 씩 증가하므로 패킷 손실은 없고
    불규칙한 간격은 전송 지터다 (실효 ~170 Hz).
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd

# === CSI 버퍼 구조 ===
CSI_LEN = 490  # 모든 파일의 모든 행에서 csi_len == 490
N_SLOTS = CSI_LEN // 2  # 245개 (I, Q) 쌍

# 채널 정보가 없는 슬롯 - 서브캐리어로 취급하지 않고 제외한다.
DC_SLOT = 122  # DC 널: 모든 파일 모든 행에서 정확히 (0, 0)
PILOT_SLOT = 121  # 파일별 상수 (시간에 따라 변하지 않음)
DROP_SLOTS = (PILOT_SLOT, DC_SLOT)
N_USABLE = N_SLOTS - len(DROP_SLOTS)  # 243

# === 행동 순열 (수집 프로토콜) ===
# Q1~Q6 = 낙상 순열, Q7~Q12 = 비낙상 순열
TAKE_PERMUTATION: Dict[int, Sequence[str]] = {
    1: ("A1", "A2", "A10"),                # 걷기 -> 방향 전환 -> 서 있는 상태에서 낙상
    2: ("A1", "A6", "A10"),                # 걷기 -> 물건 집기 -> 서 있는 상태에서 낙상
    3: ("A1", "A2", "A3", "A11"),          # 걷기 -> 방향 전환 -> 앉기 -> 일어나다가 낙상
    4: ("A8", "A2", "A3", "A11"),          # 달리기 -> 방향 전환 -> 앉기 -> 일어나다가 낙상
    5: ("A1", "A2", "A12"),                # 걷기 -> 방향 전환 -> 걷다가 낙상
    6: ("A1", "A6", "A1", "A12"),          # 걷기 -> 물건 집기 -> 걷기 -> 걷다가 낙상
    7: ("A1", "A2", "A3", "A4", "A1"),     # 비낙상
    8: ("A1", "A2", "A6", "A1"),           # 비낙상
    9: ("A1", "A7", "A6", "A1"),           # 비낙상
    10: ("A1", "A2", "A5"),                # 비낙상
    11: ("A8", "A2", "A3", "A4", "A1"),    # 비낙상
    12: ("A3", "A4", "A6", "A1"),          # 비낙상
}

# 순열의 마지막 행동에서 유도한, 파일이 가져야 할 낙상 라벨
TAKE_LABEL_MAP: Dict[int, str] = {
    take: seq[-1].lower() for take, seq in TAKE_PERMUTATION.items() if take <= 6
}

NO_ACTIVITY_LABEL = "none"
FALL_LABELS = ("a10", "a11", "a12")

FILENAME_RE = re.compile(
    r"^P(?P<person>\d+)"
    r"S(?P<session>\d+)"
    r"C(?P<config>\d+)"
    r"(?:F(?P<fresnel_a>\d+)-(?P<fresnel_b>\d+))?"
    r"Q(?P<take>\d+)"
    r"_(?P<suffix>labeled|csi)$"
)


def parse_inhouse_filename(filepath: Union[str, Path]) -> Dict[str, Any]:
    """파일명에서 통제변인 인덱스를 추출한다.

    Args:
        filepath: `P2S1C1F2-1Q2_labeled.csv` 형태의 경로 또는 파일명

    Returns:
        person, session, config, fresnel_a, fresnel_b, take, has_label,
        expected_label, is_fall_take 를 담은 dict.
        `_csi` 파일에는 F 토큰이 없으므로 fresnel_a/b 는 None.

    Raises:
        ValueError: 파일명이 규칙에 맞지 않는 경우
    """
    stem = Path(filepath).stem
    match = FILENAME_RE.match(stem)
    if match is None:
        raise ValueError(f"Filename does not match in-house pattern: {stem}")

    g = match.groupdict()
    take = int(g["take"])
    return {
        "person": int(g["person"]),
        "session": int(g["session"]),
        "config": int(g["config"]),
        "fresnel_a": int(g["fresnel_a"]) if g["fresnel_a"] is not None else None,
        "fresnel_b": int(g["fresnel_b"]) if g["fresnel_b"] is not None else None,
        "take": take,
        "has_label": g["suffix"] == "labeled",
        "expected_label": TAKE_LABEL_MAP.get(take),
        "is_fall_take": take in TAKE_LABEL_MAP,
    }


def parse_csi_column(csi_strings: Sequence[str]) -> np.ndarray:
    """`csi` 컬럼의 JSON 문자열 배열을 (N, 490) int16 으로 파싱한다.

    Args:
        csi_strings: `"[26,-30,27,...]"` 형태의 문자열 시퀀스

    Returns:
        (N, CSI_LEN) int16 배열. 원본은 signed int8 이지만 이후 연산 편의를
        위해 int16 으로 승격한다.

    Raises:
        ValueError: 길이가 CSI_LEN 이 아닌 행이 있는 경우
    """
    rows = [json.loads(s) for s in csi_strings]
    lengths = {len(r) for r in rows}
    if lengths != {CSI_LEN}:
        raise ValueError(f"Expected all csi arrays to have {CSI_LEN} values, got lengths {sorted(lengths)}")
    return np.asarray(rows, dtype=np.int16)


def compute_amplitude(csi_flat: np.ndarray) -> np.ndarray:
    """(N, 490) 인터리브 버퍼에서 슬롯별 진폭 (N, 245) 를 계산한다.

    ESP-IDF 의 CSI 버퍼는 관례적으로 [imag, real] 순서로 인터리브되지만,
    진폭 sqrt(a^2 + b^2) 은 두 값의 순서와 무관하므로 여기서는 문제되지 않는다.
    (위상을 쓰려면 펌웨어에서 순서를 먼저 확인해야 한다.)

    Args:
        csi_flat: (N, CSI_LEN) 정수 배열

    Returns:
        (N, N_SLOTS) float32 진폭
    """
    iq = csi_flat.reshape(csi_flat.shape[0], N_SLOTS, 2).astype(np.float32)
    return np.hypot(iq[:, :, 0], iq[:, :, 1])


def usable_subcarriers(drop_slots: Sequence[int] = DROP_SLOTS) -> np.ndarray:
    """채널 정보가 있는 서브캐리어 슬롯 인덱스를 반환한다.

    Args:
        drop_slots: 제외할 슬롯 인덱스 (기본: DC 널 + 상수 슬롯)

    Returns:
        오름차순 정렬된 (N_SLOTS - len(drop_slots),) int32 인덱스 배열
    """
    mask = np.ones(N_SLOTS, dtype=bool)
    mask[list(drop_slots)] = False
    return np.flatnonzero(mask).astype(np.int32)


def _unwrap_dev_timestamp(dev_us: np.ndarray) -> np.ndarray:
    """uint32 마이크로초 카운터의 wraparound 를 펴서 단조 증가시킨다."""
    dev = dev_us.astype(np.int64)
    if dev.size < 2:
        return dev
    wraps = np.concatenate([[0], (np.diff(dev) < 0).astype(np.int64)]).cumsum()
    return dev + wraps * (1 << 32)


def load_inhouse_file(
    filepath: Union[str, Path],
    drop_slots: Sequence[int] = DROP_SLOTS,
) -> Dict[str, Any]:
    """자체 수집 CSI CSV 한 개를 로드한다.

    Args:
        filepath: `data/raw/*.csv` 경로
        drop_slots: 서브캐리어로 취급하지 않을 슬롯 인덱스

    Returns:
        dict:
            amplitude      : (N, N_SLOTS) float32 - 전체 슬롯 진폭
            usable_idx     : (243,) int32 - 유효 서브캐리어 슬롯 인덱스
            time_sec       : (N,) float64 - 첫 패킷 기준 경과 시간(초)
            dev_timestamp  : (N,) int64 - wrap 보정된 디바이스 클럭(us)
            labels         : (N,) '<U8' - 행별 라벨 (`_csi` 파일은 전부 "none")
            is_fall_row    : (N,) bool - 라벨이 낙상 라벨인 행
            file_label     : str - 파일에 등장하는 대표 활동 라벨 ("none" 이면 비낙상)
            n_label_rows   : int - 활동 라벨이 붙은 행 수
            label_q_mismatch : bool - 파일 라벨이 Q 인덱스에서 기대되는 라벨과 다른지
            fs_hz_eff      : float - 실효 샘플링 레이트
            + parse_inhouse_filename() 의 모든 키, source_file

    Raises:
        ValueError: 파일명/CSI 배열 길이가 규칙에 맞지 않는 경우
    """
    filepath = Path(filepath)
    meta = parse_inhouse_filename(filepath)

    # "none" 은 결측이 아니라 실제 라벨 값이므로 NA 변환을 막는다.
    df = pd.read_csv(filepath, keep_default_na=False, na_values=[])

    csi_flat = parse_csi_column(df["csi"].to_numpy())
    amplitude = compute_amplitude(csi_flat)

    dev_timestamp = _unwrap_dev_timestamp(df["dev_timestamp"].to_numpy())
    time_sec = (dev_timestamp - dev_timestamp[0]) / 1e6

    if "label" in df.columns:
        labels = df["label"].astype(str).to_numpy().astype("<U8")
    else:
        labels = np.full(len(df), NO_ACTIVITY_LABEL, dtype="<U8")

    is_fall_row = np.isin(labels, FALL_LABELS)
    activity_labels = np.unique(labels[is_fall_row])
    file_label = str(activity_labels[0]) if activity_labels.size else NO_ACTIVITY_LABEL

    duration = float(time_sec[-1]) if len(time_sec) > 1 else 0.0
    fs_hz_eff = (len(time_sec) - 1) / duration if duration > 0 else float("nan")

    expected = meta["expected_label"]
    label_q_mismatch = bool(expected is not None and file_label != expected)

    return {
        **meta,
        "source_file": str(filepath),
        "amplitude": amplitude,
        "usable_idx": usable_subcarriers(drop_slots),
        "time_sec": time_sec,
        "dev_timestamp": dev_timestamp,
        "labels": labels,
        "is_fall_row": is_fall_row,
        "file_label": file_label,
        "activity_labels": [str(x) for x in activity_labels],
        "n_label_rows": int(is_fall_row.sum()),
        "label_q_mismatch": label_q_mismatch,
        "n_packets": int(len(df)),
        "duration_sec": duration,
        "fs_hz_eff": fs_hz_eff,
    }


def get_inhouse_file_index(raw_root: Union[str, Path] = "data/raw") -> pd.DataFrame:
    """CSI 데이터를 로드하지 않고 `data/raw/` 의 파일 인덱스를 만든다.

    Args:
        raw_root: 자체 수집 CSV 가 있는 디렉토리

    Returns:
        filepath, person, session, config, fresnel_a, fresnel_b, take,
        has_label, expected_label, is_fall_take 컬럼의 DataFrame.
        filepath 기준 정렬.

    Raises:
        FileNotFoundError: 디렉토리가 없거나 규칙에 맞는 CSV 가 하나도 없는 경우
    """
    raw_root = Path(raw_root)
    if not raw_root.is_dir():
        raise FileNotFoundError(f"Raw data directory not found: {raw_root}")

    rows: List[Dict[str, Any]] = []
    for csv_path in sorted(raw_root.glob("*.csv")):
        try:
            meta = parse_inhouse_filename(csv_path)
        except ValueError:
            continue  # 규칙에 맞지 않는 파일은 조용히 건너뛴다
        rows.append({"filepath": csv_path, **meta})

    if not rows:
        raise FileNotFoundError(f"No in-house CSI files matching the naming pattern in {raw_root}")

    return pd.DataFrame(rows).sort_values("filepath").reset_index(drop=True)
