"""Page 1 - 단일 3초 세그먼트에서 두 피처가 어떻게 만들어지는지 확인한다.

PCA motion signal -> S0~S3 디노이즈 4단계 -> PCA-ACF 순으로 보여준다.
F 비교(Page 2) 결과를 해석하기 전에 피처 자체가 정상인지 검증하는 용도.
"""

import numpy as np
import streamlit as st

from lib.cache import extract_recording_cached, get_file_index_cached, scalogram_freq_axis
from lib.plotting import (
    plot_acf,
    plot_recording_timeline,
    plot_scalogram,
    plot_signal,
)
from lib.state_defaults import feature_kwargs
from src.dwt_coef.features import STAGE_DESCRIPTIONS, STAGE_NAMES, ssqueezepy_available

st.title("🔍 Segment Inspector")
st.caption(
    "낙상 라벨이 포함된 3초 윈도우 하나를 골라 CWT S3 스칼로그램과 PCA-ACF 가 "
    "어떻게 만들어지는지 단계별로 확인한다."
)

raw_root = st.session_state["raw_root"]
file_index = get_file_index_cached(raw_root)

if file_index.empty:
    st.error(f"라벨 파일을 찾지 못했습니다: {raw_root}")
    st.stop()

with st.sidebar:
    st.header("Recording")
    names = file_index["name"].tolist()
    default_name = st.session_state["si_file"] if st.session_state["si_file"] in names else names[0]
    name = st.selectbox("File", names, index=names.index(default_name))
    row = file_index[file_index["name"] == name].iloc[0]
    st.caption(
        f"P{row['person']} / Q{row['take']} / F{row['fresnel_a']}-{row['fresnel_b']} "
        f"/ 기대 라벨 {row['expected_label']}"
    )

    st.divider()
    st.header("Windowing")
    rule = st.radio(
        "라벨링 규칙",
        options=["midpoint", "contains"],
        index=["midpoint", "contains"].index(st.session_state["ft_rule"]),
        help=(
            "midpoint: 낙상 구간의 중점이 윈도우 안에 있으면 fall (학습 파이프라인과 동일). "
            "contains: 낙상 구간이 윈도우에 통째로 들어가야 fall."
        ),
    )
    stride_sec = st.number_input(
        "Stride (s)", 0.05, 2.0, float(st.session_state["ft_stride_sec"]), step=0.05
    )
    max_windows = st.slider(
        "파일당 윈도우 수",
        1,
        20,
        int(st.session_state["ft_max_windows"]),
        help="낙상 윈도우 중 균등 간격으로 샘플. 윈도우당 약 0.7초 소요.",
    )

    st.divider()
    st.header("Feature params")
    st.caption("기본값은 학습 설정. 바꾸면 학습된 모델의 피처 분포와 어긋난다.")
    target_subcarriers = st.slider(
        "서브캐리어 수", 4, 60, int(st.session_state["ft_target_subcarriers"]),
        help="243개 유효 서브캐리어에서 균등 선택. 학습값 30.",
    )
    omega = st.slider("omega (top-K 스트림)", 4, 60, int(st.session_state["ft_omega"]))
    th_scmax = st.number_input("th_scmax", 0.1, 5.0, float(st.session_state["ft_th_scmax"]), step=0.1)
    kappa = st.number_input("kappa", 0.1, 5.0, float(st.session_state["ft_kappa"]), step=0.1)
    acf_lag_seconds = st.number_input(
        "ACF lag (s)", 0.05, 2.0, float(st.session_state["ft_acf_lag_seconds"]), step=0.05
    )

    st.divider()
    st.caption(
        f"CWT 백엔드: {'ssqueezepy (gmw)' if ssqueezepy_available() else '⚠️ numpy Morlet fallback (근사)'}"
    )

st.session_state.update(
    {
        "si_file": name,
        "ft_rule": rule,
        "ft_stride_sec": stride_sec,
        "ft_max_windows": max_windows,
        "ft_target_subcarriers": target_subcarriers,
        "ft_omega": omega,
        "ft_th_scmax": th_scmax,
        "ft_kappa": kappa,
        "ft_acf_lag_seconds": acf_lag_seconds,
    }
)

kwargs = feature_kwargs()
with st.spinner(f"{name} 피처 계산 중... (윈도우당 약 0.7초)"):
    try:
        result = extract_recording_cached(row["filepath_str"], stages=True, **kwargs)
    except Exception as exc:
        st.error(f"피처 계산 실패: {exc}")
        st.stop()

n_windows = len(result["s3"])
if n_windows == 0:
    st.warning(
        f"'{rule}' 규칙으로 낙상 윈도우가 나오지 않았습니다 "
        f"(전체 {result['n_windows_total']}개 윈도우). 규칙을 midpoint 로 바꿔보세요."
    )
    st.stop()

st.subheader(f"{name}")
st.caption(
    f"fs = {result['fs_hz']:.2f} Hz (네이티브, 중앙값 기반) | "
    f"윈도우 {result['window_samples']} 샘플 = 3.0초 | "
    f"전체 {result['n_windows_total']}개 중 낙상 {result['n_windows_fall']}개 "
    f"→ {n_windows}개 표시 | 라벨 {result['file_label']}"
)

st.plotly_chart(
    plot_recording_timeline(
        n_windows_total=result["n_windows_total"],
        stride_samples=result["stride_samples"],
        fs_hz=result["fs_hz"],
        window_samples=result["window_samples"],
        fall_intervals_sec=result["fall_intervals_sec"],
        selected_starts_sec=result["window_start_sec"],
    ),
    width="stretch",
)

window_labels = [
    f"#{int(result['window_index'][i])} @ {result['window_start_sec'][i]:.2f}s"
    for i in range(n_windows)
]
selected = st.selectbox("Window", range(n_windows), format_func=lambda i: window_labels[i])

freqs = scalogram_freq_axis(result["fs_hz"], 1.0, 170.0, 224)
stats = result["per_window_stats"][selected]

st.plotly_chart(
    plot_signal(result["signal"][selected], result["fs_hz"], title="PCA motion signal (3s window)"),
    width="stretch",
)

st.subheader("CWT 디노이즈 단계")
st.caption(
    "S0 원본 스칼로그램 → S1 행 평균 임계 → S2 주파수축 연속성 → S3 최소 지속시간. "
    "뒤로 갈수록 희소해져야 정상이다."
)
stage_cols = st.columns(2)
for i, stage in enumerate(STAGE_NAMES):
    nonzero = stats.get(f"{stage}_nonzero_ratio", float("nan"))
    with stage_cols[i % 2]:
        st.plotly_chart(
            plot_scalogram(
                result["stage_maps"][stage][selected],
                freqs,
                fs_hz=result["fs_hz"],
                title=f"{stage.upper()} — {STAGE_DESCRIPTIONS[stage]} (nonzero {nonzero:.3f})",
                downsample=2,
            ),
            width="stretch",
        )

st.subheader("PCA-ACF")
st.caption(
    "각 lag 마다 signal[t] × signal[t-lag] 를 시간축으로 펼친 시간분해 자기상관. "
    "1D ACF 곡선이 아니라 (lag × time) 2D 맵이다."
)
acf_col, profile_col = st.columns([3, 2])
with acf_col:
    st.plotly_chart(
        plot_acf(
            result["acf"][selected],
            lag_seconds=acf_lag_seconds,
            title=f"PCA-ACF ({result['acf'].shape[1]} lag × {result['acf'].shape[2]} time)",
        ),
        width="stretch",
    )
with profile_col:
    from lib.plotting import plot_group_profiles

    lags = np.linspace(acf_lag_seconds / result["acf"].shape[1], acf_lag_seconds, result["acf"].shape[1])
    st.plotly_chart(
        plot_group_profiles(
            {"this window": result["acf"][selected].mean(axis=1)[None, :]},
            x=lags,
            x_title="Lag (s)",
            y_title="Mean ACF",
            title="Lag 프로파일 (시간축 평균)",
            height=400,
            show_band=False,
        ),
        width="stretch",
    )

with st.expander("Window stats"):
    st.json({k: (float(v) if isinstance(v, (int, float, np.floating)) else v) for k, v in stats.items()})

with st.expander("Selected subcarriers"):
    st.write(
        f"245개 슬롯 중 121/122 제외 후 {len(result['selected_subcarriers'])}개 균등 선택:"
    )
    st.code(result["selected_subcarriers"].tolist())
