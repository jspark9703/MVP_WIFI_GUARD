"""go.Figure 빌더. st.* 는 호출하지 않는다 (기존 streamlit_app 규약).

레이아웃 규약도 기존 앱을 따른다: height 300~400, margin dict(t=60, b=40),
시계열은 go.Scattergl + line width 1.
"""

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import plotly.graph_objects as go

# S3 는 [0,1] 단방향, ACF 는 [-1,1] 양방향이라 컬러스케일을 다르게 쓴다.
S3_COLORSCALE = "Viridis"
ACF_COLORSCALE = "RdBu"
DIFF_COLORSCALE = "RdBu"

FALL_COLOR = "rgba(220, 20, 60, 0.15)"
FALL_BADGE_COLOR = "#dc143c"
GROUP_PALETTE = ["#2a78d6", "#1baf7a", "#eda100", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#17becf"]


def group_color(index: int) -> str:
    return GROUP_PALETTE[index % len(GROUP_PALETTE)]


def block_mean(matrix: np.ndarray, factor: int) -> np.ndarray:
    """표시용 다운샘플. 224 -> 112 (factor=2). 나눠떨어지지 않으면 잘라낸다."""
    if factor <= 1:
        return matrix
    rows = (matrix.shape[0] // factor) * factor
    cols = (matrix.shape[1] // factor) * factor
    trimmed = matrix[:rows, :cols]
    return trimmed.reshape(rows // factor, factor, cols // factor, factor).mean(axis=(1, 3))


def plot_signal(
    signal: np.ndarray,
    fs_hz: float,
    title: str = "PCA motion signal",
    height: int = 300,
) -> go.Figure:
    """윈도우의 1D PCA motion signal."""
    time_sec = np.arange(len(signal)) / fs_hz
    fig = go.Figure(
        go.Scattergl(x=time_sec, y=signal, mode="lines", line=dict(width=1), name="signal")
    )
    fig.update_layout(
        title=title,
        xaxis_title="Time in window (s)",
        yaxis_title="Amplitude",
        height=height,
        margin=dict(t=60, b=40),
        showlegend=False,
    )
    return fig


def plot_scalogram(
    matrix: np.ndarray,
    freqs_high_to_low: np.ndarray,
    fs_hz: float,
    window_seconds: float = 3.0,
    title: str = "S3 scalogram",
    height: int = 400,
    downsample: int = 1,
    zmin: Optional[float] = 0.0,
    zmax: Optional[float] = 1.0,
    colorscale: str = S3_COLORSCALE,
    zmid: Optional[float] = None,
) -> go.Figure:
    """(freq, time) 스칼로그램. 행 0 이 고주파이므로 뒤집어 저주파를 아래로 둔다."""
    display = block_mean(matrix, downsample)
    freqs = freqs_high_to_low[:: max(1, downsample)][: display.shape[0]]
    time_axis = np.linspace(0.0, window_seconds, display.shape[1])

    fig = go.Figure(
        go.Heatmap(
            z=display[::-1],
            x=time_axis,
            y=freqs[::-1],
            colorscale=colorscale,
            zmin=zmin,
            zmax=zmax,
            zmid=zmid,
            colorbar=dict(thickness=12),
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="Time (s)",
        yaxis_title="Frequency (Hz)",
        height=height,
        margin=dict(t=60, b=40),
    )
    return fig


def plot_acf(
    matrix: np.ndarray,
    lag_seconds: float = 0.4,
    window_seconds: float = 3.0,
    title: str = "PCA-ACF",
    height: int = 400,
    zmin: Optional[float] = -1.0,
    zmax: Optional[float] = 1.0,
    colorscale: str = ACF_COLORSCALE,
    zmid: Optional[float] = 0.0,
) -> go.Figure:
    """(lag, time) 자기상관 맵. lag 축은 0 -> lag_seconds."""
    lags = np.linspace(lag_seconds / matrix.shape[0], lag_seconds, matrix.shape[0])
    time_axis = np.linspace(0.0, window_seconds, matrix.shape[1])

    fig = go.Figure(
        go.Heatmap(
            z=matrix,
            x=time_axis,
            y=lags,
            colorscale=colorscale,
            zmin=zmin,
            zmax=zmax,
            zmid=zmid,
            colorbar=dict(thickness=12),
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="Time (s)",
        yaxis_title="Lag (s)",
        height=height,
        margin=dict(t=60, b=40),
    )
    return fig


def plot_group_profiles(
    profiles: Dict[str, np.ndarray],
    x: np.ndarray,
    x_title: str,
    y_title: str,
    title: str,
    height: int = 350,
    show_band: bool = True,
) -> go.Figure:
    """그룹별 평균±표준편차 프로파일을 겹쳐 그린다.

    Args:
        profiles: {group_label: (n_samples, n_bins)} - 그룹 내 윈도우들의 프로파일
        x: (n_bins,) 가로축 값
    """
    fig = go.Figure()
    for idx, (label, values) in enumerate(profiles.items()):
        if values.size == 0:
            continue
        color = group_color(idx)
        mean = values.mean(axis=0)
        if show_band and values.shape[0] > 1:
            std = values.std(axis=0)
            rgb = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
            fill = f"rgba({rgb[0]},{rgb[1]},{rgb[2]},0.15)"
            fig.add_trace(
                go.Scatter(
                    x=np.concatenate([x, x[::-1]]),
                    y=np.concatenate([mean + std, (mean - std)[::-1]]),
                    fill="toself",
                    fillcolor=fill,
                    line=dict(width=0),
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
        fig.add_trace(
            go.Scatter(
                x=x,
                y=mean,
                mode="lines",
                line=dict(width=2, color=color),
                name=f"{label} (n={values.shape[0]})",
            )
        )
    fig.update_layout(
        title=title,
        xaxis_title=x_title,
        yaxis_title=y_title,
        height=height,
        margin=dict(t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.08),
    )
    return fig


def plot_box_by_group(
    values_by_group: Dict[str, np.ndarray],
    y_title: str,
    title: str,
    height: int = 350,
) -> go.Figure:
    """그룹별 분포 box + 개별 점."""
    fig = go.Figure()
    for idx, (label, values) in enumerate(values_by_group.items()):
        if values.size == 0:
            continue
        fig.add_trace(
            go.Box(
                y=values,
                name=f"{label} (n={len(values)})",
                marker_color=group_color(idx),
                boxpoints="all",
                jitter=0.4,
                pointpos=0,
                marker=dict(size=4, opacity=0.6),
            )
        )
    fig.update_layout(
        title=title,
        yaxis_title=y_title,
        height=height,
        margin=dict(t=60, b=40),
        showlegend=False,
    )
    return fig


def plot_embedding(
    coords: np.ndarray,
    labels: Sequence[str],
    title: str,
    height: int = 450,
    symbols: Optional[Sequence[str]] = None,
) -> go.Figure:
    """2D 임베딩 산점도. 그룹 라벨로 색칠한다."""
    fig = go.Figure()
    unique = sorted(set(labels))
    labels_arr = np.asarray(labels)
    for idx, label in enumerate(unique):
        mask = labels_arr == label
        hover = None
        if symbols is not None:
            hover = np.asarray(symbols)[mask]
        fig.add_trace(
            go.Scattergl(
                x=coords[mask, 0],
                y=coords[mask, 1],
                mode="markers",
                marker=dict(size=7, color=group_color(idx), opacity=0.75),
                name=f"{label} (n={int(mask.sum())})",
                text=hover,
                hovertemplate="%{text}<extra></extra>" if hover is not None else None,
            )
        )
    fig.update_layout(
        title=title,
        xaxis_title="PC1",
        yaxis_title="PC2",
        height=height,
        margin=dict(t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.05),
    )
    return fig


def plot_csi_heatmap(
    amplitude: np.ndarray,
    time_sec: np.ndarray,
    subcarrier_idx: np.ndarray,
    fall_intervals_sec: Optional[List[Tuple[float, float]]] = None,
    max_columns: int = 1400,
    title: str = "RAW CSI amplitude",
    height: int = 380,
) -> go.Figure:
    """(time, subcarrier) 진폭 히트맵. 긴 레코딩은 시간축을 다운샘플한다.

    Args:
        amplitude: (M, n_sub) 리샘플된 진폭
        time_sec: (M,) 그리드 시간축
        subcarrier_idx: (n_sub,) 원본 슬롯 인덱스 (y축 라벨)
    """
    step = max(1, len(time_sec) // max_columns)
    display = amplitude[::step].T  # (n_sub, M')
    fig = go.Figure(
        go.Heatmap(
            z=display,
            x=time_sec[::step],
            y=subcarrier_idx,
            colorscale="Viridis",
            colorbar=dict(thickness=12, title="|H|"),
        )
    )
    for start, end in fall_intervals_sec or []:
        fig.add_vrect(x0=start, x1=end, line=dict(color=FALL_BADGE_COLOR, width=2), fillcolor="rgba(0,0,0,0)")
    fig.update_layout(
        title=f"{title} ({amplitude.shape[1]} subcarriers, {step}× time decimation)",
        xaxis_title="Time (s)",
        yaxis_title="Subcarrier slot",
        height=height,
        margin=dict(t=60, b=40),
    )
    return fig


def plot_csi_traces(
    amplitude: np.ndarray,
    time_sec: np.ndarray,
    subcarrier_idx: np.ndarray,
    n_traces: int = 4,
    fall_intervals_sec: Optional[List[Tuple[float, float]]] = None,
    title: str = "RAW CSI traces",
    height: int = 300,
) -> go.Figure:
    """대표 서브캐리어 몇 개의 진폭 시계열."""
    picks = np.round(np.linspace(0, amplitude.shape[1] - 1, n_traces)).astype(int)
    fig = go.Figure()
    for i, col in enumerate(np.unique(picks)):
        fig.add_trace(
            go.Scattergl(
                x=time_sec,
                y=amplitude[:, col],
                mode="lines",
                line=dict(width=1, color=group_color(i)),
                name=f"slot {int(subcarrier_idx[col])}",
            )
        )
    for start, end in fall_intervals_sec or []:
        fig.add_vrect(x0=start, x1=end, fillcolor=FALL_COLOR, line_width=0, layer="below")
    fig.update_layout(
        title=title,
        xaxis_title="Time (s)",
        yaxis_title="Amplitude",
        height=height,
        margin=dict(t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.08),
    )
    return fig


def plot_profile_evolution(
    profiles: np.ndarray,
    window_center_sec: np.ndarray,
    y_values: np.ndarray,
    y_title: str,
    title: str,
    colorscale: str = S3_COLORSCALE,
    zmid: Optional[float] = None,
    fall_intervals_sec: Optional[List[Tuple[float, float]]] = None,
    height: int = 380,
) -> go.Figure:
    """윈도우별 1D 프로파일을 레코딩 시간축에 쌓아 변화를 보여준다.

    Args:
        profiles: (n_windows, n_bins) - 각 윈도우의 프로파일
        window_center_sec: (n_windows,) 윈도우 중심 시각
        y_values: (n_bins,) 세로축 값 (주파수 또는 lag)
    """
    fig = go.Figure(
        go.Heatmap(
            z=profiles.T,
            x=window_center_sec,
            y=y_values,
            colorscale=colorscale,
            zmid=zmid,
            colorbar=dict(thickness=12),
        )
    )
    for start, end in fall_intervals_sec or []:
        fig.add_vrect(x0=start, x1=end, line=dict(color=FALL_BADGE_COLOR, width=2), fillcolor="rgba(0,0,0,0)")
    fig.update_layout(
        title=title,
        xaxis_title="Window center time (s)",
        yaxis_title=y_title,
        height=height,
        margin=dict(t=60, b=40),
    )
    return fig


def plot_scalar_evolution(
    series: Dict[str, np.ndarray],
    window_center_sec: np.ndarray,
    y_title: str,
    title: str,
    fall_intervals_sec: Optional[List[Tuple[float, float]]] = None,
    height: int = 300,
) -> go.Figure:
    """윈도우별 스칼라 지표의 시간 변화."""
    fig = go.Figure()
    for i, (label, values) in enumerate(series.items()):
        fig.add_trace(
            go.Scattergl(
                x=window_center_sec,
                y=values,
                mode="lines+markers",
                line=dict(width=1.5, color=group_color(i)),
                marker=dict(size=4),
                name=label,
            )
        )
    for start, end in fall_intervals_sec or []:
        fig.add_vrect(x0=start, x1=end, fillcolor=FALL_COLOR, line_width=0, layer="below")
    fig.update_layout(
        title=title,
        xaxis_title="Window center time (s)",
        yaxis_title=y_title,
        height=height,
        margin=dict(t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.08),
    )
    return fig


def plot_recording_timeline(
    n_windows_total: int,
    stride_samples: int,
    fs_hz: float,
    window_samples: int,
    fall_intervals_sec: List[Tuple[float, float]],
    selected_starts_sec: np.ndarray,
    height: int = 220,
) -> go.Figure:
    """레코딩 전체 타임라인에서 낙상 구간과 선택된 윈도우 위치를 보여준다."""
    total_sec = (n_windows_total * stride_samples + window_samples) / fs_hz
    fig = go.Figure()
    for start, end in fall_intervals_sec:
        fig.add_vrect(x0=start, x1=end, fillcolor=FALL_COLOR, line_width=0, layer="below")
    window_sec = window_samples / fs_hz
    for i, start in enumerate(selected_starts_sec):
        fig.add_shape(
            type="rect",
            x0=start,
            x1=start + window_sec,
            y0=i * 0.12,
            y1=i * 0.12 + 0.1,
            line=dict(width=1, color=group_color(i)),
            fillcolor=group_color(i),
            opacity=0.35,
        )
    fig.update_layout(
        title="Recording timeline (붉은 구간 = 낙상 라벨, 막대 = 선택된 3초 윈도우)",
        xaxis_title="Time (s)",
        xaxis=dict(range=[0, total_sec]),
        yaxis=dict(visible=False, range=[-0.05, max(0.4, len(selected_starts_sec) * 0.12 + 0.1)]),
        height=height,
        margin=dict(t=60, b=40),
        showlegend=False,
    )
    return fig
