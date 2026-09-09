#!/usr/bin/env python3
"""
CSI Data Augmentation Pipeline (Band-aware Subcarrier Grouping)

자체 수집 ESP32-C5 CSI 레코딩(data/raw/*.csv) 하나를 여러 개의 서브캐리어
부분집합 "패치"로 쪼개어 학습 샘플 수를 늘린다 (AmFall 데이터 증강).

  1. data/raw/ 파일 인덱스 생성 + 필터링
  2. 파일별 CSI 로드 -> (N, 245) 진폭, 유효 서브캐리어 243개 선별
  3. 243개를 3개 대역(low/mid/high, 81개씩)으로 분할
  4. 각 대역에서 30개씩 무작위 추출 -> 패치당 90개 서브캐리어, 파일당 15개 패치
  5. 패치별 npz 저장 + manifest.csv

리샘플/필터/PCA 는 하지 않는다. 논문이 강조한 "단순 리쉐이핑" 수준의 증강이므로
전처리는 다운스트림 단계에 맡기고, 여기서는 원시 진폭과 시간축만 저장한다.

Usage:
    python scripts/08_augmentation.py --config config/augmentation.yaml
    python scripts/08_augmentation.py --config config/augmentation.yaml --max-files 2
"""

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yaml
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dwt_coef.augmentation import generate_patches, make_file_rng
from src.dwt_coef.inhouse_loader import (
    N_SLOTS,
    get_inhouse_file_index,
    load_inhouse_file,
    usable_subcarriers,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CSI augmentation pipeline (band-aware subcarrier grouping)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/augmentation.yaml"),
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Limit number of source recordings to process (override config)",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> Dict[str, Any]:
    """Load YAML config file."""
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config


def output_stem(filepath: Path) -> str:
    """`P2S1C1F2-1Q2_labeled.csv` -> `P2S1C1F2-1Q2` (suffix 제거)."""
    stem = filepath.stem
    for suffix in ("_labeled", "_csi"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def build_manifest_row(
    source_file: str,
    patch_id: Optional[int] = None,
    output_path: str = "",
    meta: Optional[Dict[str, Any]] = None,
    status: str = "ok",
    error_msg: str = "",
) -> Dict[str, Any]:
    """manifest.csv 한 행을 만든다 (실패한 파일도 같은 스키마로 기록)."""
    meta = meta or {}
    return {
        "source_file": source_file,
        "patch_id": patch_id if patch_id is not None else "",
        "output_path": output_path,
        "person": meta.get("person", ""),
        "session": meta.get("session", ""),
        "config": meta.get("config", ""),
        "fresnel_a": meta.get("fresnel_a") if meta.get("fresnel_a") is not None else "",
        "fresnel_b": meta.get("fresnel_b") if meta.get("fresnel_b") is not None else "",
        "take": meta.get("take", ""),
        "has_label": meta.get("has_label", ""),
        "file_label": meta.get("file_label", ""),
        "expected_label": meta.get("expected_label") or "",
        "label_q_mismatch": meta.get("label_q_mismatch", ""),
        "n_packets": meta.get("n_packets", ""),
        "duration_sec": round(meta["duration_sec"], 3) if "duration_sec" in meta else "",
        "fs_hz_eff": round(meta["fs_hz_eff"], 2) if "fs_hz_eff" in meta else "",
        "n_label_rows": meta.get("n_label_rows", ""),
        "n_subcarriers": meta.get("n_subcarriers", ""),
        "status": status,
        "error_msg": error_msg,
    }


def process_file(
    filepath: Path,
    file_ordinal: int,
    config: Dict[str, Any],
    output_dir: Path,
) -> List[Dict[str, Any]]:
    """레코딩 1개를 로드해 패치 n_patches 개를 저장하고 manifest 행들을 반환한다.

    예외는 삼켜서 에러 manifest 행으로 되돌린다 (한 파일 실패가 전체를 중단시키지 않음).
    """
    aug_cfg = config["augment"]
    run_cfg = config.get("run", {})

    try:
        drop_slots = tuple(aug_cfg.get("drop_slots", (121, 122)))
        n_patches = aug_cfg["n_patches"]
        n_bands = aug_cfg["n_bands"]
        per_band = aug_cfg["per_band"]
        seed = aug_cfg.get("seed", 0)

        stem = output_stem(filepath)

        if run_cfg.get("skip_existing", False):
            expected = [output_dir / f"{stem}_aug{k:02d}.npz" for k in range(n_patches)]
            if all(p.exists() for p in expected):
                return [
                    build_manifest_row(str(filepath), status="skipped", error_msg="already exists")
                ]

        data = load_inhouse_file(filepath, drop_slots=drop_slots)

        patches = generate_patches(
            amplitude=data["amplitude"],
            usable_idx=data["usable_idx"],
            n_patches=n_patches,
            n_bands=n_bands,
            per_band=per_band,
            rng=make_file_rng(seed, file_ordinal),
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        rows: List[Dict[str, Any]] = []

        for patch in patches:
            out_path = output_dir / f"{stem}_aug{patch['patch_id']:02d}.npz"
            np.savez_compressed(
                out_path,
                amplitude=patch["amplitude"],
                subcarrier_idx=patch["subcarrier_idx"],
                band_id=patch["band_id"],
                time_sec=data["time_sec"],
                labels=data["labels"],
                is_fall_row=data["is_fall_row"],
                patch_id=np.asarray(patch["patch_id"], dtype=np.int32),
                person=np.asarray(data["person"], dtype=np.int32),
                session=np.asarray(data["session"], dtype=np.int32),
                config_c=np.asarray(data["config"], dtype=np.int32),
                fresnel_a=np.asarray(
                    data["fresnel_a"] if data["fresnel_a"] is not None else -1, dtype=np.int32
                ),
                fresnel_b=np.asarray(
                    data["fresnel_b"] if data["fresnel_b"] is not None else -1, dtype=np.int32
                ),
                take=np.asarray(data["take"], dtype=np.int32),
                has_label=np.asarray(data["has_label"], dtype=np.bool_),
                is_fall_take=np.asarray(data["is_fall_take"], dtype=np.bool_),
                file_label=np.asarray(data["file_label"]),
                fs_hz_eff=np.asarray(data["fs_hz_eff"], dtype=np.float64),
                source_file=np.asarray(filepath.name),
            )
            meta = {**data, "n_subcarriers": patch["amplitude"].shape[1]}
            rows.append(
                build_manifest_row(
                    source_file=str(filepath),
                    patch_id=patch["patch_id"],
                    output_path=str(out_path),
                    meta=meta,
                )
            )

        return rows

    except Exception as e:
        return [build_manifest_row(str(filepath), status="error", error_msg=str(e)[:200])]


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    data_cfg = config["data"]
    aug_cfg = config["augment"]
    output_dir = Path(config["output"]["path"])

    print("=" * 70)
    print("CSI AUGMENTATION PIPELINE (Band-aware Subcarrier Grouping)")
    print("=" * 70)

    print("\n[1/4] Building in-house file index...")
    file_index = get_inhouse_file_index(data_cfg["raw_root"])
    # file_ordinal 은 필터/--max-files 와 무관하게 고정되도록 전체 인덱스에서 먼저 부여한다.
    file_index["file_ordinal"] = np.arange(len(file_index))
    print(f"  Total recordings found: {len(file_index)}")

    df = file_index
    if data_cfg.get("persons"):
        df = df[df["person"].isin(data_cfg["persons"])]
    if data_cfg.get("takes"):
        df = df[df["take"].isin(data_cfg["takes"])]
    if data_cfg.get("labeled_only", False):
        df = df[df["has_label"]]

    max_files = args.max_files or config.get("run", {}).get("max_files")
    if max_files:
        df = df.head(max_files)

    n_patches = aug_cfg["n_patches"]
    n_sub = aug_cfg["n_bands"] * aug_cfg["per_band"]
    n_usable = len(usable_subcarriers(tuple(aug_cfg.get("drop_slots", (121, 122)))))

    print(f"  After filtering: {len(df)} recordings")
    print(f"  Subcarrier slots: {N_SLOTS} -> usable {n_usable} (dropped {aug_cfg.get('drop_slots')})")
    print(
        f"  Bands: {aug_cfg['n_bands']} x {n_usable // aug_cfg['n_bands']}, "
        f"per band {aug_cfg['per_band']} -> {n_sub} subcarriers/patch"
    )
    print(f"  Patches per recording: {n_patches}  ->  expected outputs: {len(df) * n_patches}")

    if len(df) == 0:
        raise RuntimeError("No recordings left after filtering - check config/augmentation.yaml")

    print(f"\n[2/4] Generating patches -> {output_dir}...")
    start_time = time.monotonic()
    manifest_rows: List[Dict[str, Any]] = []

    for row in tqdm(df.itertuples(index=False), total=len(df), desc="Augmenting", unit="file"):
        rows = process_file(Path(row.filepath), int(row.file_ordinal), config, output_dir)
        for r in rows:
            if r["status"] == "error":
                tqdm.write(f"  [ERROR] {Path(r['source_file']).name}: {r['error_msg']}")
        manifest_rows.extend(rows)

    elapsed = time.monotonic() - start_time

    print("\n[3/4] Writing manifest...")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"
    fieldnames = [
        "source_file",
        "patch_id",
        "output_path",
        "person",
        "session",
        "config",
        "fresnel_a",
        "fresnel_b",
        "take",
        "has_label",
        "file_label",
        "expected_label",
        "label_q_mismatch",
        "n_packets",
        "duration_sec",
        "fs_hz_eff",
        "n_label_rows",
        "n_subcarriers",
        "status",
        "error_msg",
    ]
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in manifest_rows:
            writer.writerow(r)
    print(f"  Saved: {manifest_path}")

    print("\n[4/4] Summary")
    manifest_df = pd.DataFrame(manifest_rows)
    n_ok = int((manifest_df["status"] == "ok").sum())
    n_err = int((manifest_df["status"] == "error").sum())
    n_skip = int((manifest_df["status"] == "skipped").sum())

    print("=" * 70)
    print(f"  Source recordings : {len(df)}")
    print(f"  Patches written   : {n_ok}")
    print(f"  Skipped files     : {n_skip}")
    print(f"  Failed files      : {n_err}")
    print(f"  Elapsed           : {elapsed:.1f}s ({len(df) / elapsed:.2f} files/s)")
    print(f"  Output directory  : {output_dir.resolve()}")

    ok_rows = manifest_df[manifest_df["status"] == "ok"]
    if not ok_rows.empty:
        print("\n  Patches per label:")
        for label, count in ok_rows["file_label"].value_counts().items():
            print(f"    {label:<6} {count}")

        mismatched = ok_rows[ok_rows["label_q_mismatch"] == True]  # noqa: E712
        if not mismatched.empty:
            print("\n  [WARN] Label does not match the Q permutation (collection/labelling anomaly):")
            for src in sorted(set(mismatched["source_file"])):
                sub = mismatched[mismatched["source_file"] == src].iloc[0]
                print(f"    {Path(src).name}: label={sub['file_label']} expected={sub['expected_label']}")

    if n_err:
        print("\n  Errors:")
        for r in manifest_rows:
            if r["status"] == "error":
                print(f"    {Path(r['source_file']).name}: {r['error_msg']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
