"""
P6. 도메인 시프트 종합.

앞 페이지들의 산출물을 모아 하나의 결론으로 수렴시킨다. 핵심 질문은
"환경 격차가 활동(낙상) 격차보다 큰가" — 그렇다면 크로스 도메인 일반화가 깨진다.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from lib.constants import (
    ACQUISITION_METRICS,
    AMPLITUDE_METRICS,
    ENV_LABELS,
    METRIC_BLOCKS,
    METRIC_DESCRIPTIONS,
    PC_METRICS,
    Q_METRICS,
    TIMING_METRICS,
)
from lib.cache import (
    metadata_batch_cached,
    probe_batch_cached,
    probe_cache_status,
    sample_cached,
)
from lib.divergence import (
    add_fdr,
    gap_comparison,
    pairwise_divergence_table,
    sample_size_curve,
    stratified_env_test,
)
from lib.plotting import (
    plot_confusion,
    plot_divergence_heatmap,
    plot_gap_comparison_bar,
    plot_permutation_null,
    plot_sample_size_curve,
)
from lib.render import chart
from lib.probe import env_probe, subject_probe_within_env
from lib.state_defaults import (
    meta_fingerprint,
    params_fingerprint,
    probe_kwargs,
    sampling_kwargs,
)

st.title("6. 도메인 시프트 종합")
st.caption(
    "앞의 모든 지표를 하나의 판단으로 모은다. 낙상 검출기 입장에서 결정적인 질문은 "
    "**환경 격차가 활동 격차보다 큰가** — 그렇다면 한 방에서 학습한 모델이 다른 방에서 무너진다."
)

repo_root = Path(st.session_state["repo_root"])
mendeley_root = st.session_state["mendeley_root"]
cache_dir = st.session_state["cache_dir"]

sample = sample_cached(mendeley_root, **sampling_kwargs())
if sample.empty:
    st.warning("표본이 비어 있습니다. 페이지 1에서 표본을 설정하세요.")
    st.stop()

paths = sample["filepath_str"].tolist()
pkwargs = probe_kwargs()

with st.sidebar:
    st.header("종합 설정")
    matrix_value = st.radio(
        "발산 행렬 셀 값", ["ks_d", "cliffs_delta", "w_std"],
        index=["ks_d", "cliffs_delta", "w_std"].index(st.session_state["sm_matrix_value"]),
        format_func=lambda v: {"ks_d": "KS D (0~1, 크기)",
                               "cliffs_delta": "Cliff's δ (−1~1, 방향)",
                               "w_std": "표준화 Wasserstein"}[v],
    )
    feature_set = st.radio(
        "분류 프로브 특징 집합", ["전체", "수집만", "진폭만", "q만"],
        index=["전체", "수집만", "진폭만", "q만"].index(st.session_state["sm_feature_set"])
        if st.session_state["sm_feature_set"] in ["전체", "수집만", "진폭만", "q만"] else 0,
    )
    n_perm = st.slider("순열 반복 횟수", 0, 50, int(st.session_state["sm_n_permutations"]), step=5)
    seed = st.number_input("프로브 seed", 0, 9999, int(st.session_state["sm_seed"]), step=1)

st.session_state.update({
    "sm_matrix_value": matrix_value, "sm_feature_set": feature_set,
    "sm_n_permutations": int(n_perm), "sm_seed": int(seed),
})

status = probe_cache_status(cache_dir, params_fingerprint(), paths)
if status["missing"] > 0 and not st.session_state["sm_has_run"]:
    st.info(
        f"이 페이지는 페이지 2~5의 결과를 사용합니다. 현재 CSI probe 캐시가 "
        f"{status['cached']}/{status['total']} 입니다."
    )
    if st.button("전부 지금 실행", type="primary", use_container_width=True):
        st.session_state["sm_has_run"] = True
        st.rerun()
    st.stop()

st.session_state["sm_has_run"] = True

progress = st.progress(0.0, text="결과 수집 중...")


def _on_progress(done: int, total: int, name: str) -> None:
    progress.progress(done / max(total, 1), text=f"[{done}/{total}] {name}")


records, q_long, sub_long, pc_long, errors = probe_batch_cached(
    paths, cache_dir, pkwargs, progress_callback=_on_progress,
)
meta, _ = metadata_batch_cached(paths, float(st.session_state["pp_fs_hz"]),
                                cache_dir, meta_fingerprint())
progress.empty()

if records.empty:
    st.error("결과가 비어 있습니다. 페이지 3 또는 4를 먼저 실행하세요.")
    st.stop()

meta_cols = [c for c in meta.columns if c not in records.columns or c == "filepath"]
merged = records.merge(meta[meta_cols], on="filepath", how="left") if not meta.empty else records

if merged["env"].nunique() < 2:
    st.warning("환경이 하나뿐이라 환경 간 비교를 할 수 없습니다. 페이지 1에서 환경을 2개 이상 선택하세요.")
    st.stop()

# --- 6.1 발산 행렬 -----------------------------------------------------------

st.subheader("6.1 발산 행렬")

all_metrics = [m for m in (ACQUISITION_METRICS + TIMING_METRICS + AMPLITUDE_METRICS
                           + Q_METRICS + PC_METRICS) if m in merged.columns]
table = add_fdr(pairwise_divergence_table(merged, all_metrics, "env"))

if table.empty:
    st.info("발산 행렬을 만들 데이터가 부족합니다.")
else:
    chart(plot_divergence_heatmap(table, matrix_value))
    st.caption(
        {"ks_d": "KS D 는 두 ECDF 의 최대 수직 거리로, 0~1 유계이고 **지표 간 비교가 가능**하다.",
         "cliffs_delta": "Cliff's δ 는 방향을 갖는다(−1~1). 중립 회색이 0(차이 없음)이다.",
         "w_std": "Wasserstein 을 합동 표준편차로 나눠 무차원화한 값."}[matrix_value]
        + " 블록별 지표 정의는 아래 표 참조."
    )

    with st.expander("지표 블록과 정의"):
        rows = [{"블록": blk, "지표": m, "설명": METRIC_DESCRIPTIONS.get(m, "")}
                for blk, ms in METRIC_BLOCKS.items() for m in ms if m in merged.columns]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# --- 6.2 환경 격차 vs 활동 격차 ----------------------------------------------

st.subheader("6.2 환경 격차 vs 활동 격차 — 결정적 비교")

if merged["fall_label"].nunique() < 2:
    st.warning(
        "표본에 낙상/비낙상이 모두 있어야 이 비교를 할 수 있습니다. "
        "페이지 1에서 활동 필터를 비우거나 A02/A05 를 포함시키세요."
    )
else:
    gaps = gap_comparison(merged, all_metrics)
    if gaps.empty:
        st.info("비교할 지표가 없습니다.")
    else:
        chart(plot_gap_comparison_bar(gaps))

        valid = gaps.dropna(subset=["env_gap", "class_gap"])
        n_exceed = int(valid["env_exceeds_class"].sum())
        n_total = len(valid)
        if n_total:
            worst = valid.loc[valid["ratio"].idxmax()] if valid["ratio"].notna().any() else None
            msg = (f"**{n_total}개 지표 중 {n_exceed}개에서 환경 격차가 활동 격차보다 큽니다** "
                   f"({n_exceed / n_total:.0%}).")
            if worst is not None:
                msg += (f" 가장 심한 지표는 `{worst['metric']}` 로, 환경 격차가 활동 격차의 "
                        f"**{worst['ratio']:.1f}배** 입니다.")
            (st.error if n_exceed > n_total / 2 else st.info)(
                msg + "\n\n환경 격차가 활동 격차를 넘어서면, 모델이 낙상보다 '어느 방인지'를 "
                "먼저 학습하게 되어 크로스 도메인 일반화가 깨집니다."
            )
        st.dataframe(gaps.round(3), use_container_width=True, hide_index=True)
        st.caption(
            "`env_gap` 은 env 쌍 최대 |Cliff's δ|, `class_gap` 은 fall vs non-fall |δ|. "
            "`env_gap_within_fall`/`_within_nonfall` 은 클래스를 고정한 채 다시 잰 환경 격차로, "
            "격차가 클래스 불균형 때문에 생긴 착시가 아님을 확인하는 용도다."
        )

# --- 6.3 활동별 층화 ---------------------------------------------------------

st.subheader("6.3 활동별 층화 — 방인가 행동인가")

strat_metric = st.selectbox(
    "층화 검정 지표", [m for m in all_metrics if m in merged.columns],
    index=([m for m in all_metrics].index("final_q") if "final_q" in all_metrics else 0),
)
strat = stratified_env_test(merged, strat_metric, "activity")
if strat.empty:
    st.info("층화 검정을 할 데이터가 부족합니다.")
else:
    strat = strat.merge(
        merged[["activity", "activity_name"]].drop_duplicates(), on="activity", how="left"
    )
    st.dataframe(
        strat[["activity", "activity_name", "n", "n_groups", "h", "eps_sq", "p_value", "p_adj"]]
        .round({"h": 2, "eps_sq": 3}), use_container_width=True, hide_index=True,
    )
    sig = strat[strat["p_adj"] < 0.05] if "p_adj" in strat else pd.DataFrame()
    st.caption(
        f"활동 {len(strat)}개 중 **{len(sig)}개**에서 환경 효과가 FDR 보정 후에도 유의합니다 "
        f"(`{strat_metric}` 기준). 각 활동 **내부에서** 검정하므로, 여기서 살아남는 환경 효과는 "
        "활동 구성 차이로 설명될 수 없습니다."
    )

# --- 6.4 환경 분류 프로브 -----------------------------------------------------

st.subheader("6.4 환경 분류 프로브")

feature_map = {
    "전체": all_metrics,
    "수집만": [m for m in (ACQUISITION_METRICS + TIMING_METRICS) if m in merged.columns],
    "진폭만": [m for m in AMPLITUDE_METRICS if m in merged.columns],
    "q만": [m for m in (Q_METRICS + PC_METRICS) if m in merged.columns],
}
features = feature_map[feature_set]

if not features:
    st.info("선택한 특징 집합에 사용 가능한 열이 없습니다.")
else:
    with st.spinner("피험자 LeaveOneGroupOut 교차검증 중..."):
        res = env_probe(merged, features, "env", "subject", seed=int(seed),
                        n_permutations=int(n_perm))

    if res.get("error"):
        st.warning(res["error"])
    else:
        acc, chance = res["balanced_accuracy"], res["chance"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("balanced accuracy", f"{acc:.3f}", delta=f"{acc - chance:+.3f} vs 우연")
        c2.metric("우연 수준", f"{chance:.3f}")
        c3.metric("순열 평균",
                  f"{res['perm_mean']:.3f}" if res["perm_mean"] is not None else "—")
        c4.metric("순열 p",
                  f"{res['perm_p']:.3f}" if res["perm_p"] is not None else "—")

        pc1, pc2 = st.columns(2)
        with pc1:
            chart(plot_confusion(res["confusion"]))
        with pc2:
            chart(plot_permutation_null(acc, res["perm_scores"], chance))

        st.caption(
            f"특징 **{res['n_features']}개**, 파일 **{res['n']}개**, 피험자 **{res['n_groups']}명**. "
            "순열 귀무분포는 **피험자 단위로** env 라벨을 섞는다 — env 는 subject 의 함수이므로 "
            "행 단위로 섞으면 한 사람이 여러 env 를 갖는 불가능한 배치가 되어 귀무분포가 "
            "인위적으로 낮아진다."
        )
        with st.expander("특징 중요도 (|계수| 평균)"):
            imp = res["coef_importance"].copy()
            imp["설명"] = imp["feature"].map(lambda m: METRIC_DESCRIPTIONS.get(m, ""))
            st.dataframe(imp.head(25).round(4), use_container_width=True, hide_index=True)

# --- 6.5 통제 ----------------------------------------------------------------

st.subheader("6.5 통제 — 같은 환경 안에서 사람 맞히기")

st.caption(
    "피험자가 env 별로 서로소이므로 LOGO 도 교락을 풀지 못한다. 단일 env **내부에서** "
    "피험자를 맞히는 정확도가 위 env 정확도에 필적하면, '환경 신호'의 상당 부분이 인물 신호다. "
    "이 앱에서 교락에 대해 할 수 있는 가장 정직한 통제다."
)

if not features:
    st.info("특징이 없어 통제 검정을 건너뜁니다.")
else:
    rows = []
    for env in sorted(merged["env"].dropna().unique()):
        r = subject_probe_within_env(merged, features, int(env), seed=int(seed))
        rows.append({
            "환경": ENV_LABELS.get(int(env), f"E{env}"),
            "balanced accuracy": r.get("balanced_accuracy"),
            "우연": r.get("chance"), "파일 수": r.get("n"), "피험자 수": r.get("n_subjects"),
            "비고": r.get("error") or "",
        })
    ctrl = pd.DataFrame(rows)
    st.dataframe(ctrl.round(3), use_container_width=True, hide_index=True)

    valid = ctrl.dropna(subset=["balanced accuracy"])
    if not valid.empty:
        lift = (valid["balanced accuracy"] / valid["우연"]).mean()
        st.warning(
            f"환경 내부 인물 식별은 우연 대비 평균 **{lift:.1f}배** 입니다. "
            "이 값이 높다면 개인차가 CSI 특징에 강하게 남아 있다는 뜻이며, "
            "§6.4 의 환경 분류 정확도 중 얼마가 '방'이고 얼마가 '사람'인지는 "
            "**이 데이터셋만으로는 원리적으로 분리할 수 없습니다.**"
        )

# --- 6.6 다중비교와 표본 크기 -------------------------------------------------

st.subheader("6.6 다중비교와 표본 크기")

if not table.empty:
    n_raw = int((table["ks_p"] < 0.05).sum())
    n_adj = int((table["ks_q"] < 0.05).sum())
    st.caption(
        f"총 **{len(table)}건**의 쌍별 검정 중 원 p<0.05 는 {n_raw}건, "
        f"BH-FDR 보정 후 q<0.05 는 **{n_adj}건**입니다. 지표들이 강하게 상관하므로"
        "(agc↔amp_mean↔q) Bonferroni 대신 BH 를 씁니다."
    )

curve_metric = st.selectbox(
    "표본 크기 곡선 지표", [m for m in all_metrics if m in merged.columns],
    index=([m for m in all_metrics].index(st.session_state["sm_curve_metric"])
           if st.session_state["sm_curve_metric"] in all_metrics else 0),
)
st.session_state["sm_curve_metric"] = curve_metric

envs_avail = sorted(merged["env"].dropna().unique())
if len(envs_avail) >= 2:
    pair = (envs_avail[0], envs_avail[1])
    sizes = [n for n in (10, 15, 20, 25, 30, 40, 50, 75, 100)
             if n <= merged.groupby("env").size().min()]
    if len(sizes) >= 2:
        curve = sample_size_curve(merged, curve_metric, "env", pair, sizes, n_boot=20, seed=int(seed))
        if not curve.empty:
            chart(plot_sample_size_curve(curve))
            d_range = curve["ks_d_mean"].max() - curve["ks_d_mean"].min()
            st.caption(
                f"E{pair[0]}–E{pair[1]}, `{curve_metric}`. n 을 {curve['n'].min()}→{curve['n'].max()} 로 "
                f"늘릴 때 KS D 는 {d_range:.3f} 만큼만 움직이지만 p 중앙값은 "
                f"{curve['p_median'].iloc[0]:.2g} → {curve['p_median'].iloc[-1]:.2g} 로 바뀝니다. "
                "**표본 크기 슬라이더는 p 를 만들지만 효과크기는 만들지 않습니다** — "
                "그래서 이 앱은 효과크기를 우선하고 p 를 각주로 둡니다."
            )
    else:
        st.info("표본이 작아 크기 곡선을 그릴 수 없습니다. 페이지 1에서 셀당 파일 수를 늘리세요.")

# --- 6.7 결론 ----------------------------------------------------------------

st.subheader("6.7 결론 (계산된 값 기반)")

bullets = []
if not table.empty:
    top = table.dropna(subset=["ks_d"]).nlargest(3, "ks_d")
    for _, r in top.iterrows():
        bullets.append(
            f"- `{r['metric']}` 는 E{r['group_a']}–E{r['group_b']} 에서 KS D=**{r['ks_d']:.3f}** "
            f"(Cliff's δ={r['cliffs_delta']:.3f}, {r['cliffs_magnitude']}, q={r['ks_q']:.3g})"
        )

if merged["fall_label"].nunique() >= 2:
    gaps = gap_comparison(merged, all_metrics)
    valid = gaps.dropna(subset=["env_gap", "class_gap"])
    if len(valid):
        bullets.append(
            f"- 지표 {len(valid)}개 중 **{int(valid['env_exceeds_class'].sum())}개**에서 "
            "환경 격차가 활동(낙상) 격차보다 크다."
        )

if features and not res.get("error"):
    bullets.append(
        f"- 파일 단위 특징만으로 환경을 balanced accuracy **{res['balanced_accuracy']:.3f}** "
        f"(우연 {res['chance']:.3f}) 로 맞힐 수 있다"
        + (f", 순열 p={res['perm_p']:.3f}." if res["perm_p"] is not None else ".")
    )

if bullets:
    st.markdown("\n".join(bullets))

st.error(
    "**단서(반드시 함께 읽을 것)**: E1=S1–10, E2=S11–20, E3=S21–30 으로 환경별 피험자 풀이 "
    "서로소이므로, 위의 모든 '환경 효과'는 실제로는 **(방 효과 + 인물 효과)** 이며 "
    "이 데이터셋만으로는 분리할 수 없습니다. §6.5 의 인물 식별 정확도가 그 크기의 상한을 시사합니다."
)

with st.expander("전체 발산 표"):
    st.dataframe(table.round(4), use_container_width=True)
    st.download_button("발산 표 CSV", table.to_csv(index=False).encode("utf-8-sig"),
                       file_name="domain_shift_divergence.csv", mime="text/csv")

with st.expander("파일별 통합 레코드"):
    st.dataframe(merged, use_container_width=True)
    st.download_button("통합 레코드 CSV", merged.to_csv(index=False).encode("utf-8-sig"),
                       file_name="domain_shift_records.csv", mime="text/csv")
