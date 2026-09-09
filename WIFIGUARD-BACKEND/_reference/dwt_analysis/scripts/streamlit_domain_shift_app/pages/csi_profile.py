"""
P3. CSI 진폭 프로파일 — 진폭 통계와 서브캐리어 지문이 환경별로 다른가.

비싼 pass(~0.6 s/파일, 대부분 complex 문자열 파싱)이므로 하드 게이트를 둔다.
P4·P5 와 **동일한 per-file 결과**를 공유하므로, 여기서 한 번 돌리면 둘 다 즉시 열린다.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from lib.constants import (
    AMPLITUDE_METRICS,
    ANTENNA_LABELS,
    COST_PROBE_SEC_PER_FILE,
    ENV_LABELS,
    METRIC_DESCRIPTIONS,
)
from lib.cache import (
    get_file_index_cached,
    metadata_batch_cached,
    probe_batch_cached,
    probe_cache_status,
    sample_cached,
)
from lib.divergence import add_fdr, kruskal_omnibus, pairwise_divergence_table
from lib.plotting import (
    plot_antenna_amplitude_share,
    plot_group_box,
    plot_group_ecdf,
    plot_scatter_with_fit,
    plot_subcarrier_profile,
)
from lib.render import chart
from lib.state_defaults import (
    echo_shared_settings,
    meta_fingerprint,
    params_fingerprint,
    probe_kwargs,
    sampling_kwargs,
)

st.title("3. CSI 진폭 프로파일")
st.caption(
    "복소 CSI 를 실제로 파싱해 진폭 통계를 본다. 핵심 질문: 환경별 진폭 차이가 "
    "**단순한 이득(AGC) 차이**인가, 아니면 방마다 다른 **구조적 주파수 응답**인가."
)

mendeley_root = st.session_state["mendeley_root"]
cache_dir = st.session_state["cache_dir"]

sample = sample_cached(mendeley_root, **sampling_kwargs())
if sample.empty:
    st.warning("표본이 비어 있습니다. 페이지 1에서 표본을 설정하세요.")
    st.stop()

paths = sample["filepath_str"].tolist()
pkwargs = probe_kwargs()

with st.sidebar:
    st.header("표본 / 파라미터")
    st.caption("표본은 페이지 1, amfall 파라미터는 페이지 4에서 설정합니다.")
    st.json(echo_shared_settings(), expanded=False)

    st.divider()
    st.header("표시")
    ant_choice = st.selectbox(
        "서브캐리어 프로파일 안테나", ["전체", "Ant1", "Ant2", "Ant3"],
        index=["전체", "Ant1", "Ant2", "Ant3"].index(st.session_state["cp_antenna"])
        if st.session_state["cp_antenna"] in ["전체", "Ant1", "Ant2", "Ant3"] else 0,
    )
    value_choice = st.selectbox(
        "프로파일 값", ["amp_mean", "amp_std", "amp_cv", "q_filt_map"],
        index=["amp_mean", "amp_std", "amp_cv", "q_filt_map"].index(st.session_state["cp_value"])
        if st.session_state["cp_value"] in ["amp_mean", "amp_std", "amp_cv", "q_filt_map"] else 0,
    )

st.session_state.update({"cp_antenna": ant_choice, "cp_value": value_choice})

status = probe_cache_status(cache_dir, params_fingerprint(), paths)
st.caption(
    f"표본 **{len(paths)}** 개 파일 · 캐시 적중 **{status['cached']}** / 계산 필요 "
    f"**{status['missing']}** → 예상 **{status['missing'] * COST_PROBE_SEC_PER_FILE:.0f}초** "
    f"(params_hash `{status['params_hash']}`)"
)

if st.button("CSI 프로파일 계산", type="primary", use_container_width=True):
    st.session_state["cp_has_run"] = True

if not st.session_state["cp_has_run"]:
    st.info("사이드바에서 설정을 확인한 뒤 위 버튼을 눌러 실행하세요.")
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
    if errors:
        st.write(errors[:10])
    st.stop()

if errors:
    with st.expander(f"⚠️ 건너뛴 파일 {len(errors)}개"):
        for line in errors:
            st.write(line)

# AGC 는 값싼 pass 에 있으므로 붙여와서 '진폭 vs 이득' 비교를 가능하게 한다.
meta, _ = metadata_batch_cached(paths, float(st.session_state["pp_fs_hz"]),
                                cache_dir, meta_fingerprint())
if not meta.empty:
    records = records.merge(meta[["filepath", "agc_mean", "rss_dbm_mean"]],
                            on="filepath", how="left")

m1, m2, m3 = st.columns(3)
m1.metric("분석 파일", f"{len(records):,}")
m2.metric("환경", f"{records['env'].nunique()}")
m3.metric("피험자", f"{records['subject'].nunique()}")

# --- 3.1 진폭 레벨 -----------------------------------------------------------

st.subheader("3.1 진폭 레벨")

c1, c2 = st.columns(2)
with c1:
    chart(plot_group_box(records, "amp_mean", "env", "CSI 진폭 평균", "진폭"))
    res = kruskal_omnibus({g: s["amp_mean"] for g, s in records.groupby("env")})
    st.caption(f"Kruskal H={res['h']:.2f}, p={res['p_value']:.3g}, ε²={res['eps_sq']:.3f}"
               if res["h"] is not None else "그룹이 부족해 검정할 수 없습니다.")
with c2:
    chart(plot_group_ecdf(records, "amp_mean", "env", "CSI 진폭 평균 ECDF"))

c3, c4 = st.columns(2)
with c3:
    chart(plot_group_box(records, "amp_cv", "env", "진폭 변동계수", "CV"))
with c4:
    chart(plot_group_box(records, "amp_sub_std_mean", "env", "서브캐리어 간 진폭 산포", "std"))

if "agc_mean" in records.columns and records["agc_mean"].notna().any():
    st.markdown("**진폭 vs 이득 — 시프트가 단순 AGC 차이인가?**")
    chart(plot_scatter_with_fit(records, "agc_mean", "amp_mean", "env", "파일별 진폭 평균 vs AGC 평균"))
    st.caption(
        "진폭은 AGC 적용 **이후** 값이다. 환경별 점 구름이 하나의 공통 직선 위에 놓이면 "
        "진폭 차이는 이득 차이의 결과일 뿐이고, 환경별로 **기울기나 절편이 어긋나면** "
        "이득으로 설명되지 않는 구조적 차이(방의 다중경로/페이딩)가 있다는 뜻이다."
    )

# --- 3.2 서브캐리어 프로파일 -------------------------------------------------

st.subheader("3.2 서브캐리어 프로파일 — 각 방의 주파수 지문")

if sub_long.empty:
    st.info("서브캐리어 데이터가 없습니다.")
else:
    if ant_choice == "전체":
        cols = st.columns(3)
        for i, col in enumerate(cols):
            with col:
                chart(plot_subcarrier_profile(sub_long, value_choice, "env", antenna=i))
    else:
        ant_idx = {"Ant1": 0, "Ant2": 1, "Ant3": 2}[ant_choice]
        chart(plot_subcarrier_profile(sub_long, value_choice, "env", antenna=ant_idx, height=420))
    st.caption(
        "선은 중앙값, 밴드는 IQR. 환경별 곡선의 **모양**(어느 서브캐리어가 깊게 패이는가)이 "
        "다르면 그것이 주파수 선택적 페이딩 지문이며, 수직 이동만 있다면 이득 차이다."
    )

# --- 3.3 안테나 균형 ---------------------------------------------------------

st.subheader("3.3 안테나 균형")

a1, a2 = st.columns(2)
with a1:
    chart(plot_antenna_amplitude_share(records, "env"))
with a2:
    chart(plot_group_box(records, "amp_ant_ratio_21", "env", "안테나2 / 안테나1 진폭비", "비율"))

use_ant = pkwargs.get("use_ant")
if use_ant:
    dropped = [ANTENNA_LABELS[i] for i in range(3) if i not in use_ant]
    if dropped:
        st.caption(
            f"현재 amfall 설정은 `use_ant={list(use_ant)}` 이라 **{', '.join(dropped)} 를 버립니다** "
            "(config/preprocess.yaml 기본값). 위 점유율이 환경별로 다르다면, 이 고정 선택은 "
            "환경마다 다른 양의 정보를 버리는 셈입니다 — P4 §4.4 에서 선택 결과로 이어집니다."
        )

# --- 3.4 활동 vs 환경 --------------------------------------------------------

st.subheader("3.4 활동 vs 환경 — 방 효과와 행동 효과 분리")

if sub_long.empty or sub_long["fall_label"].nunique() < 2:
    st.info("낙상/비낙상이 모두 포함된 표본이 아니라 이 비교를 건너뜁니다.")
else:
    envs = sorted(sub_long["env"].dropna().unique())
    cols = st.columns(len(envs))
    ant_idx = None if ant_choice == "전체" else {"Ant1": 0, "Ant2": 1, "Ant3": 2}[ant_choice]
    for col, env in zip(cols, envs):
        with col:
            chart(
                plot_subcarrier_profile(
                    sub_long[sub_long["env"] == env], value_choice, "fall_label",
                    antenna=ant_idx, title=f"{ENV_LABELS.get(int(env), env)} 내부",
                    height=340,
                ),
            )
    st.caption(
        "각 환경 **내부에서** 낙상/비낙상을 나눈 것이다. 환경 간 곡선 차이가 "
        "환경 내부의 낙상/비낙상 차이보다 크면, 도메인 격차가 클래스 신호를 덮는다는 뜻이다 "
        "(P6 §6.2 에서 정량화)."
    )

# --- 3.5 발산 요약 -----------------------------------------------------------

st.subheader("3.5 진폭 지표 발산 요약")

available = [m for m in AMPLITUDE_METRICS if m in records.columns]
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

with st.expander("파일별 진폭 레코드"):
    amp_cols = ["name", "env", "subject", "activity_name", "fall_label"] + available
    st.dataframe(records[[c for c in amp_cols if c in records.columns]],
                 use_container_width=True)

with st.expander("서브캐리어 long 프레임"):
    st.dataframe(sub_long.head(2000), use_container_width=True)
    st.caption(f"총 {len(sub_long):,} 행 중 앞 2000행")
