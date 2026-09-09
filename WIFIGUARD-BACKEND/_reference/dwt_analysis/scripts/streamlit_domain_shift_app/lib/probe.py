"""
분류 프로브 — "환경을 특징으로부터 맞힐 수 있는가"를 정량화한다.
순수 함수 (sklearn/numpy/pandas만), streamlit 을 import 하지 않는다.

맞힐 수 있다 = 측정 가능한 도메인 시프트가 존재한다. 개별 지표의 p-값 36개보다
하나의 정확도 숫자가 더 직접적인 증거다.

**교락에 대한 정직한 처리**: Mendeley 는 env 별로 피험자가 서로소이므로
(E1=S1-10, E2=S11-20, E3=S21-30) 피험자 단위 LeaveOneGroupOut 도 env 와 subject 를
분리하지 **못한다** — S03 을 빼도 E1 은 S01,S02,S04-S10 으로 남는다. LOGO 는
"처음 보는 사람, 같은 방"에 대한 정직한 일반화 추정치일 뿐 교락 해소책이 아니다.
그래서 ``subject_probe_within_env`` 를 통제로 함께 제공한다: 단일 env 내부에서
사람을 맞히는 정확도가 env 정확도에 필적하면, "환경 신호"의 상당 부분은 사람 신호다.
"""

from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, confusion_matrix
from sklearn.model_selection import LeaveOneGroupOut, StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

_MIN_ROWS = 12


def _make_model(seed: int) -> Pipeline:
    return Pipeline([
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(
            max_iter=2000, class_weight="balanced", random_state=seed, n_jobs=None,
        )),
    ])


def _prepare(df: pd.DataFrame, feature_cols: Sequence[str]) -> tuple:
    """유한값만 남기고, 분산이 0인 열은 버린다."""
    cols = [c for c in feature_cols if c in df.columns]
    if not cols:
        return None, None, []
    sub = df[cols].replace([np.inf, -np.inf], np.nan)
    keep_rows = sub.notna().all(axis=1)
    sub = sub[keep_rows]
    usable = [c for c in cols if sub[c].std(ddof=0) > 1e-12]
    if not usable:
        return None, None, []
    return sub[usable].to_numpy(dtype=np.float64), keep_rows, usable


def env_probe(
    df: pd.DataFrame,
    feature_cols: Sequence[str],
    label_col: str = "env",
    group_col: str = "subject",
    seed: int = 0,
    n_permutations: int = 20,
) -> Dict[str, Any]:
    """
    피험자 단위 LeaveOneGroupOut 으로 env 를 예측한다.

    순열 귀무분포는 **피험자 단위로** 라벨을 섞는다. env 는 subject 의 함수이므로
    행 단위로 섞으면 한 사람이 여러 env 라벨을 갖는 불가능한 배치가 되고,
    귀무분포가 인위적으로 낮아져 비교가 무의미해진다.

    Returns:
        {balanced_accuracy, chance, n, n_groups, n_features, features,
         confusion (DataFrame), per_class_recall (dict),
         perm_scores (ndarray), perm_mean, perm_std, perm_p, error}
    """
    X, keep, usable = _prepare(df, feature_cols)
    if X is None:
        return {"error": "사용 가능한 특징 열이 없습니다.", "balanced_accuracy": None}

    sub = df[keep]
    y = sub[label_col].to_numpy()
    groups = sub[group_col].to_numpy()
    classes = np.unique(y)

    if len(sub) < _MIN_ROWS or classes.size < 2 or np.unique(groups).size < 2:
        return {"error": "행/그룹/클래스 수가 부족합니다.", "balanced_accuracy": None,
                "n": int(len(sub)), "n_groups": int(np.unique(groups).size)}

    def _oof_score(y_vec: np.ndarray) -> Optional[tuple]:
        """(balanced_accuracy, out-of-fold 예측, 채워진 마스크) 또는 None."""
        logo = LeaveOneGroupOut()
        pred = np.empty(len(y_vec), dtype=y_vec.dtype)
        filled = np.zeros(len(y_vec), dtype=bool)
        for tr, te in logo.split(X, y_vec, groups):
            if np.unique(y_vec[tr]).size < 2:
                continue
            model = _make_model(seed)
            model.fit(X[tr], y_vec[tr])
            pred[te] = model.predict(X[te])
            filled[te] = True
        if filled.sum() < _MIN_ROWS:
            return None
        return float(balanced_accuracy_score(y_vec[filled], pred[filled])), pred, filled

    scored = _oof_score(y)
    if scored is None:
        return {"error": "LOGO 분할에서 유효한 폴드가 없습니다.", "balanced_accuracy": None}
    acc, pred, filled = scored

    cm = confusion_matrix(y[filled], pred[filled], labels=classes)
    cm_df = pd.DataFrame(cm, index=[f"true {c}" for c in classes],
                         columns=[f"pred {c}" for c in classes])
    with np.errstate(invalid="ignore"):
        recalls = np.diag(cm) / np.maximum(cm.sum(axis=1), 1)
    per_class_recall = {str(c): float(r) for c, r in zip(classes, recalls)}

    # 피험자 -> env 매핑을 섞는 순열 귀무분포
    rng = np.random.default_rng(seed)
    subj_to_label = sub.groupby(group_col)[label_col].first()
    perm_scores: List[float] = []
    for _ in range(max(int(n_permutations), 0)):
        shuffled = subj_to_label.sample(frac=1.0, random_state=int(rng.integers(0, 2**31 - 1)))
        mapping = dict(zip(subj_to_label.index, shuffled.to_numpy()))
        y_perm = np.array([mapping[g] for g in groups])
        if np.unique(y_perm).size < 2:
            continue
        res = _oof_score(y_perm)
        if res is not None:
            perm_scores.append(res[0])

    perm = np.asarray(perm_scores, dtype=np.float64)
    perm_p = (float(np.sum(perm >= acc) + 1) / (perm.size + 1)) if perm.size else None

    # 계수 중요도: 전체 데이터로 1회 적합, 다중클래스는 클래스 평균 |coef|
    full = _make_model(seed).fit(X, y)
    coef = np.abs(np.atleast_2d(full.named_steps["clf"].coef_)).mean(axis=0)
    importance = (pd.DataFrame({"feature": usable, "abs_coef_mean": coef})
                  .sort_values("abs_coef_mean", ascending=False)
                  .reset_index(drop=True))
    importance["rank"] = importance.index + 1

    return {
        "error": None,
        "balanced_accuracy": acc,
        "chance": float(1.0 / classes.size),
        "n": int(filled.sum()), "n_groups": int(np.unique(groups).size),
        "n_features": len(usable), "features": usable,
        "confusion": cm_df, "per_class_recall": per_class_recall,
        "perm_scores": perm, "perm_mean": float(perm.mean()) if perm.size else None,
        "perm_std": float(perm.std()) if perm.size else None, "perm_p": perm_p,
        "coef_importance": importance,
    }


def subject_probe_within_env(
    df: pd.DataFrame,
    feature_cols: Sequence[str],
    env: int,
    label_col: str = "subject",
    env_col: str = "env",
    seed: int = 0,
    n_splits: int = 5,
) -> Dict[str, Any]:
    """
    단일 env 안에서 **피험자**를 맞힌다 — 교락에 대한 통제.

    여기서는 피험자가 라벨이므로 그룹 홀드아웃이 불가능하고 StratifiedKFold 를 쓴다.
    같은 사람의 다른 파일이 학습에 들어가므로 이 정확도는 "인물 식별 상한"에 가깝다.
    이 값이 env 정확도에 필적하면 env 신호의 상당 부분이 인물 신호라는 뜻이다.
    """
    sub_env = df[df[env_col] == env]
    X, keep, usable = _prepare(sub_env, feature_cols)
    if X is None:
        return {"error": "사용 가능한 특징 열이 없습니다.", "balanced_accuracy": None, "env": env}

    sub = sub_env[keep]
    y = sub[label_col].to_numpy()
    classes, counts = np.unique(y, return_counts=True)

    if classes.size < 2 or counts.min() < 2 or len(sub) < _MIN_ROWS:
        return {"error": "피험자/표본 수가 부족합니다.", "balanced_accuracy": None,
                "env": env, "n": int(len(sub)), "n_subjects": int(classes.size)}

    k = int(min(n_splits, counts.min()))
    skf = StratifiedKFold(n_splits=k, shuffle=True, random_state=seed)
    pred = np.empty(len(y), dtype=y.dtype)
    for tr, te in skf.split(X, y):
        model = _make_model(seed)
        model.fit(X[tr], y[tr])
        pred[te] = model.predict(X[te])

    return {
        "error": None, "env": int(env),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "chance": float(1.0 / classes.size),
        "n": int(len(sub)), "n_subjects": int(classes.size),
        "n_features": len(usable), "n_splits": k,
    }
