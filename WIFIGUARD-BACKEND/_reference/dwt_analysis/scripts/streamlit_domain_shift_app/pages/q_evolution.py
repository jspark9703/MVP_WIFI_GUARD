"""
P4. amfall 단계별 q-value — 이 앱의 핵심.

섹션 순서가 곧 파이프라인 순서다. 프로덕션 select_streams/select_pcs 가 버리는
값(비선택 후보의 q, 고유값 스펙트럼, q(p), Q(p), PC 임계)을 src/dwt_coef/q_diagnostics.py
가 복원해 주기 때문에 각 단계를 실제로 들여다볼 수 있다.
"""

from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from lib.constants import (
    ANTENNA_LABELS,
    COST_PROBE_SEC_PER_FILE,
    ENV_LABELS,
    METRIC_DESCRIPTIONS,
    PC_METRICS,
    PIPELINE_MAP,
    Q_METRICS,
    STAGE_LABELS,
    STAGE_ORDER,
)
from lib.cache import (
    probe_batch_cached,
    probe_cache_status,
    sample_cached,
    single_file_stages_cached,
    verify_file_cached,
)
from lib.divergence import (
    add_fdr,
    categorical_js_matrix,
    cliffs_delta,
    kruskal_omnibus,
    pairwise_divergence_table,
)
from lib.plotting import (
    plot_eigen_spectrum,
    plot_group_box,
    plot_pc_q_bar,
    plot_q_bar,
    plot_q_landscape,
    plot_q_landscape_group_mean,
    plot_selection_heatmap,
    plot_stage_gap_bars,
    plot_stage_q_lines,
    plot_stage_signals,
    plot_categorical_share_bar,
)
from lib.render import chart
from lib.stage_probe import stage_rows
from lib.state_defaults import (
    params_fingerprint,
    probe_kwargs,
    reset_amfall_params,
    sampling_kwargs,
)

st.title("4. amfall 단계별 q-value")
st.caption(
    "q(h) = max(moving variance) / mean(moving variance). amfall 은 이 값으로 스트림과 PC 를 "
    "고른다. 파이프라인을 지나며 환경 격차가 **줄어드는지 커지는지**가 이 페이지의 질문이다."
)

mendeley_root = st.session_state["mendeley_root"]
cache_dir = st.session_state["cache_dir"]

sample = sample_cached(mendeley_root, **sampling_kwargs())
if sample.empty:
    st.warning("표본이 비어 있습니다. 페이지 1에서 표본을 설정하세요.")
    st.stop()

paths = sample["filepath_str"].tolist()

# --- 사이드바: amfall 파라미터 ----------------------------------------------

with st.sidebar:
    st.header("amfall 파라미터")
    st.caption("초기값은 config/preprocess.yaml 과 동일합니다.")

    if st.button("config/preprocess.yaml 값으로 되돌리기", use_container_width=True):
        reset_amfall_params()
        st.rerun()

    segment_mode = st.radio(
        "구간 모드", ["first_window", "whole_file"],
        index=["first_window", "whole_file"].index(st.session_state["pp_segment_mode"]),
        help="first_window: preprocess_segment 와 동일하게 int(fs×window_sec)=800 샘플로 "
             "절단/패딩 → DVC 파이프라인 수치와 직접 비교 가능. "
             "whole_file: 4초 전체 사용 → 통계적으로 더 안정적.",
    )
    window_sec = st.number_input("window_sec", 0.5, 4.0,
                                 float(st.session_state["pp_window_sec"]), step=0.5)

    st.divider()
    st.subheader("리샘플")
    fs_hz = st.number_input("fs_hz", 32.0, 1000.0, float(st.session_state["pp_fs_hz"]), step=10.0)
    tolerance_ms = st.number_input("tolerance_ms", 0.1, 20.0,
                                   float(st.session_state["pp_tolerance_ms"]), step=0.1)
    max_gap = st.number_input("max_interp_gap_steps", 1, 512,
                              int(st.session_state["pp_max_interp_gap_steps"]), step=1)

    st.divider()
    st.subheader("대역통과")
    low_hz = st.number_input("low_hz", 0.01, 50.0, float(st.session_state["pp_low_hz"]), step=0.1)
    high_hz = st.number_input("high_hz", 1.0, 159.0, float(st.session_state["pp_high_hz"]), step=1.0)
    order = st.number_input("filter_order", 1, 10, int(st.session_state["pp_filter_order"]), step=1)

    st.divider()
    st.subheader("스트림 / PC 선택")
    omega = st.number_input("omega (MV 반창폭)", 4, 512, int(st.session_state["pp_omega"]), step=4,
                            help=f"MV 창 = 2ω+1 샘플. ω=160 이면 321 샘플 ≈ 1.0초 (fs=320 기준).")
    n_streams = st.number_input("n_streams", 1, 90, int(st.session_state["pp_n_streams"]), step=1)
    cur_ant = st.session_state["pp_use_ant"] or (0, 1, 2)
    ant_labels = st.multiselect(
        "사용 안테나", [1, 2, 3], default=[a + 1 for a in cur_ant],
        help="config 기본값은 [2, 3] (0-based [1,2]) 이라 안테나 1을 버립니다.",
    )
    max_pcs = st.number_input("max_pcs", 1, 10, int(st.session_state["pp_max_pcs"]), step=1)

prev_params = params_fingerprint()

st.session_state.update({
    "pp_segment_mode": segment_mode, "pp_window_sec": float(window_sec),
    "pp_fs_hz": float(fs_hz), "pp_tolerance_ms": float(tolerance_ms),
    "pp_max_interp_gap_steps": int(max_gap),
    "pp_low_hz": float(low_hz), "pp_high_hz": float(high_hz), "pp_filter_order": int(order),
    "pp_omega": int(omega), "pp_n_streams": int(n_streams),
    "pp_use_ant": tuple(a - 1 for a in sorted(ant_labels)) if ant_labels else None,
    "pp_max_pcs": int(max_pcs),
})

if params_fingerprint() != prev_params:
    st.session_state["qv_has_run"] = False
    st.session_state["cp_has_run"] = False

if high_hz >= fs_hz / 2:
    st.error(f"high_hz({high_hz})는 나이퀴스트 주파수({fs_hz / 2:.1f} Hz)보다 작아야 합니다.")
    st.stop()

pkwargs = probe_kwargs()

# --- 4.0 파이프라인 지도 -----------------------------------------------------

st.subheader("4.0 파이프라인 지도")
st.caption("**무엇이 버려지는가** 열이 이 페이지가 존재하는 이유다 — 프로덕션 반환값만으로는 볼 수 없는 것들.")
st.dataframe(
    pd.DataFrame(PIPELINE_MAP, columns=["단계", "함수", "지배 파라미터", "측정 대상", "무엇이 버려지는가"]),
    use_container_width=True, hide_index=True,
)
st.caption(
    "버려진 값은 `src/dwt_coef/q_diagnostics.py` 가 복원한다. q 커널(`_compute_q`)은 "
    "프로덕션에서 그대로 import 하고 선택 부기만 재작성했으며, §4.6 에서 두 구현의 등가성을 확인한다. "
    "`src/dwt_coef/preprocessing.py` 는 DVC `preprocess` 스테이지의 dep 이라 수정하지 않았다."
)

# --- 게이트 ------------------------------------------------------------------

status = probe_cache_status(cache_dir, params_fingerprint(), paths)
st.caption(
    f"표본 **{len(paths)}** 개 파일 · 캐시 적중 **{status['cached']}** / 계산 필요 "
    f"**{status['missing']}** → 예상 **{status['missing'] * COST_PROBE_SEC_PER_FILE:.0f}초** "
    f"(params_hash `{status['params_hash']}`)"
)

if st.button("단계별 q 계산", type="primary", use_container_width=True):
    st.session_state["qv_has_run"] = True

if not st.session_state["qv_has_run"]:
    st.info("사이드바에서 amfall 파라미터를 확인한 뒤 위 버튼을 눌러 실행하세요. "
            "페이지 3을 이미 실행했다면 대부분 캐시 적중입니다.")
    st.stop()

progress = st.progress(0.0, text="CSI 파싱 준비 중...")


def _on_progress(done: int, total: int, name: str) -> None:
    progress.progress(done / max(total, 1), text=f"[{done}/{total}] {name}")


records, q_long, sub_long, pc_long, errors = probe_batch_cached(
    paths, cache_dir, pkwargs, progress_callback=_on_progress,
)
progress.empty()

if records.empty:
    st.error("프로브 결과가 비어 있습니다.")
    st.stop()

if errors:
    with st.expander(f"⚠️ 건너뛴 파일 {len(errors)}개"):
        for line in errors:
            st.write(line)

# --- 4.1 한 파일 따라가기 ----------------------------------------------------

st.subheader("4.1 한 파일 따라가기")

names = records.sort_values(["env", "subject", "activity", "trial"])
options = names["filepath"].tolist()
labels = dict(zip(names["filepath"], names["name"] + "  (" + names["fall_label"] + ")"))

default_idx = 0
if st.session_state["qv_focus_file"] in options:
    default_idx = options.index(st.session_state["qv_focus_file"])
focus = st.selectbox("파일 선택", options, index=default_idx, format_func=lambda p: labels.get(p, p))
st.session_state["qv_focus_file"] = focus

stages = single_file_stages_cached(focus, **pkwargs)
sig = stages["signals"]
rec = stages["record"]

k1, k2, k3, k4 = st.columns(4)
k1.metric("S2 q 최대", f"{rec['q_filt_max']:.3f}")
k2.metric("S3 선택 스트림", f"{rec['selected_stream_count']} / {rec['candidate_stream_count']}")
k3.metric("S4 q(p) 최대", f"{rec['q_pc_max']:.3f}",
          delta=f"{rec['q_pc_over_q_stream']:.2f}× vs S2")
k4.metric("S5 최종 q", f"{rec['final_q']:.3f}")

chart(plot_stage_signals(sig))
st.caption(
    f"2행의 검은 점(●)이 **원본 패킷이 놓인 위치**다. 점 사이가 넓을수록 그 구간은 리샘플러가 "
    f"만들어낸 선형 보간이다. 이 파일: 원본 {rec['raw_packets']}개 → 리샘플 "
    f"{rec['resampled_samples']}개, 보간 {rec['interp_steps']}개 "
    f"(**{rec['interp_ratio']:.1%}가 조작된 샘플**), 폴백 {rec['fallback_steps']}개, "
    f"비단조 gap {rec['nonpositive_gaps']}개."
)

lm1, lm2 = st.columns(2)
zmax = float(np.nanmax([np.nanmax(sig["q_res_map"]), np.nanmax(sig["q_filt_map"])]))
with lm1:
    chart(plot_q_landscape(sig["q_res_map"], "S1 리샘플 후 q landscape", 0.0, zmax))
with lm2:
    chart(plot_q_landscape(sig["q_filt_map"], "S2 대역통과 후 q landscape", 0.0, zmax))
st.caption("두 히트맵은 같은 색 범위를 공유한다 — 필터가 q 를 얼마나 끌어올렸는지 직접 비교 가능.")

chart(
    plot_q_bar(sig["q_top"], sig["Q_top"], sig["stream_q_threshold"], sig["selected_count"], sig["top_origins"]),
)
st.caption(
    "Q-임계는 **q 내림차순으로 정렬된** 배열에 상수로 적용되므로 선택 집합은 항상 앞쪽 prefix 다. "
    "따라서 막대의 선택 여부는 `i < selected_stream_count` 로 판정한다 "
    "(풀 인덱스인 `selected_stream_indices` 로 판정하면 인덱스 공간이 달라 틀린다)."
)

p1, p2 = st.columns(2)
with p1:
    chart(plot_eigen_spectrum(sig["eigenvalues"], sig["eig_threshold"]))
with p2:
    sel = set(int(i) for i in sig["pc_selected_indices"])
    chart(
        plot_pc_q_bar(sig["q_pc"], sig["Q_pc"], sig["pc_q_threshold"], [int(i) in sel for i in sig["pc_candidate_indices"]]),
    )
st.caption(
    f"PC 임계는 1/(n_c−1) = {sig['pc_q_threshold']:.3f} 이다 (스트림의 1/n 과 분모가 다르다). "
    f"후보 {rec['pc_candidate_count']}개 중 {rec['pc_selected_count']}개 선택"
    + (", max_pcs 상한에 걸림" if rec["pc_capped"] else "") + "."
)

# --- 4.2 스테이지별 q 변화 ---------------------------------------------------

st.subheader("4.2 스테이지별 q 변화 (배치)")

stage_df = pd.DataFrame([r for _, row in records.iterrows() for r in stage_rows(row.to_dict())])

split = st.checkbox("낙상 / 비낙상으로 나눠 보기", value=bool(st.session_state["qv_split_by_fall"]))
st.session_state["qv_split_by_fall"] = bool(split)

chart(plot_stage_q_lines(stage_df, "env"))

if split and stage_df["fall_label"].nunique() > 1:
    fcols = st.columns(2)
    for col, lbl in zip(fcols, ["Fall", "Non-fall"]):
        with col:
            chart(plot_stage_q_lines(stage_df[stage_df["fall_label"] == lbl], "env"))
            st.caption(f"{lbl} 만")

# 단계별 환경 격차
gap_rows = []
envs = sorted(stage_df["env"].dropna().unique())
for s in STAGE_ORDER:
    sub = stage_df[stage_df["stage"] == s]
    if sub.empty:
        continue
    deltas = []
    for ga, gb in combinations(envs, 2):
        d = cliffs_delta(sub.loc[sub["env"] == ga, "q"], sub.loc[sub["env"] == gb, "q"])["delta"]
        if d is not None:
            deltas.append(abs(d))
    res = kruskal_omnibus({g: t["q"] for g, t in sub.groupby("env")})
    gap_rows.append({"stage": s, "단계": STAGE_LABELS.get(s, s),
                     "env_gap": max(deltas) if deltas else np.nan,
                     "eps_sq": res["eps_sq"], "p_value": res["p_value"]})
gap_df = pd.DataFrame(gap_rows)

chart(plot_stage_gap_bars(gap_df))

if not gap_df.empty and gap_df["env_gap"].notna().any():
    first = gap_df["env_gap"].dropna().iloc[0]
    last = gap_df["env_gap"].dropna().iloc[-1]
    trend = "커집니다" if last > first else ("줄어듭니다" if last < first else "거의 변하지 않습니다")
    st.caption(
        f"환경 격차가 첫 단계 |δ|={first:.3f} 에서 마지막 단계 |δ|={last:.3f} 로 **{trend}**. "
        "격차가 커진다면 amfall 전처리가 도메인 시프트를 완화하는 게 아니라 증폭한다는 뜻이다."
    )
    st.dataframe(add_fdr(gap_df[["단계", "env_gap", "eps_sq", "p_value"]], "p_value", "p_adj")
                 .round({"env_gap": 3, "eps_sq": 3}), use_container_width=True, hide_index=True)

# --- 4.3 landscape 모양 ------------------------------------------------------

st.subheader("4.3 q landscape 의 모양")

chart(plot_q_landscape_group_mean(q_long, "env"))

s1, s2, s3 = st.columns(3)
for col, metric, title in (
    (s1, "q_entropy_norm", "정규화 엔트로피 (평탄도)"),
    (s2, "q_gini", "지니계수 (집중도)"),
    (s3, "selected_stream_count", "선택된 스트림 수"),
):
    with col:
        chart(plot_group_box(records, metric, "env", title, metric))
        res = kruskal_omnibus({g: t[metric] for g, t in records.groupby("env")})
        st.caption(f"H={res['h']:.2f}, p={res['p_value']:.3g}, ε²={res['eps_sq']:.3f}"
                   if res["h"] is not None else "그룹 부족")

st.caption(
    "landscape 가 평탄할수록(엔트로피↑, 지니↓) 1/n_top 임계를 통과하는 스트림이 많아진다. "
    "세 지표가 같은 방향으로 움직이는지 확인하면 선택 개수 차이가 우연이 아님을 알 수 있다."
)

# --- 4.4 선택 결과 -----------------------------------------------------------

st.subheader("4.4 선택 결과 — 환경마다 다른 스트림을 고르는가")

selected = q_long[q_long["selected"]] if "selected" in q_long.columns else pd.DataFrame()
if selected.empty:
    st.info("선택된 스트림 정보가 없습니다.")
else:
    c1, c2 = st.columns(2)
    with c1:
        chart(
            plot_categorical_share_bar(selected, "antenna", "env", "환경별 선택 안테나 점유율", ANTENNA_LABELS),
        )
        jsd = categorical_js_matrix(selected, "env", "antenna")
        if not jsd.empty:
            st.caption("안테나 분포 JSD: " + " · ".join(
                f"E{int(r.group_a)}–E{int(r.group_b)} {r.jsd:.3f}" for r in jsd.itertuples()))
    with c2:
        chart(plot_selection_heatmap(selected, "env"))
        jsd_sub = categorical_js_matrix(selected, "env", "subcarrier",
                                        categories=list(range(30)))
        if not jsd_sub.empty:
            st.caption("서브캐리어 분포 JSD: " + " · ".join(
                f"E{int(r.group_a)}–E{int(r.group_b)} {r.jsd:.3f}" for r in jsd_sub.itertuples()))
            st.caption(
                "30빈 × 표본 수에서는 카이제곱의 기대도수≥5 가정이 깨지므로 JSD(base 2, 0~1)를 쓴다."
            )

    if selected["fall_label"].nunique() > 1:
        with st.expander("낙상 / 비낙상으로 나눈 선택 분포"):
            fc = st.columns(2)
            for col, lbl in zip(fc, ["Fall", "Non-fall"]):
                with col:
                    chart(plot_selection_heatmap(selected[selected["fall_label"] == lbl], "env"))
                    st.caption(f"{lbl} 만")

# --- 4.5 PCA 단계 ------------------------------------------------------------

st.subheader("4.5 PCA 단계 — PCA 가 활동 신호를 살리는가 죽이는가")

pc1, pc2, pc3 = st.columns(3)
for col, metric, title in (
    (pc1, "eig_top1_ratio", "최대 고유값 분산 설명비"),
    (pc2, "eig_effective_rank", "고유값 유효 랭크"),
    (pc3, "q_pc_over_q_stream", "q(p) 최대 / q(h) 최대"),
):
    with col:
        chart(plot_group_box(records, metric, "env", title, metric))
        res = kruskal_omnibus({g: t[metric] for g, t in records.groupby("env")})
        st.caption(f"H={res['h']:.2f}, p={res['p_value']:.3g}, ε²={res['eps_sq']:.3f}"
                   if res["h"] is not None else "그룹 부족")

st.caption(
    "`q_pc_over_q_stream` 이 1 보다 크면 PCA 가 활동 신호를 **증폭**했다는 뜻이고, "
    "1 보다 작으면 스트림 합성 과정에서 신호를 잃었다는 뜻이다. 이 비율이 환경별로 다르면 "
    "PCA 단계 자체가 도메인 시프트의 원천이 된다."
)

pd1, pd2 = st.columns(2)
with pd1:
    chart(plot_group_box(records, "pc_selected_count", "env", "선택된 PC 수", "개수"))
with pd2:
    chart(plot_group_box(records, "pc_candidate_count", "env", "후보 PC 수", "개수"))

# --- 4.6 검증 ----------------------------------------------------------------

st.subheader("4.6 검증 — 재작성본 ≡ 프로덕션")

with st.expander("등가성 확인 실행 (파일 N개)"):
    st.caption(
        "이 앱은 `select_streams`/`select_pcs` 의 선택 부기를 재작성해 버려진 값을 복원한다. "
        "재작성이 원본과 **정확히 같은 선택**을 하는지 실제 파일로 확인한다. "
        "중복 구현을 숨기지 않고 감사 가능하게 만드는 장치다."
    )
    n_verify = st.slider("확인할 파일 수", 3, min(30, len(paths)), min(10, len(paths)))
    if st.button("등가성 확인", use_container_width=True):
        vrows = []
        vbar = st.progress(0.0, text="확인 중...")
        subset = (records.groupby("env", group_keys=False)
                  .head(max(1, n_verify // max(records["env"].nunique(), 1)))["filepath"]
                  .tolist()[:n_verify])
        for i, fp in enumerate(subset):
            vbar.progress((i + 1) / len(subset), text=f"[{i+1}/{len(subset)}]")
            vrows.append(verify_file_cached(fp, **pkwargs))
        vbar.empty()

        vdf = pd.DataFrame(vrows)
        n_ok = int(vdf["all_match"].sum())
        if n_ok == len(vdf):
            st.success(
                f"✅ {n_ok}/{len(vdf)} 파일에서 로컬 재작성본과 프로덕션이 **완전히 일치**합니다 "
                f"(최대 q 차이 {vdf['max_abs_q_diff'].max():.2e}). 선택 집합이 prefix 라는 "
                "불변식도 모든 파일에서 성립합니다."
            )
        else:
            st.error(f"❌ {len(vdf) - n_ok}개 파일에서 불일치. 아래 표를 확인하세요.")
        st.dataframe(vdf, use_container_width=True)

# --- 발산 요약 ---------------------------------------------------------------

st.subheader("4.7 q / PC 지표 발산 요약")

available = [m for m in (Q_METRICS + PC_METRICS) if m in records.columns]
table = add_fdr(pairwise_divergence_table(records, available, "env"))
if table.empty:
    st.info("비교할 그룹이 부족합니다.")
else:
    show = table.copy()
    show["지표"] = show["metric"].map(lambda m: f"{m} — {METRIC_DESCRIPTIONS.get(m, '')}")
    show["쌍"] = "E" + show["group_a"].astype(str) + "–E" + show["group_b"].astype(str)
    st.dataframe(
        show[["지표", "쌍", "n_a", "n_b", "median_a", "median_b",
              "ks_d", "cliffs_delta", "cliffs_magnitude", "ks_p", "ks_q"]]
        .round({"median_a": 3, "median_b": 3, "ks_d": 3, "cliffs_delta": 3}),
        use_container_width=True,
    )

with st.expander("q_long (후보 스트림별 q)"):
    st.dataframe(q_long.head(2000), use_container_width=True)
    st.caption(f"총 {len(q_long):,} 행 중 앞 2000행. `selected` 는 prefix 규칙으로 계산됨.")

with st.expander("pc_long (후보 PC별 고유값/q)"):
    st.dataframe(pc_long.head(2000), use_container_width=True)
    st.caption(f"총 {len(pc_long):,} 행 중 앞 2000행. 프로덕션이 버리는 값들이다.")
