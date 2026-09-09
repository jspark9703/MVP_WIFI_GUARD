"""
P2. 수집 단계 시프트 — CSI 값을 하나도 보기 전에, 수집 자체가 환경별로 다른가.

값싼 pass(~25 ms/파일)이므로 소프트 게이트만 둔다. 이 페이지의 첫 섹션인 패킷 타이밍이
이 앱에서 가장 값싸면서 가장 결정적인 발견이기 때문에, 비싼 Run 버튼 뒤에 가두지 않는다.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from lib.constants import (
    ACQUISITION_METRICS,
    AGC_MANIFEST_REL,
    ANTENNA_LABELS,
    COST_META_SEC_PER_FILE,
    METRIC_DESCRIPTIONS,
    NOMINAL_GAP_MS,
)
from lib.cache import (
    get_file_index_cached,
    load_csv_artifact_cached,
    metadata_batch_cached,
    sample_cached,
)
from lib.divergence import add_fdr, categorical_js_matrix, cliffs_delta, kruskal_omnibus, pairwise_divergence_table
from lib.plotting import (
    plot_agc_sample_vs_full,
    plot_categorical_share_bar,
    plot_gap_histogram,
    plot_group_box,
    plot_group_ecdf,
    plot_grouped_box_2factor,
)
from lib.render import chart
from lib.sampling import composition_matched_reference
from lib.state_defaults import echo_shared_settings, meta_fingerprint, sampling_kwargs

st.title("2. 수집 단계 시프트")
st.caption(
    "CSI 값을 파싱하기 전에 링크 계층만 본다 — 패킷 타이밍 / AGC / RSSI. "
    "여기서 이미 환경 차이가 나온다면, 그 차이는 이후 모든 단계에 그대로 실려 간다."
)

repo_root = Path(st.session_state["repo_root"])
mendeley_root = st.session_state["mendeley_root"]
cache_dir = st.session_state["cache_dir"]

file_index = get_file_index_cached(mendeley_root)
sample = sample_cached(mendeley_root, **sampling_kwargs())

if sample.empty:
    st.warning("표본이 비어 있습니다. 페이지 1에서 표본을 설정하세요.")
    st.stop()

with st.sidebar:
    st.header("표본 (페이지 1에서 설정)")
    st.caption("여기서는 고정 — 바꾸려면 페이지 1로 가세요.")
    st.json(echo_shared_settings(), expanded=False)

    st.divider()
    st.header("스캔 범위")
    full_dataset = st.checkbox(
        "전체 9000 파일 스캔", value=bool(st.session_state["aq_full_dataset"]),
        help=f"전체 스캔은 약 {9000 * COST_META_SEC_PER_FILE / 60:.1f}분 걸립니다. "
             "표본만으로도 결론은 같지만, 전체를 돌리면 하위집단(예: 저레이트 파일)을 빠짐없이 셀 수 있습니다.",
    )
    run_full = st.button("전체 스캔 실행", type="primary", use_container_width=True) if full_dataset else False

st.session_state["aq_full_dataset"] = bool(full_dataset)

if full_dataset and not run_full and not st.session_state["aq_has_run"]:
    st.info("전체 데이터셋 스캔은 버튼을 눌러 실행하세요. (표본 결과를 보려면 체크박스를 해제하세요)")
    st.stop()

targets = file_index if (full_dataset and run_full) else sample
paths = targets["filepath_str"].tolist()

progress = st.progress(0.0, text="메타데이터 스캔 준비 중...")


def _on_progress(done: int, total: int, name: str) -> None:
    progress.progress(done / max(total, 1), text=f"[{done}/{total}] {name}")


meta, errors = metadata_batch_cached(
    paths, float(st.session_state["pp_fs_hz"]), cache_dir, meta_fingerprint(),
    progress_callback=_on_progress,
)
progress.empty()
st.session_state["aq_has_run"] = True

if meta.empty:
    st.error("메타데이터를 읽지 못했습니다.")
    st.stop()

meta = meta.merge(
    targets[["filepath_str", "env", "subject", "activity", "activity_name",
             "is_fall", "fall_label", "class_id", "trial"]],
    left_on="filepath", right_on="filepath_str", how="left",
).drop(columns=["filepath_str"])

if errors:
    with st.expander(f"⚠️ 건너뛴 파일 {len(errors)}개"):
        for line in errors:
            st.write(line)

m1, m2, m3 = st.columns(3)
m1.metric("스캔한 파일", f"{len(meta):,}")
m2.metric("환경", f"{meta['env'].nunique()}")
m3.metric("피험자", f"{meta['subject'].nunique()}")

# --- 2.1 패킷 타이밍 --------------------------------------------------------

st.subheader("2.1 패킷 타이밍 — 수집 자체가 환경별로 다르다")

chart(plot_gap_histogram(meta, "median_gap_ms", "env", log_x=True, nominal=NOMINAL_GAP_MS))

lowrate = meta[meta["is_lowrate"]]
rate_rows = []
for env in sorted(meta["env"].unique()):
    sub = meta[meta["env"] == env]
    low = sub[sub["is_lowrate"]]
    rate_rows.append({
        "환경": f"E{env}", "파일 수": len(sub),
        "저레이트 파일": len(low),
        "비율": f"{len(low) / max(len(sub), 1):.1%}",
        "해당 피험자": ", ".join(f"S{s:02d}" for s in sorted(low["subject"].dropna().unique()))
                      if len(low) else "—",
        "median gap 중앙값(ms)": round(float(sub["median_gap_ms"].median()), 4),
    })
st.dataframe(pd.DataFrame(rate_rows), use_container_width=True)

if len(lowrate):
    st.warning(
        f"**저레이트/비단조 타임스탬프 파일 {len(lowrate)}개 발견.** 이 파일들은 "
        "`resample_signal` 안에서 조용히 대량의 샘플을 **만들어냅니다**.\n\n"
        f"실제 사례 `E1_S03_C01_A01_T02.csv`: 958개 gap 중 **928개가 ≤ 0**(비단조)이고, "
        "살아남은 ~100.352 ms gap 은 리샘플러의 가드를 그대로 통과합니다 — "
        f"100.352 / {NOMINAL_GAP_MS} = 32.1 스텝 ≤ `max_interp_gap_steps=64` 이고, "
        f"|100.352 − 32×{NOMINAL_GAP_MS}| = 0.352 ms ≤ `tolerance_ms=2.0` 이기 때문입니다. "
        "결과적으로 실제 샘플 1개당 보간 샘플 31개가 생성되어, 그 파일 신호의 약 **49%가 선형 보간**입니다.\n\n"
        "선형 보간은 moving variance 를 평탄화하므로 **q 를 직접 편향시킵니다.** "
        "즉 수집 → 리샘플러 → q 로 이어지는 인과 사슬이 있고, 그것이 환경별로 다릅니다."
    )

t1, t2 = st.columns(2)
with t1:
    chart(plot_group_box(meta, "frac_nonpositive", "env", "비단조(≤0) gap 비율", "비율"))
    res = kruskal_omnibus({g: s["frac_nonpositive"] for g, s in meta.groupby("env")})
    st.caption(
        f"Kruskal H={res['h']:.2f}, p={res['p_value']:.3g}, ε²={res['eps_sq']:.3f}"
        if res["h"] is not None else "그룹이 부족해 검정할 수 없습니다."
    )
with t2:
    chart(plot_group_box(meta, "gap_cv", "env", "패킷 간격 변동계수", "CV"))
    res = kruskal_omnibus({g: s["gap_cv"] for g, s in meta.groupby("env")})
    st.caption(
        f"Kruskal H={res['h']:.2f}, p={res['p_value']:.3g}, ε²={res['eps_sq']:.3f}"
        if res["h"] is not None else "그룹이 부족해 검정할 수 없습니다."
    )

# --- 2.2 AGC ----------------------------------------------------------------

st.subheader("2.2 AGC")

a1, a2 = st.columns(2)
with a1:
    chart(plot_group_box(meta, "agc_mean", "env", "파일별 AGC 평균", "AGC"))
with a2:
    chart(plot_group_ecdf(meta, "agc_mean", "env", "AGC 평균 ECDF"))

agc_manifest = load_csv_artifact_cached(str(repo_root / AGC_MANIFEST_REL))
if not agc_manifest.empty and {"env", "activity", "agc_mean"} <= set(agc_manifest.columns):
    ref = composition_matched_reference(agc_manifest, meta, "agc_mean", "env", "activity")
    if not ref.empty:
        chart(
            plot_agc_sample_vs_full(
                dict(zip(ref["env"], ref["sample_mean"])),
                dict(zip(ref["env"], ref["reference_mean"])),
            ),
        )
        worst = float(ref["diff"].max())
        ok = worst < 1.0
        st.caption(
            f"표본 평균과 **동일 활동 구성으로 가중한** 전체 9000 파일 기준값의 최대 차이 "
            f"**{worst:.2f}** — "
            + ("표본이 전체를 잘 대표합니다 (기준: 1.0 미만)." if ok else
               "⚠️ 차이가 1.0 이상입니다. 셀당 파일 수를 늘리거나 seed 를 바꿔 확인하세요.")
            + "\n\n활동 구성을 맞추는 이유: 층화 표본은 낙상/비낙상을 50:50 으로 뽑지만 "
              "전체 데이터셋은 2:10 이고 AGC 는 활동마다 다릅니다(C3 걷기 41.4 vs C1 앉아서낙상 34.4). "
              "가중 없이 비교하면 **층화가 의도한 구성 차이를 샘플러 편향으로 오독**하게 됩니다. "
              "기준값은 `results/analysis/agc_distribution/agc_manifest.csv` (파일당 1행)에서 계산합니다."
        )
        st.dataframe(ref.round(3), use_container_width=True, hide_index=True)

chart(plot_grouped_box_2factor(meta, "agc_mean", "fall_label", "env", "AGC — 활동(낙상 여부) × 환경"))

# --- 2.3 RSSI ---------------------------------------------------------------

st.subheader("2.3 RSSI 와 실제 수신전력")
st.caption(
    "이 레포에서 RSSI 는 지금까지 분석된 적이 없습니다. **raw RSSI 만 보면 환경 차이가 거의 "
    "없지만**, AGC 가 크게 다르기 때문에 실제 수신전력은 다릅니다. Intel 5300 변환 "
    "`dBm = 10·log10(Σ10^(rssi_i/10)) − 44 − agc` 를 적용해야 시프트가 보입니다."
)

r1, r2 = st.columns(2)
with r1:
    rssi_means = meta.groupby("env")[["rssi_a_mean", "rssi_b_mean", "rssi_c_mean"]].mean().round(2)
    rssi_means.columns = [ANTENNA_LABELS[i] for i in range(3)]
    st.markdown("**raw RSSI 평균 (환경 차이가 거의 없음)**")
    st.dataframe(rssi_means, use_container_width=True)
with r2:
    st.markdown("**실제 수신전력 dBm (환경 차이가 드러남)**")
    st.dataframe(meta.groupby("env")[["rss_dbm_mean", "rss_dbm_std", "agc_mean"]]
                 .mean().round(2), use_container_width=True)

d1, d2 = st.columns(2)
with d1:
    chart(plot_group_box(meta, "rss_dbm_mean", "env", "실제 수신전력", "dBm"))
    res = kruskal_omnibus({g: s["rss_dbm_mean"] for g, s in meta.groupby("env")})
    st.caption(
        f"Kruskal H={res['h']:.2f}, p={res['p_value']:.3g}, ε²={res['eps_sq']:.3f}"
        if res["h"] is not None else "그룹이 부족해 검정할 수 없습니다."
    )
with d2:
    chart(
        plot_categorical_share_bar(meta, "rssi_best_ant", "env", "환경별 '가장 강한 안테나' 분포", ANTENNA_LABELS),
    )
    jsd = categorical_js_matrix(meta, "env", "rssi_best_ant")
    if not jsd.empty:
        st.caption("최강 안테나 분포 JSD: " + " · ".join(
            f"E{int(r.group_a)}–E{int(r.group_b)} {r.jsd:.3f}" for r in jsd.itertuples()
        ))

dead = meta["noise_is_dead"].all() if "noise_is_dead" in meta else False
st.caption(
    f"`noise` 컬럼은 파일당 unique 값 {int(meta['noise_n_unique'].max())}개"
    + (" — 전 데이터셋 −127 고정이라 분석에서 제외합니다 (죽은 컬럼임을 숨기지 않고 확인)."
       if dead else " — 값이 변합니다. 분석 대상에 넣을 수 있습니다.")
)

# --- 2.4 요약 ---------------------------------------------------------------

st.subheader("2.4 수집 지표 발산 요약")

available = [m for m in ACQUISITION_METRICS if m in meta.columns]
table = add_fdr(pairwise_divergence_table(meta, available, "env"))
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
    st.caption(
        "**분석 단위는 패킷이 아니라 파일입니다.** 패킷 단위로 풀링하면 env 당 368만 행이 되어 "
        "모든 p 가 0 이 되고 검정이 무의미해집니다. `ks_d` 는 그 자체로 효과크기(0~1)이고 "
        "지표 간 비교가 가능하며, `ks_q` 는 BH-FDR 보정값입니다."
    )

with st.expander("파일별 raw 메타데이터"):
    st.dataframe(meta, use_container_width=True)
    st.download_button("CSV 다운로드", meta.to_csv(index=False).encode("utf-8-sig"),
                       file_name="domain_shift_acquisition.csv", mime="text/csv")
