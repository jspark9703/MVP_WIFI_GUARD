"""
교차 도메인 낙상 분류 프로토콜. streamlit 을 import 하지 않는다.

## 왜 이렇게까지 통제하는가 (실측 근거)

순진한 LOEO — "환경 내 CV" vs "나머지 두 환경으로 학습" — 를 108샘플로 돌리면 격차가
**음수**(CWT S3 −0.102)로 나온다. in-domain 은 환경 1개(36샘플), cross 는 환경 2개
(72샘플)에서 학습하기 때문이다. 즉 그 수치는 방 효과가 아니라 **학습셋 크기**를 잰 것이다.
학습 크기를 맞추고 테스트 폴드를 고정하자 −0.031 로 붕괴했다.

크기만 맞춰도 아직 두 가지가 다르다: 학습 **피험자 수**와 **학습에 쓰인 방의 개수**
(1개 vs 2개 — 다중 소스 학습 자체가 도메인 일반화 기법이라 in-domain 을 불리하게 만든다).
그래서 이 모듈은 **모든 arm 의 학습 피험자 수를 동일하게(기본 5명)** 맞춘다.

## arm 구성 (대상 환경 E, 반복 r)

E 의 피험자 10명을 TEST_r(5명) / POOL_r(5명) 으로 나눈다. **TEST_r 은 모든 arm 공통.**

- **IN (플라시보)**: POOL_r 5명 — 같은 방, 다른 사람
- **CROSS**: 다른 두 환경에서 5명 — 다른 방, 다른 사람  ← 헤드라인
- **CROSS-E'**: 특정 한 환경에서 5명 — 비대칭성
- **LOEO-full**: 다른 두 환경 전체 — 통제 없는 참고 수치

IN 은 단순 baseline 이 아니라 **플라시보**다. 테스트셋·학습 피험자 수·활동 구성이 CROSS 와
같고 학습한 사람들이 녹화된 방만 다르다. 따라서 `gap = IN − CROSS` 는 동일 테스트셋 위의
**대응(paired) 차이**이고, IN 의 분포가 곧 "방은 상관없다"는 귀무분포가 된다.

## 그룹핑

CV 그룹은 **피험자**다. `(env, subject, class_id, trial)` 를 공유하는 파일들은 하나의
물리 세션의 연속 phase 라(session_builder.CLASS_ACTIVITY_ORDER) 같은 세션의 A02 낙상과
A03 눕기는 정적 다중경로를 공유한다 — 파일 단위 그룹핑으로는 막히지 않는다.
피험자 그룹은 rx·세션·파일을 모두 포섭한다.

예측 단위는 **파일**(그 파일의 rx 행 예측확률 평균). rx 3개는 행을 3배로 늘리지만
유효 표본은 늘리지 않는다.
"""

import hashlib
import json
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.metrics import balanced_accuracy_score, roc_auc_score

ARM_ORDER: Tuple[str, ...] = ("IN", "CROSS", "CROSS_E", "LOEO_full")


@dataclass
class Arm:
    name: str
    target_env: int
    replicate: int
    train_subjects: Tuple[int, ...]
    test_subjects: Tuple[int, ...]
    train_idx: np.ndarray
    test_idx: np.ndarray
    source_env: Optional[int] = None       # CROSS_E 에서만
    achieved: Optional[pd.DataFrame] = None


@dataclass
class ArmResult:
    arm: Arm
    unit_ids: np.ndarray
    y_true: np.ndarray
    y_score: np.ndarray
    subjects: np.ndarray
    activities: np.ndarray
    n_train: int
    n_train_subjects: int


# --- arm 구성 ----------------------------------------------------------------

def _cell_counts(rows: pd.DataFrame, idx: np.ndarray,
                 cells: Sequence[str]) -> pd.Series:
    return rows.iloc[idx].groupby(list(cells)).size()


def match_training_indices(
    rows: pd.DataFrame,
    candidate_subjects: Sequence[int],
    template: pd.Series,
    cells: Sequence[str],
    rng: np.random.Generator,
) -> Tuple[np.ndarray, pd.DataFrame]:
    """
    후보 피험자들의 행에서 `template` 과 같은 `(activity, is_fall)` 셀 구성을 뽑는다.

    **채우지 못한 셀은 조용히 넘기지 않고 보고한다** — 그 자체가 발견이다
    (예: 어떤 환경에 특정 활동이 부족하면 활동 격차를 방 격차로 오독하게 된다).
    """
    pool = rows[rows["subject"].isin(list(candidate_subjects))]
    picked: List[int] = []
    report = []

    for key, want in template.items():
        key_tuple = key if isinstance(key, tuple) else (key,)
        mask = np.ones(len(pool), dtype=bool)
        for col, val in zip(cells, key_tuple):
            mask &= (pool[col].to_numpy() == val)
        avail = pool.index.to_numpy()[mask]
        take = int(min(want, len(avail)))
        if take > 0:
            picked.extend(rng.choice(avail, size=take, replace=False).tolist())
        report.append({"cell": str(key), "wanted": int(want),
                       "available": int(len(avail)), "taken": take})

    achieved = pd.DataFrame(report)
    achieved["shortfall"] = achieved["wanted"] - achieved["taken"]
    return np.asarray(sorted(picked), dtype=int), achieved


def make_matched_arms(
    rows: pd.DataFrame,
    target_env: int,
    n_test_subjects: int = 5,
    n_train_subjects: int = 5,
    n_replicates: int = 20,
    cells: Sequence[str] = ("activity", "is_fall"),
    include_single_source: bool = True,
    include_loeo_full: bool = True,
    seed: int = 0,
) -> List[Arm]:
    """대상 환경 하나에 대해 모든 arm × 반복을 만든다. 인덱스는 `rows` 의 위치 인덱스."""
    rows = rows.reset_index(drop=True)
    pos = np.arange(len(rows))

    env_mask = rows["env"].to_numpy() == target_env
    env_subjects = np.unique(rows.loc[env_mask, "subject"].to_numpy())
    other_envs = sorted(set(rows["env"].unique()) - {target_env})

    if len(env_subjects) < n_test_subjects + n_train_subjects:
        return []

    arms: List[Arm] = []
    for r in range(n_replicates):
        rng = np.random.default_rng(seed * 1000 + target_env * 100 + r)
        shuffled = rng.permutation(env_subjects)
        test_subs = tuple(sorted(shuffled[:n_test_subjects].tolist()))
        pool_subs = tuple(sorted(shuffled[n_test_subjects:n_test_subjects + n_train_subjects].tolist()))

        test_idx = pos[env_mask & rows["subject"].isin(test_subs).to_numpy()]
        in_idx = pos[env_mask & rows["subject"].isin(pool_subs).to_numpy()]
        if test_idx.size == 0 or in_idx.size == 0:
            continue

        template = _cell_counts(rows, in_idx, cells)

        arms.append(Arm("IN", target_env, r, pool_subs, test_subs, in_idx, test_idx))

        # CROSS: 다른 두 환경에서 라운드로빈으로 n_train_subjects 명
        cross_subs: List[int] = []
        per_env = {e: list(rng.permutation(
            np.unique(rows.loc[rows["env"] == e, "subject"].to_numpy()))) for e in other_envs}
        while len(cross_subs) < n_train_subjects and any(per_env.values()):
            for e in other_envs:
                if per_env[e] and len(cross_subs) < n_train_subjects:
                    cross_subs.append(int(per_env[e].pop()))
        if len(cross_subs) == n_train_subjects:
            idx, achieved = match_training_indices(rows, cross_subs, template, cells, rng)
            if idx.size:
                arms.append(Arm("CROSS", target_env, r, tuple(sorted(cross_subs)),
                                test_subs, idx, test_idx, achieved=achieved))

        if include_single_source:
            for e in other_envs:
                subs = list(rng.permutation(
                    np.unique(rows.loc[rows["env"] == e, "subject"].to_numpy())))[:n_train_subjects]
                if len(subs) < n_train_subjects:
                    continue
                idx, achieved = match_training_indices(rows, subs, template, cells, rng)
                if idx.size:
                    arms.append(Arm(f"CROSS_E{e}", target_env, r,
                                    tuple(sorted(int(s) for s in subs)), test_subs,
                                    idx, test_idx, source_env=int(e), achieved=achieved))

        if include_loeo_full and r == 0:
            # 통제 없는 참고 수치 — 반복마다 같으므로 한 번만.
            full_idx = pos[~env_mask]
            all_test = pos[env_mask]
            arms.append(Arm("LOEO_full", target_env, 0,
                            tuple(sorted(np.unique(rows.loc[~env_mask, "subject"]).tolist())),
                            tuple(sorted(env_subjects.tolist())), full_idx, all_test))

    return arms


# --- 실행 / 채점 -------------------------------------------------------------

def run_arm(X: np.ndarray, y: np.ndarray, rows: pd.DataFrame, arm: Arm,
            pipeline_factory: Callable[[int, int], Any],
            predict_unit: str = "file") -> Optional[ArmResult]:
    """
    한 arm 을 학습·예측한다. 파이프라인은 폴드마다 새로 만들어(`clone`) 학습 폴드로만 적합된다.
    """
    tr, te = arm.train_idx, arm.test_idx
    if tr.size < 4 or te.size < 2 or np.unique(y[tr]).size < 2:
        return None

    pipe = clone(pipeline_factory(len(tr), X.shape[1]))
    pipe.fit(X[tr], y[tr])

    if hasattr(pipe, "predict_proba"):
        score = pipe.predict_proba(X[te])[:, 1]
    else:
        score = pipe.decision_function(X[te])

    sub = rows.iloc[te]
    if predict_unit == "file":
        # 같은 파일의 rx 행들을 평균해 파일 단위 예측으로 합친다 (배포 형태와 일치).
        df = pd.DataFrame({"unit": sub["filepath"].to_numpy(), "score": score,
                           "y": y[te], "subject": sub["subject"].to_numpy(),
                           "activity": sub["activity"].to_numpy()})
        agg = df.groupby("unit", sort=False).agg(
            score=("score", "mean"), y=("y", "first"),
            subject=("subject", "first"), activity=("activity", "first")).reset_index()
        unit_ids = agg["unit"].to_numpy()
        y_true = agg["y"].to_numpy()
        y_score = agg["score"].to_numpy()
        subjects = agg["subject"].to_numpy()
        activities = agg["activity"].to_numpy()
    else:
        unit_ids = sub["sample_key"].to_numpy() if "sample_key" in sub else np.asarray(te)
        y_true, y_score = y[te], score
        subjects = sub["subject"].to_numpy()
        activities = sub["activity"].to_numpy()

    return ArmResult(arm, unit_ids, y_true, y_score, subjects, activities,
                     int(tr.size), int(len(arm.train_subjects)))


def _metrics(y_true: np.ndarray, y_score: np.ndarray) -> Dict[str, float]:
    if np.unique(y_true).size < 2:
        return {"auc": float("nan"), "ba_at_half": float("nan"),
                "ba_at_oracle": float("nan"), "oracle_threshold": float("nan")}
    auc = float(roc_auc_score(y_true, y_score))
    ba_half = float(balanced_accuracy_score(y_true, (y_score >= 0.5).astype(int)))
    # oracle 임계는 테스트에서 고른다 — 캘리브레이션을 배제한 상한이지 일반화 추정치가 아니다.
    best, best_t = -1.0, 0.5
    for t in np.unique(y_score):
        ba = balanced_accuracy_score(y_true, (y_score >= t).astype(int))
        if ba > best:
            best, best_t = float(ba), float(t)
    return {"auc": auc, "ba_at_half": ba_half,
            "ba_at_oracle": best, "oracle_threshold": best_t}


def score_arm(res: ArmResult) -> Dict[str, Any]:
    n_pos = int(np.sum(res.y_true == 1))
    n_neg = int(np.sum(res.y_true == 0))
    return {
        "arm": res.arm.name, "target_env": res.arm.target_env,
        "replicate": res.arm.replicate, "source_env": res.arm.source_env,
        **_metrics(res.y_true, res.y_score),
        "mean_pred_prob": float(np.mean(res.y_score)),
        "n_test": int(len(res.y_true)), "n_pos": n_pos, "n_neg": n_neg,
        "n_test_subjects": int(np.unique(res.subjects).size),
        "n_train": res.n_train, "n_train_subjects": res.n_train_subjects,
        # 이 값보다 작은 AUC 차이는 읽으면 안 된다.
        "auc_granularity": float(1.0 / max(n_pos * n_neg, 1)),
    }


def bootstrap_subject_ci(res: ArmResult, statistic: str = "auc",
                         n_boot: int = 2000, alpha: float = 0.05,
                         seed: int = 0) -> Dict[str, float]:
    """
    테스트 **피험자 클러스터** 부트스트랩. 행 단위 부트스트랩은 한 사람의 파일들을
    독립으로 취급해 CI 가 몇 배 좁아진다.
    """
    subs = np.unique(res.subjects)
    if subs.size < 2:
        return {"lo": float("nan"), "hi": float("nan"), "n_subjects": int(subs.size)}

    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        pick = rng.choice(subs, size=subs.size, replace=True)
        idx = np.concatenate([np.where(res.subjects == s)[0] for s in pick])
        m = _metrics(res.y_true[idx], res.y_score[idx])
        if np.isfinite(m[statistic]):
            vals.append(m[statistic])
    if not vals:
        return {"lo": float("nan"), "hi": float("nan"), "n_subjects": int(subs.size)}
    return {"lo": float(np.percentile(vals, 100 * alpha / 2)),
            "hi": float(np.percentile(vals, 100 * (1 - alpha / 2))),
            "n_subjects": int(subs.size)}


def per_subject_scores(res: ArmResult, statistic: str = "auc") -> pd.DataFrame:
    """피험자별 점수 — 한 사람이 결과를 끌고 가는지 즉시 보인다."""
    out = []
    for s in np.unique(res.subjects):
        m = np.where(res.subjects == s)[0]
        out.append({"subject": int(s), "n": int(m.size),
                    "value": _metrics(res.y_true[m], res.y_score[m])[statistic]})
    return pd.DataFrame(out)


def paired_gap(scores: pd.DataFrame, arm_a: str = "IN", arm_b: str = "CROSS",
               statistic: str = "auc", alpha: float = 0.05,
               n_eff_subjects: Optional[int] = None) -> Dict[str, Any]:
    """
    `(target_env, replicate)` 로 두 arm 을 짝지어 대응 차이를 낸다 — 테스트셋이 동일하므로
    독립 두 평균을 겹쳐 보는 것보다 훨씬 민감하다.

    `mde80` = 2.8·sd/√n_eff. **`|gap| < mde80` 이면 "강건하다"가 아니라 "검출력 부족"이다.**
    """
    a = scores[scores["arm"] == arm_a].set_index(["target_env", "replicate"])[statistic]
    b = scores[scores["arm"] == arm_b].set_index(["target_env", "replicate"])[statistic]
    common = a.index.intersection(b.index)
    if len(common) < 2:
        return {"n_pairs": int(len(common)), "gap_mean": None, "underpowered": True}

    d = (a.loc[common] - b.loc[common]).to_numpy(dtype=float)
    d = d[np.isfinite(d)]
    if d.size < 2:
        return {"n_pairs": int(d.size), "gap_mean": None, "underpowered": True}

    n_eff = int(n_eff_subjects) if n_eff_subjects else int(d.size)
    sd = float(np.std(d, ddof=1))
    mde80 = float(2.8 * sd / max(np.sqrt(max(n_eff, 1)), 1e-9))
    gap = float(np.mean(d))
    return {
        "n_pairs": int(d.size), "gap_mean": gap, "gap_sd": sd,
        "gap_lo": float(np.percentile(d, 100 * alpha / 2)),
        "gap_hi": float(np.percentile(d, 100 * (1 - alpha / 2))),
        "win_rate": float(np.mean(d > 0)),
        "mde80": mde80, "n_eff_subjects": n_eff,
        "underpowered": bool(abs(gap) < mde80),
    }


def permute_labels_within_subject(rows: pd.DataFrame, y: np.ndarray,
                                  rng: np.random.Generator) -> np.ndarray:
    """
    낙상 라벨을 **파일 단위로, 피험자 내부에서만** 섞는다.

    행 단위 셔플은 한 파일의 rx 3행에 서로 다른 라벨을 주는 불가능한 배치가 되어 귀무를
    인위적으로 낮춘다. 피험자 내 제한은 각자의 클래스 주변분포를 보존하므로
    "누구인지"만 외운 모델은 이 귀무를 넘지 못한다.
    """
    out = y.copy()
    files = rows["filepath"].to_numpy()
    subs = rows["subject"].to_numpy()

    file_df = pd.DataFrame({"filepath": files, "subject": subs, "y": y}) \
        .drop_duplicates("filepath")
    mapping: Dict[str, int] = {}
    for _, grp in file_df.groupby("subject"):
        shuffled = rng.permutation(grp["y"].to_numpy())
        mapping.update(dict(zip(grp["filepath"].to_numpy(), shuffled)))

    for i, f in enumerate(files):
        out[i] = mapping.get(f, out[i])
    return out


def split_structure_hash(arms: Sequence[Arm]) -> str:
    """모든 브랜치가 바이트 동일한 폴드를 봤음을 증명하는 해시."""
    payload = [[a.name, a.target_env, a.replicate,
                a.train_idx.tolist(), a.test_idx.tolist()] for a in arms]
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:10]


# --- canary ------------------------------------------------------------------

def group_canary(rows: pd.DataFrame, arms: Sequence[Arm]) -> Dict[str, Any]:
    """학습·테스트가 피험자/파일/세션 어느 수준에서도 겹치지 않는지 단언한다."""
    bad_subject = bad_file = bad_session = 0
    for a in arms:
        tr, te = rows.iloc[a.train_idx], rows.iloc[a.test_idx]
        if set(tr["subject"]) & set(te["subject"]):
            bad_subject += 1
        if set(tr["filepath"]) & set(te["filepath"]):
            bad_file += 1
        if "session_id" in rows.columns and set(tr["session_id"]) & set(te["session_id"]):
            bad_session += 1
    return {"n_arms": len(arms), "subject_overlap": bad_subject,
            "file_overlap": bad_file, "session_overlap": bad_session,
            "ok": bool(bad_subject == 0 and bad_file == 0 and bad_session == 0)}


def composition_canary(arms: Sequence[Arm]) -> Dict[str, Any]:
    """맞추지 못한 `(activity, is_fall)` 셀이 있었는지 — 있으면 그 자체가 발견이다."""
    total = worst = 0
    for a in arms:
        if a.achieved is None or a.achieved.empty:
            continue
        short = a.achieved["shortfall"]
        total += int((short > 0).sum())
        worst = max(worst, int(short.max()))
    return {"cells_short": total, "worst_shortfall": worst, "ok": bool(total == 0)}
