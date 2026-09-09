"""Page 2 - F(프레넬) 조건별로 두 피처가 어떻게 다른지 비교한다.

그룹 평균 이미지 / 전체 평균 대비 차분 / 1D 주변 프로파일 / 스칼라 분포 순으로
같은 데이터를 점점 요약해가며 보여준다. 차이는 보통 프로파일에서 가장 잘 보인다.
"""

from typing import Dict

import numpy as np
import pandas as pd
import streamlit as st

from lib.cache import extract_batch, get_file_index_cached, scalogram_freq_axis
from lib.plotting import (
    DIFF_COLORSCALE,
    plot_acf,
    plot_box_by_group,
    plot_group_profiles,
    plot_scalogram,
)
from lib.state_defaults import feature_kwargs
from src.dwt_coef.features import ssqueezepy_available

st.title("📐 Fresnel Compare")
st.caption(
    "낙상 3초 세그먼트의 CWT S3 스칼로그램과 PCA-ACF 가 프레넬 조건 F 에 따라 "
    "어떻게 달라지는지 비교한다."
)

raw_root = st.session_state["raw_root"]
file_index = get_file_index_cached(raw_root)

if file_index.empty:
    st.error(f"라벨 파일을 찾지 못했습니다: {raw_root}")
    st.stop()

with st.sidebar:
    st.header("Grouping")
    group_mode = st.radio(
        "F 그룹화 단위",
        options=["a", "ab"],
        index=["a", "ab"].index(st.session_state["fc_group_mode"]),
        format_func=lambda m: "F{a} — 3그룹 (각 6파일)" if m == "a" else "F{a}-{b} — 9셀 (각 2파일)",
        help="9셀 모드는 셀당 파일이 2개뿐이라 노이즈가 지배적일 수 있다.",
    )
    persons = st.multiselect(
        "참여자",
        sorted(file_index["person"].unique().tolist()),
        default=list(st.session_state["fc_persons"]),
    )

    st.divider()
    st.header("Windowing")
    rule = st.radio(
        "라벨링 규칙",
        options=["midpoint", "contains"],
        index=["midpoint", "contains"].index(st.session_state["ft_rule"]),
        help="midpoint 는 학습 파이프라인과 동일. contains 는 더 보수적이라 윈도우가 절반쯤 된다.",
    )
    max_windows = st.slider(
        "파일당 윈도우 수", 1, 12, int(st.session_state["ft_max_windows"])
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
    n_files = len(file_index[file_index["person"].isin(persons)]) if persons else 0
    est_sec = n_files * (max_windows * 0.75 + 1.5)
    st.caption(
        f"{n_files}개 파일 × {max_windows}윈도우 ≈ {est_sec:.0f}초 (첫 실행 기준, 캐시 적중 시 즉시)"
    )
    run_clicked = st.button("계산 실행", type="primary", width="stretch")

st.session_state.update(
    {
        "fc_group_mode": group_mode,
        "fc_persons": tuple(persons),
        "ft_rule": rule,
        "ft_max_windows": max_windows,
        "ft_target_subcarriers": target_subcarriers,
        "ft_omega": omega,
        "ft_acf_lag_seconds": acf_lag_seconds,
    }
)

if run_clicked:
    st.session_state["fc_has_run"] = True
if not persons:
    st.warning("참여자를 최소 한 명 선택하세요.")
    st.stop()
if not st.session_state["fc_has_run"]:
    st.info("사이드바에서 조건을 고른 뒤 **계산 실행** 을 누르세요.")
    st.stop()

selected_files = file_index[file_index["person"].isin(persons)]
progress = st.progress(0.0, text="피처 계산 준비 중...")


def _on_progress(done: int, total: int, name: str) -> None:
    progress.progress(done / max(total, 1), text=f"[{done}/{total}] {name}")


s3, acf, meta, errors = extract_batch(
    selected_files["filepath_str"].tolist(),
    group_mode=group_mode,
    progress_callback=_on_progress,
    **feature_kwargs(),
)
progress.empty()

if len(s3) == 0:
    st.error("윈도우가 하나도 나오지 않았습니다. 라벨링 규칙이나 필터를 바꿔보세요.")
    if errors:
        st.write(errors)
    st.stop()

if errors:
    with st.expander(f"⚠️ 건너뛴 파일 {len(errors)}개"):
        for line in errors:
            st.write(line)

groups = sorted(meta["f_group"].unique().tolist())
fs_hz = float(meta["fs_hz"].iloc[0])
freqs = scalogram_freq_axis(fs_hz, 1.0, 170.0, 224)

st.subheader(f"{len(s3)}개 윈도우 / {meta['name'].nunique()}개 레코딩 / {len(groups)}개 F 그룹")
st.caption(
    f"CWT 백엔드: {'ssqueezepy (gmw)' if ssqueezepy_available() else '⚠️ numpy Morlet fallback (근사)'} | "
    f"fs = {fs_hz:.2f} Hz | 라벨링 = {rule}"
)

# --- 교락 확인: F 와 참여자가 섞여 있는지 ---
st.markdown("#### F × 참여자 분포")
st.caption(
    "F 그룹마다 참여자 구성이 다르면, 아래에서 보이는 차이가 F 때문인지 사람 때문인지 "
    "구분할 수 없다 (교락)."
)
crosstab = pd.crosstab(meta["f_group"], meta["person"].map(lambda p: f"P{p}"))
st.dataframe(crosstab, width="stretch")

group_windows: Dict[str, np.ndarray] = {g: np.flatnonzero(meta["f_group"] == g) for g in groups}
small_groups = [g for g, idx in group_windows.items() if len(idx) < 5]
if small_groups:
    st.warning(f"윈도우가 5개 미만인 그룹: {', '.join(small_groups)} — 평균이 불안정하다.")

# --- 1. 그룹 평균 피처 이미지 ---
st.markdown("---")
st.markdown("#### 1. F 그룹별 평균 피처")

s3_means = {g: s3[idx].mean(axis=0) for g, idx in group_windows.items()}
acf_means = {g: acf[idx].mean(axis=0) for g, idx in group_windows.items()}

n_cols = min(3, len(groups))
cols = st.columns(n_cols)
for i, g in enumerate(groups):
    with cols[i % n_cols]:
        st.plotly_chart(
            plot_scalogram(
                s3_means[g],
                freqs,
                fs_hz=fs_hz,
                title=f"{g} — mean S3 (n={len(group_windows[g])})",
                height=320,
                downsample=2,
                zmin=0.0,
                zmax=float(max(m.max() for m in s3_means.values())),
            ),
            width="stretch",
        )

cols = st.columns(n_cols)
for i, g in enumerate(groups):
    with cols[i % n_cols]:
        st.plotly_chart(
            plot_acf(
                acf_means[g],
                lag_seconds=acf_lag_seconds,
                title=f"{g} — mean PCA-ACF (n={len(group_windows[g])})",
                height=320,
                zmin=-0.6,
                zmax=0.6,
            ),
            width="stretch",
        )

# --- 2. 전체 평균 대비 차분 ---
st.markdown("---")
st.markdown("#### 2. 전체 평균 대비 차분")
st.caption("그룹 평균 − 전체 평균. F 별 차이가 가장 뚜렷하게 드러나는 패널이다.")

s3_grand = s3.mean(axis=0)
acf_grand = acf.mean(axis=0)
s3_diff_max = max(float(np.abs(m - s3_grand).max()) for m in s3_means.values())
acf_diff_max = max(float(np.abs(m - acf_grand).max()) for m in acf_means.values())

cols = st.columns(n_cols)
for i, g in enumerate(groups):
    with cols[i % n_cols]:
        st.plotly_chart(
            plot_scalogram(
                s3_means[g] - s3_grand,
                freqs,
                fs_hz=fs_hz,
                title=f"{g} — S3 차분",
                height=320,
                downsample=2,
                zmin=-s3_diff_max,
                zmax=s3_diff_max,
                zmid=0.0,
                colorscale=DIFF_COLORSCALE,
            ),
            width="stretch",
        )

cols = st.columns(n_cols)
for i, g in enumerate(groups):
    with cols[i % n_cols]:
        st.plotly_chart(
            plot_acf(
                acf_means[g] - acf_grand,
                lag_seconds=acf_lag_seconds,
                title=f"{g} — ACF 차분",
                height=320,
                zmin=-acf_diff_max,
                zmax=acf_diff_max,
            ),
            width="stretch",
        )

# --- 3. 1D 주변 프로파일 ---
st.markdown("---")
st.markdown("#### 3. 1D 주변 프로파일")
st.caption("2D 맵을 한 축으로 접어 그룹별로 겹쳐 그린다. 밴드는 ±1 표준편차.")

prof_cols = st.columns(2)
with prof_cols[0]:
    st.plotly_chart(
        plot_group_profiles(
            {g: s3[idx].mean(axis=2) for g, idx in group_windows.items()},
            x=freqs[::-1],
            x_title="Frequency (Hz)",
            y_title="Mean S3 energy",
            title="S3 주파수 프로파일 (시간축 평균)",
        ),
        width="stretch",
    )
    st.plotly_chart(
        plot_group_profiles(
            {g: s3[idx].mean(axis=1) for g, idx in group_windows.items()},
            x=np.linspace(0.0, 3.0, s3.shape[2]),
            x_title="Time in window (s)",
            y_title="Mean S3 energy",
            title="S3 시간 프로파일 (주파수축 평균)",
        ),
        width="stretch",
    )

with prof_cols[1]:
    lags = np.linspace(acf_lag_seconds / acf.shape[1], acf_lag_seconds, acf.shape[1])
    st.plotly_chart(
        plot_group_profiles(
            {g: acf[idx].mean(axis=2) for g, idx in group_windows.items()},
            x=lags,
            x_title="Lag (s)",
            y_title="Mean ACF",
            title="ACF lag 프로파일 (시간축 평균)",
        ),
        width="stretch",
    )
    st.plotly_chart(
        plot_group_profiles(
            {g: acf[idx].mean(axis=1) for g, idx in group_windows.items()},
            x=np.linspace(0.0, 3.0, acf.shape[2]),
            x_title="Time in window (s)",
            y_title="Mean ACF",
            title="ACF 시간 프로파일 (lag축 평균)",
        ),
        width="stretch",
    )

# --- 4. 스칼라 분포 ---
st.markdown("---")
st.markdown("#### 4. 스칼라 요약 분포")

meta = meta.copy()
meta["acf_abs_mean"] = np.abs(acf).mean(axis=(1, 2))
meta["s3_energy"] = s3.mean(axis=(1, 2))

scalar_specs = [
    ("s3_nonzero_ratio", "S3 nonzero 비율"),
    ("s3_energy", "S3 평균 에너지"),
    ("signal_q", "signal q (max/mean 이동분산)"),
    ("acf_abs_mean", "ACF 평균 |값|"),
]
cols = st.columns(2)
for i, (col_name, title) in enumerate(scalar_specs):
    with cols[i % 2]:
        st.plotly_chart(
            plot_box_by_group(
                {g: meta.loc[meta["f_group"] == g, col_name].to_numpy() for g in groups},
                y_title=title,
                title=f"{title} by F",
            ),
            width="stretch",
        )

with st.expander("윈도우별 원자료"):
    st.dataframe(meta, width="stretch")
