"""Page 4 - 레코딩 1개를 3초/0.25초 슬라이딩으로 훑어 RAW CSI, CWT, ACF 변화를 본다.

일상행동 파일(`_csi`, Q7~Q12)은 프레넬 영역이 라벨돼 있지 않다. 대신 행동 순열이
알려져 있으므로(Q7 = 걷기→방향전환→앉기→일어서기→걷기 등), 레코딩 전체를 훑으면서
사람이 위치를 옮길 때 피처가 어떻게 변하는지 시간축 위에서 확인한다.

낙상 파일과 달리 fall 윈도우만 고르지 않고 **전 구간 윈도우**를 쓴다.
"""

import numpy as np
import streamlit as st

from lib.cache import (
    extract_recording_cached,
    get_file_index_cached,
    load_recording_raw_cached,
    scalogram_freq_axis,
)
from lib.plotting import (
    ACF_COLORSCALE,
    plot_acf,
    plot_csi_heatmap,
    plot_csi_traces,
    plot_profile_evolution,
    plot_scalar_evolution,
    plot_scalogram,
    plot_signal,
)
from src.dwt_coef.features import ssqueezepy_available
from src.dwt_coef.inhouse_features import fresnel_group_label
from src.dwt_coef.inhouse_loader import TAKE_PERMUTATION

ACTIVITY_NAMES = {
    "A1": "걷기",
    "A2": "방향 전환",
    "A3": "앉기",
    "A4": "일어서기",
    "A5": "서 있다 눕기",
    "A6": "허리 숙여 물건 집기",
    "A7": "쪼그려 앉았다 일어나기",
    "A8": "달리기",
    "A10": "서 있는 상태에서 낙상",
    "A11": "일어나다가 낙상",
    "A12": "걷다가 낙상",
}

st.title("🎞️ Recording Sweep")
st.caption(
    "레코딩 하나를 3초 윈도우 / 0.25초 stride 로 훑으며 RAW CSI → CWT → ACF 가 "
    "시간에 따라 어떻게 변하는지 본다. 일상행동 파일은 F 라벨이 없으므로 "
    "행동 순열과 시간축으로 위치 변화를 읽는다."
)

raw_root = st.session_state["raw_root"]
# 일상행동(_csi, Q7~Q12)까지 포함해야 하므로 labeled_only=False
file_index = get_file_index_cached(raw_root, labeled_only=False)

if file_index.empty:
    st.error(f"CSV 를 찾지 못했습니다: {raw_root}")
    st.stop()


def _describe(row) -> str:
    take = int(row["take"])
    sequence = TAKE_PERMUTATION.get(take, ())
    kind = "낙상" if row["is_fall_take"] else "일상행동"
    f_label = fresnel_group_label(row["fresnel_a"], row["fresnel_b"], mode="ab")
    return f"{row['name']}  ·  {kind}  ·  {f_label}"


ALL = "전체"


def _take_caption(take: int) -> str:
    sequence = TAKE_PERMUTATION.get(take, ())
    return " → ".join(ACTIVITY_NAMES.get(a, a) for a in sequence)


with st.sidebar:
    st.header("Sample")

    kind = st.radio(
        "행동 종류",
        options=["일상행동 (Q7~Q12)", "낙상 (Q1~Q6)", ALL],
        index=["일상행동 (Q7~Q12)", "낙상 (Q1~Q6)", ALL].index(
            st.session_state.get("sw_kind", "일상행동 (Q7~Q12)")
        ),
        horizontal=False,
    )
    is_fall_take = file_index["is_fall_take"].to_numpy(dtype=bool)
    if kind == "일상행동 (Q7~Q12)":
        candidates = file_index[~is_fall_take]
    elif kind == "낙상 (Q1~Q6)":
        candidates = file_index[is_fall_take]
    else:
        candidates = file_index
    candidates = candidates.reset_index(drop=True)

    persons = [ALL] + [f"P{p}" for p in sorted(candidates["person"].unique())]
    person_pick = st.selectbox(
        "참여자",
        persons,
        index=persons.index(st.session_state.get("sw_person"))
        if st.session_state.get("sw_person") in persons
        else 0,
    )
    if person_pick != ALL:
        candidates = candidates[candidates["person"] == int(person_pick[1:])].reset_index(drop=True)

    takes = [ALL] + [f"Q{q}" for q in sorted(candidates["take"].unique())]
    take_pick = st.selectbox(
        "행동 순열",
        takes,
        index=takes.index(st.session_state.get("sw_take")) if st.session_state.get("sw_take") in takes else 0,
        format_func=lambda t: t if t == ALL else f"{t} — {_take_caption(int(t[1:]))}",
    )
    if take_pick != ALL:
        candidates = candidates[candidates["take"] == int(take_pick[1:])].reset_index(drop=True)

    if candidates.empty:
        st.warning("조건에 맞는 파일이 없습니다.")
        st.stop()

    names = candidates["name"].tolist()
    default_name = st.session_state.get("sw_file")
    index = names.index(default_name) if default_name in names else 0

    def _option_label(n: str) -> str:
        r = candidates[candidates["name"] == n].iloc[0]
        f_label = fresnel_group_label(r["fresnel_a"], r["fresnel_b"], mode="ab")
        return f"P{r['person']} Q{r['take']} · {f_label}"

    name = st.selectbox(
        f"파일 ({len(names)}개)", names, index=index, format_func=_option_label
    )
    row = candidates[candidates["name"] == name].iloc[0]
    st.caption(f"`{name}`")

    st.divider()
    st.header("Sweep")
    st.caption("윈도우 3.0초 / stride 0.25초 고정. 계산량 때문에 균등 서브샘플만 조절한다.")
    max_windows = st.slider(
        "분석할 윈도우 수",
        4,
        140,
        int(st.session_state.get("sw_max_windows", 40)),
        step=4,
        help="전 구간 윈도우 중 균등 간격으로 샘플. 윈도우당 약 0.75초 소요.",
    )

    st.divider()
    st.header("Feature params")
    target_subcarriers = st.slider(
        "서브캐리어 수", 4, 60, int(st.session_state["ft_target_subcarriers"])
    )
    omega = st.slider("omega (top-K 스트림)", 4, 60, int(st.session_state["ft_omega"]))
    acf_lag_seconds = st.number_input(
        "ACF lag (s)", 0.05, 2.0, float(st.session_state["ft_acf_lag_seconds"]), step=0.05
    )

    st.divider()
    st.caption(f"≈ {max_windows * 0.75 + 2:.0f}초 소요 (첫 실행 기준)")
    run_clicked = st.button("계산 실행", type="primary", width="stretch")

st.session_state.update(
    {
        "sw_file": name,
        "sw_kind": kind,
        "sw_person": person_pick,
        "sw_take": take_pick,
        "sw_max_windows": max_windows,
        "ft_target_subcarriers": target_subcarriers,
        "ft_omega": omega,
        "ft_acf_lag_seconds": acf_lag_seconds,
    }
)

# --- 1. RAW CSI (피처 계산 없이 즉시 표시) ---
raw = load_recording_raw_cached(row["filepath_str"], target_subcarriers)

take = int(row["take"])
sequence = TAKE_PERMUTATION.get(take, ())
sequence_text = " → ".join(f"{a}({ACTIVITY_NAMES.get(a, a)})" for a in sequence)

st.subheader(_describe(row))
st.caption(
    f"P{row['person']} / Q{take} / fs = {raw['fs_hz']:.2f} Hz / "
    f"{raw['duration_sec']:.1f}초 / {raw['n_packets']}패킷 → 그리드 {len(raw['resampled'])}샘플"
)
st.markdown(f"**행동 순열 Q{take}**: {sequence_text}")
if not row["is_fall_take"]:
    st.info(
        "일상행동 파일은 프레넬 영역(F)도, 구간별 행동 라벨도 없다. "
        "위 순열 순서대로 진행됐다고 보고 시간축에서 구간을 읽어야 한다."
    )

st.markdown("---")
st.markdown("#### 1. RAW CSI")
st.plotly_chart(
    plot_csi_heatmap(
        raw["resampled"],
        raw["time_sec"],
        raw["selected_subcarriers"],
        fall_intervals_sec=raw["fall_intervals_sec"],
    ),
    width="stretch",
)
st.plotly_chart(
    plot_csi_traces(
        raw["resampled"],
        raw["time_sec"],
        raw["selected_subcarriers"],
        fall_intervals_sec=raw["fall_intervals_sec"],
    ),
    width="stretch",
)

if run_clicked:
    st.session_state["sw_has_run"] = True
if not st.session_state.get("sw_has_run"):
    st.info("**계산 실행** 을 누르면 CWT / ACF 를 계산합니다.")
    st.stop()

# --- 2~3. 윈도우별 피처 ---
with st.spinner(f"{max_windows}개 윈도우의 CWT / ACF 계산 중..."):
    try:
        result = extract_recording_cached(
            row["filepath_str"],
            rule="midpoint",
            stride_sec=0.25,
            max_windows=max_windows,
            target_subcarriers=target_subcarriers,
            omega=omega,
            th_scmax=float(st.session_state["ft_th_scmax"]),
            kappa=float(st.session_state["ft_kappa"]),
            acf_lag_seconds=acf_lag_seconds,
            fall_only=False,
        )
    except Exception as exc:
        st.error(f"피처 계산 실패: {exc}")
        st.stop()

s3 = result["s3"]
acf = result["acf"]
centers = result["window_center_sec"]
freqs = scalogram_freq_axis(result["fs_hz"], 1.0, 170.0, 224)

# 스크러버는 계산 후에야 윈도우 수를 알 수 있으므로 사이드바를 여기서 이어서 그린다.
# (with st.sidebar 는 스크립트 어디서든 사이드바에 덧붙인다)
with st.sidebar:
    st.divider()
    st.header("Window")
    picked = st.select_slider(
        "윈도우 중심 시각",
        options=list(range(len(s3))),
        value=min(int(st.session_state.get("sw_window", len(s3) // 2)), len(s3) - 1),
        format_func=lambda i: f"{centers[i]:.2f}s",
    )
    st.caption(
        f"윈도우 #{int(result['window_index'][picked])} / 전체 {result['n_windows_total']}개 중 "
        f"{len(s3)}개 샘플"
    )
st.session_state["sw_window"] = picked

st.markdown("---")
st.markdown("#### 2. CWT (S3) — 시간에 따른 변화")
st.caption(
    f"전체 {result['n_windows_total']}개 윈도우 중 {len(s3)}개 균등 샘플. "
    "각 윈도우의 224×224 스칼로그램을 주파수 프로파일로 접어 레코딩 시간축에 쌓았다. "
    "가로 = 윈도우 중심 시각, 세로 = 주파수."
)
st.plotly_chart(
    plot_profile_evolution(
        s3.mean(axis=2),
        centers,
        freqs[::-1],
        y_title="Frequency (Hz)",
        title="S3 주파수 프로파일의 시간 변화",
        fall_intervals_sec=raw["fall_intervals_sec"],
    ),
    width="stretch",
)

st.markdown("---")
st.markdown("#### 3. PCA-ACF — 시간에 따른 변화")
st.caption("각 윈도우의 128×64 ACF 를 lag 프로파일로 접어 쌓았다. 세로 = lag.")
lags = np.linspace(acf_lag_seconds / acf.shape[1], acf_lag_seconds, acf.shape[1])
st.plotly_chart(
    plot_profile_evolution(
        acf.mean(axis=2),
        centers,
        lags,
        y_title="Lag (s)",
        title="ACF lag 프로파일의 시간 변화",
        colorscale=ACF_COLORSCALE,
        zmid=0.0,
        fall_intervals_sec=raw["fall_intervals_sec"],
    ),
    width="stretch",
)

st.plotly_chart(
    plot_scalar_evolution(
        {
            "S3 평균 에너지": s3.mean(axis=(1, 2)),
            "ACF 평균 |값|": np.abs(acf).mean(axis=(1, 2)),
            "signal q / 10": np.array([s["signal_q"] for s in result["per_window_stats"]]) / 10.0,
        },
        centers,
        y_title="Value",
        title="윈도우별 스칼라 지표 (움직임이 큰 구간에서 함께 올라간다)",
        fall_intervals_sec=raw["fall_intervals_sec"],
    ),
    width="stretch",
)

# --- 4. 개별 윈도우 스크러버 ---
st.markdown("---")
st.markdown(f"#### 4. 개별 윈도우 — {centers[picked]:.2f}s")
st.caption("사이드바 **Window** 슬라이더로 시각을 옮기면서 본다.")

st.plotly_chart(
    plot_signal(
        result["signal"][picked],
        result["fs_hz"],
        title=f"PCA motion signal @ {centers[picked]:.2f}s",
    ),
    width="stretch",
)
cols = st.columns(2)
with cols[0]:
    st.plotly_chart(
        plot_scalogram(
            s3[picked],
            freqs,
            fs_hz=result["fs_hz"],
            title=f"S3 @ {centers[picked]:.2f}s",
            downsample=2,
        ),
        width="stretch",
    )
with cols[1]:
    st.plotly_chart(
        plot_acf(acf[picked], lag_seconds=acf_lag_seconds, title=f"PCA-ACF @ {centers[picked]:.2f}s"),
        width="stretch",
    )

st.caption(
    f"CWT 백엔드: {'ssqueezepy (gmw)' if ssqueezepy_available() else '⚠️ numpy Morlet fallback (근사)'}"
)

with st.expander("윈도우별 stats"):
    import pandas as pd

    st.dataframe(
        pd.DataFrame(result["per_window_stats"]).assign(center_sec=centers),
        width="stretch",
    )
