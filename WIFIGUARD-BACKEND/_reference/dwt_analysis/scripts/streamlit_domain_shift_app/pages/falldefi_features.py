"""
P5. FallDeFi 피처의 환경 격차 vs 활동 격차.

FallDeFi (Palipana et al., IMWUT 2017) 는 Table 1 에서 피처를 두 묶음으로 나눈다:
같은 환경에서 정확도가 가장 높았던 **Original Features (OF)** 전체와, 저자들이
**환경이 바뀌어도 강건하다**고 보고한 부분집합 **Selected Features (SF)**.

이 페이지는 그 주장을 Mendeley 로 검증한다: SF 가 정말 OF 보다 환경 격차가 작은가,
그리고 그 격차가 활동(낙상) 격차보다 작은가.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from lib.constants import (
    COST_PROBE_SEC_PER_FILE,
    FALLDEFI_OF_METRICS,
    FALLDEFI_OF_ONLY_METRICS,
    FALLDEFI_SF_METRICS,
    METRIC_DESCRIPTIONS,
)
from lib.cache import (
    probe_batch_cached,
    probe_cache_status,
    sample_cached,
    single_file_stages_cached,
)
from lib.divergence import add_fdr, gap_comparison, kruskal_omnibus, pairwise_divergence_table
from lib.plotting import (
    plot_falldefi_spectrogram,
    plot_group_box,
    plot_grouped_box_2factor,
    plot_of_sf_gap_scatter,
    plot_of_sf_summary_bar,
    plot_power_burst_curve,
)
from lib.render import chart
from lib.state_defaults import params_fingerprint, probe_kwargs, sampling_kwargs
from src.dwt_coef.falldefi_features import ENTROPY_BANDS, PBC_HIGH_HZ, PBC_LOW_HZ

st.title("5. FallDeFi 피처 — 환경 격차 vs 활동 격차")
st.caption(
    "FallDeFi (Palipana et al., IMWUT 2017) Table 1 의 피처를 Mendeley 에 그대로 적용한다. "
    "핵심 질문: 저자들이 **환경 강건**하다고 지목한 Selected Features 가 이 데이터셋에서도 "
    "실제로 환경 격차가 작은가?"
)

mendeley_root = st.session_state["mendeley_root"]
cache_dir = st.session_state["cache_dir"]

sample = sample_cached(mendeley_root, **sampling_kwargs())
if sample.empty:
    st.warning("표본이 비어 있습니다. 페이지 1에서 표본을 설정하세요.")
    st.stop()

paths = sample["filepath_str"].tolist()

# --- 사이드바 ---------------------------------------------------------------

with st.sidebar:
    st.header("FallDeFi 파라미터")
    st.caption("논문 기본값을 Mendeley 320 Hz 에 맞춘 값입니다.")

    pc_mode = st.radio(
        "PC 선택 규칙", ["variance95", "amfall_q"],
        index=["variance95", "amfall_q"].index(st.session_state["pp_fd_pc_mode"]),
        help="variance95: 논문 §5.1 대로 분산 95% 를 담는 PC (대역통과 전 신호). "
             "amfall_q: 이 레포의 amfall 이 q 로 고른 PC (대역통과 후). "
             "두 규칙이 피처에 얼마나 영향을 주는지 비교할 수 있습니다.",
    )
    freq_res = st.number_input("주파수 분해능 (Hz)", 0.5, 10.0,
                               float(st.session_state["pp_fd_freq_res_hz"]), step=0.5,
                               help="논문은 512@1000Hz ≈ 2 Hz. 창 길이는 fs/분해능으로 역산됩니다.")
    overlap = st.slider("STFT 겹침 비율", 0.5, 0.95,
                        float(st.session_state["pp_fd_overlap"]), step=0.05,
                        help="논문은 50%. 4초 파일에서는 프레임이 부족해 기본 90% 를 씁니다.")
    noise_k = st.number_input("잡음 임계 k (N_th = μ + kσ)", 0.5, 10.0,
                              float(st.session_state["pp_fd_noise_k"]), step=0.5)
    torso_pct = st.slider("몸통 주파수 percentile", 10.0, 90.0,
                          float(st.session_state["pp_fd_torso_pct"]), step=5.0)

prev = params_fingerprint()
st.session_state.update({
    "pp_fd_pc_mode": pc_mode, "pp_fd_freq_res_hz": float(freq_res),
    "pp_fd_overlap": float(overlap), "pp_fd_noise_k": float(noise_k),
    "pp_fd_torso_pct": float(torso_pct),
})
if params_fingerprint() != prev:
    st.session_state["fdp_has_run"] = False

pkwargs = probe_kwargs()

# --- 5.0 피처 정의와 이탈 ----------------------------------------------------

st.subheader("5.0 FallDeFi Table 1 과 이 구현의 이탈")

table1 = pd.DataFrame([
    {"구분": "Spectral", "피처": m, "설명": METRIC_DESCRIPTIONS.get(m, ""),
     "SF (환경 강건 주장)": "✅" if m in FALLDEFI_SF_METRICS else ""}
    for m in FALLDEFI_OF_METRICS if not m.startswith(("fd_event", "fd_pbc"))
] + [
    {"구분": "Power burst curve", "피처": m, "설명": METRIC_DESCRIPTIONS.get(m, ""),
     "SF (환경 강건 주장)": "✅" if m in FALLDEFI_SF_METRICS else ""}
    for m in FALLDEFI_OF_METRICS if m.startswith(("fd_event", "fd_pbc"))
])
st.dataframe(table1, use_container_width=True, hide_index=True)
st.caption(
    f"**OF {len(FALLDEFI_OF_METRICS)}개** 중 **SF {len(FALLDEFI_SF_METRICS)}개**"
    f"(event duration, spectral entropy 1-10 & 10-30 Hz, fractal dimension)를 저자들이 "
    "환경 강건 피처로 지목했습니다. 논문 §6.2 의 근거: 엔트로피와 프랙탈 차원은 **정규화된** "
    "값이고, 지속시간은 환경과 무관한 낙상 고유의 특성이라는 것입니다."
)

with st.expander("⚠️ Mendeley 적용 시 불가피한 이탈 6가지 (해석에 영향)"):
    st.markdown(
        f"""
| # | 논문 | 이 구현 | 왜 |
|---|---|---|---|
| 1 | 1000 pkts/s, 5 GHz (나이퀴스트 500 Hz) | **320 Hz (나이퀴스트 160 Hz)** | Mendeley 수집 조건. STFT 창은 주파수 분해능 {freq_res:.1f} Hz 를 맞추도록 fs/분해능으로 역산 |
| 2 | 잡음 임계를 **>250 Hz** 에서 추정 | **> fs/4 = {pkwargs['fs_hz']/4:.0f} Hz** | 320 Hz 표본화에는 250 Hz 대역이 **존재하지 않음**. 비례 대응하는 상위 절반을 쓰지만, 그 대역에도 실제 이벤트 에너지가 섞일 수 있어 **임계가 과대추정**될 수 있음 |
| 3 | wavelet denoising 후 PCA | **대역통과 이전(리샘플 직후) 신호** | amfall 의 0.5–80 Hz 를 걸면 80 Hz 위가 비어 `entropy_30_max` 와 잡음 추정 대역이 모두 무의미해짐 |
| 4 | 분산 95% PC 에 STFT 후 평균 | 동일 (`variance95`), `amfall_q` 옵션 제공 | 두 PC 선택 규칙의 영향을 비교하기 위해 |
| 5 | — | extreme/torso 곡선에서 **1 Hz 미만 제외** | CSI 진폭의 거대한 저주파 드리프트를 넣으면 두 곡선이 전 파일에서 0–2 Hz 로 눌림(실측) |
| 6 | Hausdorff 차원 | **Higuchi 추정기** | 4초 파일 스펙트로그램은 프레임이 20–70개라 box-counting 이 점 개수에서 포화되어 곡선 모양과 무관하게 1.0 이 나옴(실측) |

PBC 대역은 논문 그대로 **{PBC_LOW_HZ:.0f}–{PBC_HIGH_HZ:.0f} Hz**, 엔트로피 대역도 그대로
{', '.join(f'{lo:.0f}-{hi if hi else "max"}' for _, lo, hi in ENTROPY_BANDS)} Hz 입니다.

**2번이 가장 중요한 한계입니다.** 논문은 "이벤트는 175 Hz 를 넘지 않으므로 250 Hz 위는 순수 잡음"
이라는 전제로 임계를 잡는데, Mendeley 는 스펙트럼 전체가 160 Hz 이하라 그 전제를 만족하는
대역이 없습니다. 따라서 여기서 재현한 FallDeFi 피처는 원 논문과 완전히 동일한 값이 아니며,
아래 결론은 "이 적응 구현 기준"으로 읽어야 합니다.
"""
    )

# --- 게이트 ------------------------------------------------------------------

status = probe_cache_status(cache_dir, params_fingerprint(), paths)
st.caption(
    f"표본 **{len(paths)}** 개 파일 · 캐시 적중 **{status['cached']}** / 계산 필요 "
    f"**{status['missing']}** → 예상 **{status['missing'] * COST_PROBE_SEC_PER_FILE:.0f}초** "
    f"(params_hash `{status['params_hash']}`)"
)

if st.button("FallDeFi 피처 계산", type="primary", use_container_width=True):
    st.session_state["fdp_has_run"] = True

if not st.session_state["fdp_has_run"]:
    st.info("사이드바 파라미터를 확인한 뒤 위 버튼을 눌러 실행하세요. "
            "FallDeFi 피처는 페이지 3·4 와 같은 probe 결과에 들어 있어 대부분 캐시 적중입니다.")
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

available_of = [m for m in FALLDEFI_OF_METRICS if m in records.columns]
available_sf = [m for m in FALLDEFI_SF_METRICS if m in records.columns]

m1, m2, m3, m4 = st.columns(4)
m1.metric("분석 파일", f"{len(records):,}")
m2.metric("OF 피처", f"{len(available_of)}")
m3.metric("SF 피처", f"{len(available_sf)}")
det = float(records["fd_event_detected"].mean()) if "fd_event_detected" in records else float("nan")
m4.metric("PBC 이벤트 검출률", f"{det:.0%}")

if "fd_event_detected" in records and records["fd_event_detected"].nunique() > 1:
    by_fall = records.groupby("fall_label")["fd_event_detected"].mean()
    st.caption(
        "PBC prescreener 검출률 — "
        + " · ".join(f"{k} {v:.0%}" for k, v in by_fall.items())
        + ". 논문은 이 단계에서 낙상을 100% 잡는다고 보고했습니다. 낙상 검출률이 낮다면 "
          "위 이탈 2번(잡음 임계 과대추정)의 영향일 수 있습니다."
    )

# --- 5.1 한 파일 따라가기 ----------------------------------------------------

st.subheader("5.1 한 파일 따라가기 — 스펙트로그램에서 피처까지")

names = records.sort_values(["env", "subject", "activity", "trial"])
options = names["filepath"].tolist()
labels = dict(zip(names["filepath"], names["name"] + "  (" + names["fall_label"] + ")"))

default_idx = 0
if st.session_state["fdp_focus_file"] in options:
    default_idx = options.index(st.session_state["fdp_focus_file"])
focus = st.selectbox("파일 선택", options, index=default_idx,
                     format_func=lambda p: labels.get(p, p))
st.session_state["fdp_focus_file"] = focus

stages = single_file_stages_cached(focus, **pkwargs)
arrays = (stages.get("signals") or {}).get("falldefi")
rec = stages["record"]

if not arrays:
    st.info("이 파일에서는 FallDeFi 배열을 생성하지 못했습니다.")
else:
    chart(plot_falldefi_spectrogram(arrays))
    st.caption(
        f"잡음 임계 N_th = {rec['fd_noise_threshold']:.4g} "
        f"(> {rec['fd_noise_band_lo_hz']:.0f} Hz 대역의 μ + {noise_k:g}σ), "
        f"프레임 {rec['fd_n_frames']}개, PC {rec.get('fd_n_pcs_used', 0)}개"
        + (f" (분산 {rec['fd_pc_var_explained']:.1%})"
           if np.isfinite(rec.get("fd_pc_var_explained", np.nan)) else "")
        + ". 빨간 선은 극단 주파수, 흰 선은 몸통 주파수 곡선입니다."
    )

    chart(plot_power_burst_curve(arrays))
    st.caption(
        f"PBC 는 {PBC_LOW_HZ:.0f}–{PBC_HIGH_HZ:.0f} Hz 대역의 진폭 합입니다(논문 식 5). "
        f"임계를 넘는 가장 긴 구간이 이벤트이고, 그 길이가 SF 인 `fd_event_duration` "
        f"= **{rec['fd_event_duration']:.2f} s** 입니다."
        + ("" if rec["fd_event_detected"] else
           " 이 파일은 임계를 넘지 못해 이벤트 미검출이며, 스펙트럼 피처는 전체 구간에서 계산됩니다.")
    )

    k = st.columns(4)
    for col, key in zip(k, FALLDEFI_SF_METRICS):
        col.metric(key.replace("fd_", ""), f"{rec.get(key, float('nan')):.3f}")
    st.caption("위 4개가 논문이 환경 강건하다고 지목한 Selected Features 입니다.")

# --- 5.2 피처별 환경/활동 분포 ----------------------------------------------

st.subheader("5.2 피처별 분포 — 환경과 활동")

if records["fall_label"].nunique() < 2:
    st.warning("낙상/비낙상이 모두 있어야 활동 격차를 잴 수 있습니다. 페이지 1에서 활동 필터를 비우세요.")
else:
    feature_set = st.radio(
        "표시할 피처", ["SF (환경 강건 주장)", "OF only", "OF (전체)"],
        index=["SF (환경 강건 주장)", "OF only", "OF (전체)"].index(
            st.session_state["fdp_feature_set"])
        if st.session_state["fdp_feature_set"] in
        ["SF (환경 강건 주장)", "OF only", "OF (전체)"] else 0,
        horizontal=True,
    )
    st.session_state["fdp_feature_set"] = feature_set

    shown = {"SF (환경 강건 주장)": available_sf,
             "OF only": [m for m in FALLDEFI_OF_ONLY_METRICS if m in records.columns],
             "OF (전체)": available_of}[feature_set]

    for i in range(0, len(shown), 2):
        cols = st.columns(2)
        for col, metric in zip(cols, shown[i:i + 2]):
            with col:
                chart(plot_grouped_box_2factor(
                    records, metric, "fall_label", "env",
                    f"{metric.replace('fd_', '')} — {METRIC_DESCRIPTIONS.get(metric, '')}",
                ))
                res = kruskal_omnibus({g: t[metric] for g, t in records.groupby("env")})
                st.caption(
                    f"환경 Kruskal H={res['h']:.2f}, p={res['p_value']:.3g}, ε²={res['eps_sq']:.3f}"
                    if res["h"] is not None else "그룹이 부족해 검정할 수 없습니다."
                )

# --- 5.3 논문 주장 검증 -------------------------------------------------------

st.subheader("5.3 논문 주장 검증 — SF 가 정말 환경에 강건한가")

if records["fall_label"].nunique() < 2:
    st.warning("활동 격차를 잴 수 없어 이 절을 건너뜁니다.")
    st.stop()

gaps = gap_comparison(records, available_of)
gaps_valid = gaps.dropna(subset=["env_gap", "class_gap"])

if gaps_valid.empty:
    st.info("격차를 계산할 데이터가 부족합니다.")
else:
    chart(plot_of_sf_gap_scatter(gaps_valid, available_sf))
    st.caption(
        "**대각선 아래**(환경 격차 < 활동 격차)에 있어야 도메인 간 일반화에 쓸 만한 피처입니다. "
        "논문 주장이 맞다면 별표(SF)가 원(OF only)보다 아래쪽·왼쪽에 몰려야 합니다."
    )

    c1, c2 = st.columns([2, 3])
    with c1:
        chart(plot_of_sf_summary_bar(gaps_valid, available_sf))
    with c2:
        g = gaps_valid.copy()
        g["묶음"] = np.where(g["metric"].isin(available_sf), "SF", "OF only")
        summary = (g.groupby("묶음")
                   .agg(피처수=("metric", "size"),
                        평균_환경격차=("env_gap", "mean"),
                        평균_활동격차=("class_gap", "mean"),
                        환경이_활동보다_큰_피처=("env_exceeds_class", "sum"))
                   .round(3))
        st.dataframe(summary, use_container_width=True)

        sf_env = g.loc[g["묶음"] == "SF", "env_gap"].mean()
        of_env = g.loc[g["묶음"] == "OF only", "env_gap"].mean()
        if np.isfinite(sf_env) and np.isfinite(of_env):
            if sf_env < of_env:
                st.success(
                    f"**논문 주장과 일치**: SF 평균 환경 격차 {sf_env:.3f} < OF-only {of_env:.3f}. "
                    "이 데이터셋에서도 SF 가 환경 변화에 상대적으로 덜 민감합니다."
                )
            else:
                st.error(
                    f"**논문 주장과 불일치**: SF 평균 환경 격차 {sf_env:.3f} ≥ OF-only {of_env:.3f}. "
                    "Mendeley 에서는 SF 가 더 강건하지 않습니다. 위 이탈 2번(잡음 임계)과 "
                    "환경↔피험자 교락을 함께 감안해 해석하세요."
                )

    n_ok = int((~gaps_valid["env_exceeds_class"].astype(bool)).sum())
    st.caption(
        f"전체 OF {len(gaps_valid)}개 중 **{n_ok}개**만 환경 격차가 활동 격차보다 작습니다 "
        f"(= 대각선 아래). 나머지 {len(gaps_valid) - n_ok}개는 '어느 방인지'가 '낙상인지'보다 "
        "강하게 드러나는 피처입니다."
    )
    st.dataframe(
        gaps_valid.assign(묶음=np.where(gaps_valid["metric"].isin(available_sf), "SF", "OF only"))
        [["metric", "묶음", "env_gap", "class_gap", "ratio",
          "env_gap_within_fall", "env_gap_within_nonfall", "env_exceeds_class"]]
        .sort_values("ratio").round(3),
        use_container_width=True, hide_index=True,
    )

# --- 5.4 쌍별 발산 ------------------------------------------------------------

st.subheader("5.4 FallDeFi 피처 쌍별 발산")

table = add_fdr(pairwise_divergence_table(records, available_of, "env"))
if table.empty:
    st.info("비교할 그룹이 부족합니다.")
else:
    show = table.copy()
    show["묶음"] = np.where(show["metric"].isin(available_sf), "SF", "OF only")
    show["쌍"] = "E" + show["group_a"].astype(str) + "–E" + show["group_b"].astype(str)
    st.dataframe(
        show[["metric", "묶음", "쌍", "n_a", "n_b", "median_a", "median_b",
              "ks_d", "cliffs_delta", "cliffs_magnitude", "ks_p", "ks_q"]]
        .round({"median_a": 3, "median_b": 3, "ks_d": 3, "cliffs_delta": 3}),
        use_container_width=True,
    )

with st.expander("파일별 FallDeFi 피처 레코드"):
    cols = (["name", "env", "subject", "activity_name", "fall_label",
             "fd_event_detected", "fd_pc_mode", "fd_pc_var_explained"] + available_of)
    st.dataframe(records[[c for c in cols if c in records.columns]],
                 use_container_width=True)
    st.download_button(
        "CSV 다운로드",
        records[[c for c in cols if c in records.columns]].to_csv(index=False).encode("utf-8-sig"),
        file_name="falldefi_features.csv", mime="text/csv",
    )
