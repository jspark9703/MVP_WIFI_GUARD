"""
파일 단위 행(row) parquet 캐시. streamlit 을 import 하지 않는다.

왜 ``@st.cache_data(persist="disk")`` 를 쓰지 않는가:
그 데코레이터는 **함수 인자**로 키를 만든다. 배치 러너의 인자는 파일 목록이므로
표본 크기를 25 -> 40 으로 올리면 키가 통째로 바뀌어 이미 계산한 25개를 전부 버린다.
표본 크기 슬라이더가 주 조작 수단인 UI 에서 이건 최악의 실패 양상이다.

대신 여기서는 (filepath, params_hash) 를 키로 하는 파일 단위 행을 parquet 에 쌓는다.
25 -> 40 은 새로 15개만 계산한다. 덤으로 parquet 은 검사 가능한 산출물이고
Streamlit 재시작에도 살아남는다.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pandas as pd

CACHE_KINDS: Tuple[str, ...] = ("meta", "record", "q", "sub", "pc")


def params_hash(params: Dict[str, Any]) -> str:
    """파라미터 dict -> 8자 해시. 값이 바뀌면 반드시 새 캐시 파일이 생겨야 한다."""
    blob = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]


def cache_path(cache_dir: str, kind: str, hash8: str) -> Path:
    if kind not in CACHE_KINDS:
        raise ValueError(f"kind must be one of {CACHE_KINDS}, got {kind!r}")
    return Path(cache_dir) / f"{kind}_{hash8}.parquet"


def load_cached_rows(
    path: Path,
    filepaths: Sequence[str],
    key_col: str = "filepath",
) -> Tuple[pd.DataFrame, List[str]]:
    """
    요청된 파일 중 이미 캐시된 행을 읽고, 아직 없는 파일 목록을 돌려준다.

    Returns:
        (캐시에 있던 행, 계산해야 할 filepath 목록)
    """
    wanted = [str(f) for f in filepaths]
    if not Path(path).exists():
        return pd.DataFrame(), wanted

    try:
        cached = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - 손상된 캐시는 미스로 취급하고 다시 만든다
        return pd.DataFrame(), wanted

    if cached.empty or key_col not in cached.columns:
        return pd.DataFrame(), wanted

    have = set(cached[key_col].astype(str))
    hits = cached[cached[key_col].astype(str).isin(wanted)]
    missing = [f for f in wanted if f not in have]
    return hits, missing


def append_cached_rows(path: Path, new_rows: pd.DataFrame, key_col: str = "filepath") -> None:
    """
    새 행을 캐시에 병합한다. 배치당 1회만 호출한다(파일당 아님).
    tmp 에 쓰고 os.replace 로 교체 — 동일 볼륨에서 원자적이라 중단돼도 캐시가 깨지지 않는다.
    """
    if new_rows is None or new_rows.empty:
        return

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            existing = pd.read_parquet(path)
            merged = pd.concat([existing, new_rows], ignore_index=True)
        except Exception:  # noqa: BLE001
            merged = new_rows
    else:
        merged = new_rows

    if key_col in merged.columns:
        merged = merged.drop_duplicates(subset=key_col, keep="last")

    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def append_cached_long(path: Path, new_rows: pd.DataFrame, key_col: str = "filepath") -> None:
    """
    long 프레임(파일당 여러 행)용 병합. 파일 단위로 중복을 제거한다 —
    행 단위 drop_duplicates 는 서로 다른 파일의 동일 (antenna, subcarrier) 행을
    잘못 지울 수 있다.
    """
    if new_rows is None or new_rows.empty:
        return

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    merged = new_rows
    if path.exists():
        try:
            existing = pd.read_parquet(path)
            if key_col in existing.columns and key_col in new_rows.columns:
                incoming = set(new_rows[key_col].astype(str))
                existing = existing[~existing[key_col].astype(str).isin(incoming)]
            merged = pd.concat([existing, new_rows], ignore_index=True)
        except Exception:  # noqa: BLE001
            merged = new_rows

    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def load_long_rows(path: Path, filepaths: Sequence[str], key_col: str = "filepath") -> pd.DataFrame:
    """long 캐시에서 요청된 파일들의 행만 읽는다."""
    if not Path(path).exists():
        return pd.DataFrame()
    try:
        cached = pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    if cached.empty or key_col not in cached.columns:
        return pd.DataFrame()
    wanted = {str(f) for f in filepaths}
    return cached[cached[key_col].astype(str).isin(wanted)]


def write_params_json(cache_dir: str, hash8: str, params: Dict[str, Any]) -> Path:
    """해시가 무엇을 뜻하는지 사람이 읽을 수 있게 남긴다(캐시와 달리 git 추적 대상)."""
    out_dir = Path(cache_dir).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"params_{hash8}.json"
    path.write_text(json.dumps(params, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def list_cached_hashes(cache_dir: str) -> pd.DataFrame:
    """현재 디스크에 있는 캐시 목록 — P1 에서 '뭐가 이미 공짜인지' 보여주는 데 쓴다."""
    d = Path(cache_dir)
    if not d.exists():
        return pd.DataFrame(columns=["kind", "params_hash", "rows", "size_kb", "path"])

    rows = []
    for p in sorted(d.glob("*.parquet")):
        stem = p.stem
        if "_" not in stem:
            continue
        kind, hash8 = stem.split("_", 1)
        try:
            n = len(pd.read_parquet(p, columns=["filepath"]))
        except Exception:  # noqa: BLE001
            n = -1
        rows.append({"kind": kind, "params_hash": hash8, "rows": n,
                     "size_kb": round(p.stat().st_size / 1024, 1), "path": str(p)})
    return pd.DataFrame(rows)
