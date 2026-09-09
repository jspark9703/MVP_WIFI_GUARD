"""
분포 발산 지표와 가설검정. 순수 함수 — streamlit 을 import 하지 않는다.

관례 (scripts/streamlit_app/lib/stats_analysis.py 와 동일): 퇴화 입력에서 예외를
던지지 않고 값이 None 인 dict 를 반환한다. 호출부는 캡션으로 처리하면 된다.

**분석 단위는 패킷이 아니라 파일이다.** agc_summary_stats.csv 는 env 당 368만
패킷을 풀링하는데, 그 n 에서는 모든 p 가 0 이고 모든 검정이 "유의"하다.
파일별 집계값(파일당 1행)이 올바른 입도이며, 이 모듈의 모든 함수는 그것을 가정한다.

효과크기를 우선하고 p 는 각주로 둔다 — 표본 크기 슬라이더를 움직이면 p 는 움직이지만
KS D 나 Cliff's δ 는 거의 움직이지 않는다 (sample_size_curve 가 그것을 보여준다).
"""

from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from lib.constants import cliff_magnitude

_MIN_N = 3


def _clean(x: Iterable[float]) -> np.ndarray:
    a = np.asarray(list(x), dtype=np.float64)
    return a[np.isfinite(a)]


def cliffs_delta(a: Iterable[float], b: Iterable[float]) -> Dict[str, Any]:
    """
    Cliff's delta — 방향성 있는 비모수 효과크기, δ ∈ [-1, 1].

    이미 필요한 Mann-Whitney U 에서 유도한다: δ = 2U/(n_a·n_b) − 1.
    호출 한 번으로 효과크기와 p 를 동시에 얻는다.
    """
    x, y = _clean(a), _clean(b)
    if x.size < _MIN_N or y.size < _MIN_N:
        return {"delta": None, "magnitude": None, "u": None, "p_value": None,
                "n_a": int(x.size), "n_b": int(y.size)}
    try:
        res = stats.mannwhitneyu(x, y, alternative="two-sided")
    except ValueError:
        return {"delta": None, "magnitude": None, "u": None, "p_value": None,
                "n_a": int(x.size), "n_b": int(y.size)}
    delta = 2.0 * float(res.statistic) / (x.size * y.size) - 1.0
    return {"delta": float(delta), "magnitude": cliff_magnitude(delta),
            "u": float(res.statistic), "p_value": float(res.pvalue),
            "n_a": int(x.size), "n_b": int(y.size)}


def ks_effect(a: Iterable[float], b: Iterable[float]) -> Dict[str, Any]:
    """
    2-표본 Kolmogorov-Smirnov. D ∈ [0,1] 자체가 효과크기이고 **지표 간 비교가 가능**해서
    (지표 × env쌍) 행렬의 셀 값으로 적합하다. 10 Hz 하위집단이 만드는 이봉 분포에도
    민감하다 — 그 상황에서 평균/표준편차는 오히려 오도한다.
    """
    x, y = _clean(a), _clean(b)
    if x.size < _MIN_N or y.size < _MIN_N:
        return {"d": None, "p_value": None, "n_a": int(x.size), "n_b": int(y.size)}
    res = stats.ks_2samp(x, y)
    return {"d": float(res.statistic), "p_value": float(res.pvalue),
            "n_a": int(x.size), "n_b": int(y.size)}


def standardized_wasserstein(a: Iterable[float], b: Iterable[float]) -> Dict[str, Any]:
    """
    Wasserstein 거리를 합동 표준편차로 나눠 무차원화한다.

    표준화 없이는 단위를 갖고 다니므로 agc_mean 과 q_entropy_norm 을 한 행렬에
    섞을 수 없다. 그래서 기본 셀 값이 아니라 보조 지표로 둔다.
    """
    x, y = _clean(a), _clean(b)
    if x.size < _MIN_N or y.size < _MIN_N:
        return {"w": None, "w_std": None, "pooled_std": None}
    w = float(stats.wasserstein_distance(x, y))
    pooled = float(np.sqrt(((x.size - 1) * np.var(x, ddof=1) +
                            (y.size - 1) * np.var(y, ddof=1)) /
                           max(x.size + y.size - 2, 1)))
    return {"w": w, "w_std": float(w / pooled) if pooled > 1e-12 else None,
            "pooled_std": pooled}


def kruskal_omnibus(values_by_group: Dict[Any, Iterable[float]]) -> Dict[str, Any]:
    """
    Kruskal-Wallis 옴니버스 + ε² 효과크기.

    H 는 n 에 비례해 커지므로 단독으로는 시프트 크기를 말해주지 못한다.
    ε² = (H − k + 1)/(n − k) 를 병기한다.
    """
    groups = [_clean(v) for v in values_by_group.values()]
    groups = [g for g in groups if g.size >= _MIN_N]
    k = len(groups)
    n = int(sum(g.size for g in groups))
    if k < 2:
        return {"h": None, "p_value": None, "n_groups": k, "n": n, "eps_sq": None}
    try:
        res = stats.kruskal(*groups)
    except ValueError:
        return {"h": None, "p_value": None, "n_groups": k, "n": n, "eps_sq": None}
    eps_sq = (float(res.statistic) - k + 1.0) / (n - k) if n > k else None
    return {"h": float(res.statistic), "p_value": float(res.pvalue),
            "n_groups": k, "n": n,
            "eps_sq": float(np.clip(eps_sq, 0.0, 1.0)) if eps_sq is not None else None}


def js_divergence(p: Iterable[float], q: Iterable[float], base: float = 2.0) -> float:
    """
    Jensen-Shannon divergence. base=2 이면 [0,1] 유계, 대칭, 빈 칸에서도 유한.

    30빈 서브캐리어 분포 × n≈150 파일에서는 카이제곱의 기대도수≥5 가정이 깨지지만
    JSD 는 그런 가정이 없다. 그래서 범주형 비교의 헤드라인으로 쓴다.
    """
    a = np.asarray(list(p), dtype=np.float64)
    b = np.asarray(list(q), dtype=np.float64)
    if a.size != b.size or a.size == 0:
        return float("nan")
    sa, sb = a.sum(), b.sum()
    if sa <= 0 or sb <= 0:
        return float("nan")
    a, b = a / sa, b / sb
    m = 0.5 * (a + b)

    def _kl(u: np.ndarray, v: np.ndarray) -> float:
        mask = u > 0
        return float(np.sum(u[mask] * np.log(u[mask] / v[mask])))

    jsd = 0.5 * _kl(a, m) + 0.5 * _kl(b, m)
    return float(jsd / np.log(base))


def categorical_js_matrix(
    df: pd.DataFrame,
    group_col: str,
    value_col: str,
    categories: Optional[Sequence[Any]] = None,
) -> pd.DataFrame:
    """그룹 쌍별 범주 분포 JSD. 선택 안테나 / 선택 서브캐리어 비교용."""
    if df.empty or group_col not in df or value_col not in df:
        return pd.DataFrame(columns=["group_a", "group_b", "jsd", "n_a", "n_b"])

    cats = list(categories) if categories is not None else sorted(df[value_col].dropna().unique())
    hist: Dict[Any, np.ndarray] = {}
    for g, grp in df.groupby(group_col, sort=True):
        counts = grp[value_col].value_counts()
        hist[g] = np.array([float(counts.get(c, 0)) for c in cats])

    rows = []
    for ga, gb in combinations(sorted(hist.keys()), 2):
        rows.append({"group_a": ga, "group_b": gb,
                     "jsd": js_divergence(hist[ga], hist[gb]),
                     "n_a": int(hist[ga].sum()), "n_b": int(hist[gb].sum())})
    return pd.DataFrame(rows)


def pairwise_divergence_table(
    df: pd.DataFrame,
    metrics: Sequence[str],
    group_col: str = "env",
    pairs: Optional[Sequence[Tuple[Any, Any]]] = None,
) -> pd.DataFrame:
    """
    (지표 × 그룹쌍) 발산 표 — P5 발산 행렬의 원천 데이터.

    Returns:
        columns: metric, group_a, group_b, n_a, n_b, median_a, median_b,
                 ks_d, ks_p, cliffs_delta, cliffs_magnitude, mw_p, w_std
    """
    if df.empty or group_col not in df:
        return pd.DataFrame(columns=["metric", "group_a", "group_b", "n_a", "n_b",
                                     "median_a", "median_b", "ks_d", "ks_p",
                                     "cliffs_delta", "cliffs_magnitude", "mw_p", "w_std"])

    groups = sorted(df[group_col].dropna().unique())
    pair_list = list(pairs) if pairs is not None else list(combinations(groups, 2))

    rows = []
    for metric in metrics:
        if metric not in df.columns:
            continue
        for ga, gb in pair_list:
            a = _clean(df.loc[df[group_col] == ga, metric])
            b = _clean(df.loc[df[group_col] == gb, metric])
            ks = ks_effect(a, b)
            cd = cliffs_delta(a, b)
            ws = standardized_wasserstein(a, b)
            rows.append({
                "metric": metric, "group_a": ga, "group_b": gb,
                "n_a": int(a.size), "n_b": int(b.size),
                "median_a": float(np.median(a)) if a.size else np.nan,
                "median_b": float(np.median(b)) if b.size else np.nan,
                "ks_d": ks["d"], "ks_p": ks["p_value"],
                "cliffs_delta": cd["delta"], "cliffs_magnitude": cd["magnitude"],
                "mw_p": cd["p_value"], "w_std": ws["w_std"],
            })
    return pd.DataFrame(rows)


def benjamini_hochberg(pvals: Iterable[float]) -> np.ndarray:
    """BH step-up FDR. NaN 은 NaN 으로 통과시킨다."""
    p = np.asarray(list(pvals), dtype=np.float64)
    out = np.full(p.shape, np.nan)
    ok = np.isfinite(p)
    m = int(ok.sum())
    if m == 0:
        return out
    idx = np.where(ok)[0]
    order = idx[np.argsort(p[idx])]
    ranked = p[order] * m / np.arange(1, m + 1)
    # step-up: 뒤에서부터 누적 최소
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out[order] = np.clip(q, 0.0, 1.0)
    return out


def add_fdr(table: pd.DataFrame, p_col: str = "ks_p", q_col: str = "ks_q") -> pd.DataFrame:
    """
    쌍별 표에 BH-FDR 열을 추가한다.

    3쌍 × ~12지표 = 36검정. 지표들이 강하게 상관(agc↔amp_mean↔q)하므로
    Bonferroni 는 과보수적이고 BH 가 적절하다. 원 p 와 q 를 나란히 보여준다.
    """
    if table.empty or p_col not in table.columns:
        return table
    out = table.copy()
    out[q_col] = benjamini_hochberg(out[p_col].to_numpy())
    return out


def gap_comparison(
    df: pd.DataFrame,
    metrics: Sequence[str],
    env_col: str = "env",
    class_col: str = "fall_label",
) -> pd.DataFrame:
    """
    환경 격차 vs 활동 격차 — 이 앱의 결정적 비교.

    낙상 검출기 입장에서 환경 격차가 클래스 격차보다 크면 크로스 도메인 일반화가
    깨진다. 환경 격차를 클래스 내부에서도(fall 안에서, non-fall 안에서) 재계산해
    "클래스 불균형 때문에 생긴 착시"가 아님을 보인다.

    Returns:
        columns: metric, env_gap, class_gap, env_gap_within_fall,
                 env_gap_within_nonfall, ratio, env_exceeds_class
    """
    if df.empty:
        return pd.DataFrame(columns=["metric", "env_gap", "class_gap",
                                     "env_gap_within_fall", "env_gap_within_nonfall",
                                     "ratio", "env_exceeds_class"])

    envs = sorted(df[env_col].dropna().unique())

    def _max_env_gap(sub: pd.DataFrame, metric: str) -> Optional[float]:
        vals = []
        for ga, gb in combinations(envs, 2):
            d = cliffs_delta(sub.loc[sub[env_col] == ga, metric],
                             sub.loc[sub[env_col] == gb, metric])["delta"]
            if d is not None:
                vals.append(abs(d))
        return max(vals) if vals else None

    rows = []
    for metric in metrics:
        if metric not in df.columns:
            continue
        env_gap = _max_env_gap(df, metric)

        classes = sorted(df[class_col].dropna().unique())
        class_gap = None
        if len(classes) == 2:
            cd = cliffs_delta(df.loc[df[class_col] == classes[0], metric],
                              df.loc[df[class_col] == classes[1], metric])["delta"]
            class_gap = abs(cd) if cd is not None else None

        within = {}
        for lbl in ("Fall", "Non-fall"):
            sub = df[df[class_col] == lbl]
            within[lbl] = _max_env_gap(sub, metric) if not sub.empty else None

        ratio = (env_gap / class_gap) if (env_gap is not None and class_gap not in (None, 0)) else None
        rows.append({
            "metric": metric, "env_gap": env_gap, "class_gap": class_gap,
            "env_gap_within_fall": within.get("Fall"),
            "env_gap_within_nonfall": within.get("Non-fall"),
            "ratio": ratio,
            "env_exceeds_class": (env_gap > class_gap)
            if (env_gap is not None and class_gap is not None) else None,
        })
    return pd.DataFrame(rows)


def stratified_env_test(
    df: pd.DataFrame,
    metric: str,
    strat_col: str = "activity",
    env_col: str = "env",
) -> pd.DataFrame:
    """
    각 층(활동) **내부에서** env Kruskal. 환경 효과가 모든 활동에서 살아남으면
    그것은 활동 아티팩트가 아니다 — "방인가 행동인가"에 대한 결정적 통제.
    """
    if df.empty or metric not in df.columns or strat_col not in df.columns:
        return pd.DataFrame(columns=[strat_col, "n", "n_groups", "h", "p_value", "eps_sq"])

    rows = []
    for key, grp in df.groupby(strat_col, sort=True):
        res = kruskal_omnibus({g: sub[metric] for g, sub in grp.groupby(env_col, sort=True)})
        rows.append({strat_col: key, "n": res["n"], "n_groups": res["n_groups"],
                     "h": res["h"], "p_value": res["p_value"], "eps_sq": res["eps_sq"]})
    out = pd.DataFrame(rows)
    return add_fdr(out, p_col="p_value", q_col="p_adj")


def sample_size_curve(
    df: pd.DataFrame,
    metric: str,
    group_col: str,
    pair: Tuple[Any, Any],
    sizes: Sequence[int],
    n_boot: int = 20,
    seed: int = 0,
) -> pd.DataFrame:
    """
    표본 크기 n 을 바꿔가며 KS D 와 p 를 부트스트랩으로 재추정한다.

    **D 는 거의 평평하고 p 만 급락하는 것을 보여주는 것이 목적**이다 —
    사이드바 표본 크기 슬라이더가 결론을 만들어내는 게 아니라는 증거이자,
    "효과크기 우선, p 는 각주" 프레이밍의 정당화.
    """
    ga, gb = pair
    a_all = _clean(df.loc[df[group_col] == ga, metric])
    b_all = _clean(df.loc[df[group_col] == gb, metric])
    if a_all.size < _MIN_N or b_all.size < _MIN_N:
        return pd.DataFrame(columns=["n", "ks_d_mean", "ks_d_lo", "ks_d_hi", "p_median"])

    rng = np.random.default_rng(seed)
    rows = []
    for n in sizes:
        n_eff = int(min(n, a_all.size, b_all.size))
        if n_eff < _MIN_N:
            continue
        ds, ps = [], []
        for _ in range(n_boot):
            a = rng.choice(a_all, n_eff, replace=False)
            b = rng.choice(b_all, n_eff, replace=False)
            r = stats.ks_2samp(a, b)
            ds.append(float(r.statistic))
            ps.append(float(r.pvalue))
        rows.append({
            "n": n_eff,
            "ks_d_mean": float(np.mean(ds)),
            "ks_d_lo": float(np.percentile(ds, 5)),
            "ks_d_hi": float(np.percentile(ds, 95)),
            "p_median": float(np.median(ps)),
        })
    return pd.DataFrame(rows).drop_duplicates(subset="n")
