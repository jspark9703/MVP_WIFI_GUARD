"""
층화 표본 추출. streamlit 을 import 하지 않는다 (순수 함수).

기존 src/dwt_coef/selection_stats.sample_sessions 는 (env, class_id) 셀에서
'세션'(= 여러 활동 phase 파일의 묶음)을 뽑는다. 이 앱은 활동 축으로 비교하므로
'파일' 단위로 뽑아야 하고, 셀 정의도 fall/non-fall 을 포함해야 해서 별도로 둔다.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.dwt_coef.session_builder import ACTIVITY_NAME_MAP

from lib.constants import ENV_LABELS, FALL_LABELS

STRATA_CHOICES: Dict[str, Tuple[str, ...]] = {
    "env × fall/non-fall": ("env", "fall_label"),
    "env × activity": ("env", "activity"),
    "env only": ("env",),
}


def add_group_columns(file_index: pd.DataFrame) -> pd.DataFrame:
    """파일 인덱스에 표시·그룹화용 컬럼을 덧붙인다 (원본 비파괴)."""
    df = file_index.copy()
    df["filepath_str"] = df["filepath"].astype(str)
    df["name"] = df["filepath"].map(lambda p: str(p).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0])
    df["env_label"] = df["env"].map(lambda e: ENV_LABELS.get(int(e), f"E{e}"))
    df["fall_label"] = df["is_fall"].map(lambda f: FALL_LABELS[bool(f)])
    df["activity_name"] = df["activity"].map(lambda a: ACTIVITY_NAME_MAP.get(int(a), f"A{a}"))
    df["activity_label"] = df.apply(
        lambda r: f"A{int(r['activity']):02d} {r['activity_name']}", axis=1
    )
    return df


def _draw_balanced(cell: pd.DataFrame, n: int, rng: np.random.Generator) -> pd.DataFrame:
    """
    피험자를 라운드로빈으로 순회하며 뽑아, 한 셀 안에서 특정 인물이 표본을
    독점하지 않게 한다. env 와 subject 가 교락된 데이터셋에서 이건 위생 문제가
    아니라 정확성 문제다 — 한 사람이 셀을 지배하면 '환경 효과'가 사실상
    '그 사람 효과'가 된다.
    """
    if len(cell) <= n:
        return cell

    per_subject: List[List[int]] = []
    for _, grp in cell.groupby("subject", sort=True):
        idx = grp.index.to_numpy()
        rng.shuffle(idx)
        per_subject.append(list(idx))

    order = np.arange(len(per_subject))
    rng.shuffle(order)
    per_subject = [per_subject[i] for i in order]

    picked: List[int] = []
    depth = 0
    while len(picked) < n:
        progressed = False
        for bucket in per_subject:
            if depth < len(bucket):
                picked.append(bucket[depth])
                progressed = True
                if len(picked) == n:
                    break
        if not progressed:
            break
        depth += 1

    return cell.loc[picked]


def stratified_sample(
    file_index: pd.DataFrame,
    envs: Sequence[int],
    activities: Optional[Sequence[int]] = None,
    n_per_cell: int = 25,
    seed: int = 0,
    strata: Tuple[str, ...] = ("env", "fall_label"),
    balance_subjects: bool = True,
) -> pd.DataFrame:
    """
    각 층(cell)에서 최대 ``n_per_cell`` 개 파일을 복원 없이 추출한다.

    Returns:
        file_index 의 부분집합 + ``cell`` 컬럼. 셀 순서/파일 순서는 seed 에 대해 결정적.
    """
    df = add_group_columns(file_index)
    df = df[df["env"].isin([int(e) for e in envs])]
    if activities:
        df = df[df["activity"].isin([int(a) for a in activities])]

    if df.empty:
        return df.assign(cell=pd.Series(dtype=str))

    missing = [c for c in strata if c not in df.columns]
    if missing:
        raise ValueError(f"unknown strata column(s): {missing}")

    rng = np.random.default_rng(seed)

    parts: List[pd.DataFrame] = []
    for _, cell in df.groupby(list(strata), sort=True):
        if balance_subjects:
            picked = _draw_balanced(cell, n_per_cell, rng)
        elif len(cell) > n_per_cell:
            idx = cell.index.to_numpy()
            rng.shuffle(idx)
            picked = cell.loc[idx[:n_per_cell]]
        else:
            picked = cell
        parts.append(picked)

    out = pd.concat(parts, axis=0)
    out["cell"] = out[list(strata)].astype(str).agg(" | ".join, axis=1)
    return out.sort_values(["env", "subject", "activity", "trial"]).reset_index(drop=True)


def sample_summary(sample: pd.DataFrame) -> pd.DataFrame:
    """셀별 표본 구성 요약 — 표본이 실제로 균형 잡혔는지 눈으로 확인하는 표."""
    if sample.empty:
        return pd.DataFrame(
            columns=["cell", "n_files", "n_subjects", "subjects", "n_activities", "max_subject_share"]
        )

    rows = []
    for cell, grp in sample.groupby("cell", sort=True):
        counts = grp["subject"].value_counts()
        rows.append({
            "cell": cell,
            "n_files": int(len(grp)),
            "n_subjects": int(grp["subject"].nunique()),
            "subjects": ", ".join(f"S{s:02d}" for s in sorted(grp["subject"].unique())),
            "n_activities": int(grp["activity"].nunique()),
            "max_subject_share": round(float(counts.max() / len(grp)), 3),
        })
    return pd.DataFrame(rows)


def confound_crosstab(file_index: pd.DataFrame) -> pd.DataFrame:
    """
    env × subject 교차표. 블록 대각 구조(E1=S1-10, E2=S11-20, E3=S21-30)를
    '주장'이 아니라 '증거'로 보여주기 위한 것.
    """
    return pd.crosstab(file_index["env"], file_index["subject"])


def composition_matched_reference(
    manifest: pd.DataFrame,
    sample: pd.DataFrame,
    value_col: str,
    group_col: str = "env",
    compose_col: str = "activity",
) -> pd.DataFrame:
    """
    표본과 **동일한 활동 구성**으로 가중한 전체 데이터셋 기준값.

    층화 표본은 낙상/비낙상을 50:50 으로 뽑지만 전체 데이터셋은 2:10 이다. 따라서
    표본 평균을 전체 평균과 그냥 비교하면 층화가 의도한 구성 차이를 '샘플러 편향'으로
    오독하게 된다. 여기서는 표본의 활동 비중 w_a 를 전체 데이터셋의 (그룹, 활동) 평균에
    적용해 공정한 기준값을 만든다.

    Returns:
        columns: group, sample_mean, reference_mean, diff, n_sample, n_reference
    """
    need = {group_col, compose_col, value_col}
    if manifest.empty or not need <= set(manifest.columns) or sample.empty:
        return pd.DataFrame(columns=[group_col, "sample_mean", "reference_mean",
                                     "diff", "n_sample", "n_reference"])

    weights = sample[compose_col].value_counts(normalize=True)
    cell_means = manifest.groupby([group_col, compose_col])[value_col].mean()

    rows = []
    for g in sorted(sample[group_col].dropna().unique()):
        num = den = 0.0
        for act, w in weights.items():
            key = (g, act)
            if key in cell_means.index and np.isfinite(cell_means.loc[key]):
                num += w * float(cell_means.loc[key])
                den += w
        if den <= 0:
            continue
        ref = num / den
        smp_vals = sample.loc[sample[group_col] == g, value_col]
        smp_mean = float(smp_vals.mean()) if len(smp_vals) else float("nan")
        rows.append({
            group_col: g, "sample_mean": smp_mean, "reference_mean": ref,
            "diff": abs(smp_mean - ref), "n_sample": int(len(smp_vals)),
            "n_reference": int((manifest[group_col] == g).sum()),
        })
    return pd.DataFrame(rows)


def sample_cost_estimate(n_files: int, sec_per_file: float) -> str:
    total = n_files * sec_per_file
    if total < 90:
        return f"{n_files}개 파일 × {sec_per_file:.3f}s ≈ **{total:.0f}초** (콜드 캐시 기준, 캐시 적중 시 즉시)"
    return f"{n_files}개 파일 × {sec_per_file:.3f}s ≈ **{total / 60:.1f}분** (콜드 캐시 기준, 캐시 적중 시 즉시)"
