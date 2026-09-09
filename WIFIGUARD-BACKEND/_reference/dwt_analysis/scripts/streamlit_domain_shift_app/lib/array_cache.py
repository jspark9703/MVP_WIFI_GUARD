"""
CWT/ACF 배열 캐시. streamlit 을 import 하지 않는다.

기존 `disk_cache.py` 는 스칼라 행을 parquet 에 쌓지만 여기 산출물은 샘플당 114 KB
(S3 224×224 + ACF 128×64, float16)라 행 저장이 불가능하다. 그래서 같은
`(대상, params_hash)` 키 규약은 유지하되 **샘플당 `.npz` 파일 + 인덱스 parquet** 으로 간다.

- 증분: 표본을 늘려도 이미 계산한 샘플은 다시 계산하지 않는다
- 재개: 배치가 중간에 끊겨도 이미 쓴 샘플은 남는다
- `params_hash` 가 바뀌면 **다른 디렉터리**가 생긴다 (조용한 재사용은 정확성 버그)

`.gitignore` 의 `/results/analysis/domain_shift/cache/` 규칙이 이 디렉터리를 덮는다.
"""

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np
import pandas as pd


def sample_key(filepath: str, rx: int) -> str:
    """`(파일, rx)` -> 캐시 파일명에 쓸 안정적인 키."""
    stem = Path(str(filepath)).stem
    return f"{stem}_rx{int(rx)}"


def cache_root(cache_dir: str, hash8: str) -> Path:
    return Path(cache_dir) / f"cwtacf_{hash8}"


def index_path(cache_dir: str, hash8: str) -> Path:
    return cache_root(cache_dir, hash8) / "index.parquet"


def sample_path(cache_dir: str, hash8: str, key: str) -> Path:
    return cache_root(cache_dir, hash8) / f"{key}.npz"


def save_sample(cache_dir: str, hash8: str, key: str,
                s3: np.ndarray, acf: np.ndarray) -> None:
    """float16 으로 저장한다. S3 는 [0,1], ACF 는 [-1,1] 이라 정밀도 손실이 무의미하다."""
    root = cache_root(cache_dir, hash8)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{key}.npz.tmp"
    # 경로 문자열을 넘기면 numpy 가 ".npz" 로 끝나지 않는 이름에 확장자를 덧붙여
    # os.replace 가 없는 파일을 찾게 된다. 파일 핸들로 넘기면 이름을 그대로 쓴다.
    with open(tmp, "wb") as fh:
        np.savez_compressed(fh, s3=s3.astype(np.float16), acf=acf.astype(np.float16))
    os.replace(tmp, root / f"{key}.npz")


def load_sample(cache_dir: str, hash8: str, key: str) -> Tuple[np.ndarray, np.ndarray]:
    with np.load(sample_path(cache_dir, hash8, key)) as z:
        return z["s3"], z["acf"]


def missing_keys(cache_dir: str, hash8: str, keys: Sequence[str]) -> List[str]:
    root = cache_root(cache_dir, hash8)
    if not root.exists():
        return list(keys)
    return [k for k in keys if not (root / f"{k}.npz").exists()]


def load_index(cache_dir: str, hash8: str) -> pd.DataFrame:
    path = index_path(cache_dir, hash8)
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - 손상된 인덱스는 미스로 취급
        return pd.DataFrame()


def append_index(cache_dir: str, hash8: str, rows: pd.DataFrame,
                 key_col: str = "sample_key") -> None:
    """배치당 1회만 호출. tmp 에 쓰고 os.replace 로 원자적 교체."""
    if rows is None or rows.empty:
        return

    path = index_path(cache_dir, hash8)
    path.parent.mkdir(parents=True, exist_ok=True)

    merged = rows
    if path.exists():
        try:
            existing = pd.read_parquet(path)
            merged = pd.concat([existing, rows], ignore_index=True)
        except Exception:  # noqa: BLE001
            merged = rows
    if key_col in merged.columns:
        merged = merged.drop_duplicates(subset=key_col, keep="last")

    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def load_arrays(cache_dir: str, hash8: str, keys: Sequence[str]) -> Tuple[np.ndarray, np.ndarray]:
    """키 순서대로 (n,224,224), (n,128,64) 를 쌓아 돌려준다 (float16 유지)."""
    s3_list, acf_list = [], []
    for k in keys:
        s3, acf = load_sample(cache_dir, hash8, k)
        s3_list.append(s3)
        acf_list.append(acf)
    if not s3_list:
        return (np.zeros((0, 224, 224), dtype=np.float16),
                np.zeros((0, 128, 64), dtype=np.float16))
    return np.stack(s3_list), np.stack(acf_list)


def cache_status(cache_dir: str, hash8: str, keys: Sequence[str]) -> Dict[str, Any]:
    miss = missing_keys(cache_dir, hash8, keys)
    root = cache_root(cache_dir, hash8)
    size_mb = 0.0
    if root.exists():
        size_mb = sum(p.stat().st_size for p in root.glob("*.npz")) / 1024 / 1024
    return {"params_hash": hash8, "total": len(keys),
            "cached": len(keys) - len(miss), "missing": len(miss),
            "size_mb": round(size_mb, 1), "path": str(root)}


def list_cached_runs(cache_dir: str) -> pd.DataFrame:
    """디스크에 있는 CWT/ACF 캐시 목록 — 무엇이 이미 공짜인지 보여주는 용도."""
    d = Path(cache_dir)
    if not d.exists():
        return pd.DataFrame(columns=["params_hash", "samples", "size_mb", "path"])
    rows = []
    for sub in sorted(d.glob("cwtacf_*")):
        if not sub.is_dir():
            continue
        files = list(sub.glob("*.npz"))
        rows.append({
            "params_hash": sub.name.replace("cwtacf_", ""),
            "samples": len(files),
            "size_mb": round(sum(p.stat().st_size for p in files) / 1024 / 1024, 1),
            "path": str(sub),
        })
    return pd.DataFrame(rows)
