"""Shared Plotly figure builders for both app pages."""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

FALL_COLOR = "rgba(220, 20, 60, 0.15)"
NONFALL_COLOR = "rgba(120, 120, 120, 0.08)"
FALL_BADGE_COLOR = "#dc143c"
NORMAL_BADGE_COLOR = "#2e8b57"


def _slice_by_time(
    time_sec: np.ndarray,
    time_range: Optional[Tuple[float, float]],
) -> Tuple[int, int]:
    """Return (start_idx, end_idx) covering time_range, or the full array if None."""
    if time_range is None:
        return 0, len(time_sec)
    t_start, t_end = time_range
    start_idx = int(np.searchsorted(time_sec, t_start, side="left"))
    end_idx = int(np.searchsorted(time_sec, t_end, side="right"))
    return max(0, start_idx), min(len(time_sec), end_idx)


def _visible_phases(
    phase_boundaries: List[Dict[str, Any]],
    t_start: float,
    t_end: float,
) -> List[Dict[str, Any]]:
    return [
        pb for pb in phase_boundaries
        if pb["end_sec"] > t_start and pb["start_sec"] < t_end
    ]


def _add_phase_overlay(fig: go.Figure, phase_boundaries: List[Dict[str, Any]], t_start: float, t_end: float) -> None:
    for pb in _visible_phases(phase_boundaries, t_start, t_end):
        color = FALL_COLOR if pb["is_fall"] else NONFALL_COLOR
        x0 = max(pb["start_sec"], t_start)
        x1 = min(pb["end_sec"], t_end)
        fig.add_vrect(
            x0=x0, x1=x1,
            fillcolor=color, line_width=0, layer="below",
        )
        fig.add_annotation(
            x=(x0 + x1) / 2, y=1.02, yref="paper",
            text=pb["activity_name"],
            showarrow=False, font=dict(size=10),
        )


def plot_selected_streams(
    pipeline: Dict[str, Any],
    time_range: Optional[Tuple[float, float]] = None,
) -> go.Figure:
    """Raw amplitude time series for the selected subcarriers, with phase-boundary overlay."""
    time_sec = pipeline["time_sec"]
    H_S = pipeline["H_S"]
    origins = pipeline["stream_stats"].get("selected_stream_origins", [])

    start_idx, end_idx = _slice_by_time(time_sec, time_range)
    t_start = float(time_sec[start_idx]) if len(time_sec) else 0.0
    t_end = float(time_sec[end_idx - 1]) if end_idx > start_idx else t_start

    fig = go.Figure()
    for i in range(H_S.shape[1]):
        ant, sub = origins[i] if i < len(origins) else (None, None)
        name = f"Ant{ant + 1}-Sub{sub + 1}" if ant is not None else f"stream {i}"
        fig.add_trace(go.Scattergl(
            x=time_sec[start_idx:end_idx],
            y=H_S[start_idx:end_idx, i],
            mode="lines", name=name, line=dict(width=1),
        ))

    _add_phase_overlay(fig, pipeline["phase_boundaries"], t_start, t_end)

    fig.update_layout(
        title="Selected Subcarriers — Raw Amplitude",
        xaxis_title="Time (s)", yaxis_title="Amplitude",
        height=350, margin=dict(t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.08),
    )
    return fig


def plot_moving_variance(
    pipeline: Dict[str, Any],
    time_range: Optional[Tuple[float, float]] = None,
) -> go.Figure:
    """Moving-variance time series for the selected subcarriers, with phase-boundary overlay."""
    time_sec = pipeline["time_sec"]
    mv = pipeline["mv_per_stream"]
    origins = pipeline["stream_stats"].get("selected_stream_origins", [])

    start_idx, end_idx = _slice_by_time(time_sec, time_range)
    t_start = float(time_sec[start_idx]) if len(time_sec) else 0.0
    t_end = float(time_sec[end_idx - 1]) if end_idx > start_idx else t_start

    fig = go.Figure()
    for i in range(mv.shape[1]):
        ant, sub = origins[i] if i < len(origins) else (None, None)
        name = f"Ant{ant + 1}-Sub{sub + 1}" if ant is not None else f"stream {i}"
        fig.add_trace(go.Scattergl(
            x=time_sec[start_idx:end_idx],
            y=mv[start_idx:end_idx, i],
            mode="lines", name=name, line=dict(width=1),
        ))

    _add_phase_overlay(fig, pipeline["phase_boundaries"], t_start, t_end)

    fig.update_layout(
        title="Moving Variance — Selected Subcarriers",
        xaxis_title="Time (s)", yaxis_title="Variance",
        height=350, margin=dict(t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.08),
    )
    return fig


def plot_q_value_bar(stream_stats: Dict[str, Any]) -> go.Figure:
    """
    Bar chart of normalized Q(h_i) = q(h_i) / sum(q) across top-N candidate streams —
    this is the actual quantity select_streams() applies its threshold to — selected
    vs. below-threshold, with a reference line at the Q-threshold.
    """
    q_values = stream_stats.get("q_values_top", [])
    Q_normalized = stream_stats.get("Q_values_normalized", [])
    selected_indices = set(stream_stats.get("selected_stream_indices", []))
    q_threshold = stream_stats.get("q_threshold", 0.0)

    labels = [f"cand {i}" for i in range(len(Q_normalized))]
    colors = ["#d62728" if i in selected_indices else "#c7c7c7" for i in range(len(Q_normalized))]
    hover = [f"q(h)={q:.3f}" for q in q_values]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=Q_normalized, marker_color=colors, name="Q(h) normalized",
        hovertext=hover, hoverinfo="text+y",
    ))
    fig.add_hline(y=q_threshold, line_dash="dash", line_color="black",
                  annotation_text=f"threshold={q_threshold:.3f}")

    fig.update_layout(
        title=f"Normalized Q(h) per Candidate Stream (top {len(Q_normalized)})",
        xaxis_title="Candidate (sorted by q, descending)", yaxis_title="Q(h) normalized",
        height=300, margin=dict(t=60, b=40), showlegend=False,
    )
    return fig


def _phase_at_time(phase_boundaries: List[Dict[str, Any]], fs_hz: float, n_samples: int, t: float) -> Optional[Dict[str, Any]]:
    if not phase_boundaries:
        return None
    idx = min(int(round(t * fs_hz)), n_samples - 1)
    for pb in phase_boundaries:
        if pb["start_idx"] <= idx < pb["end_idx"]:
            return pb
    return phase_boundaries[-1]


def build_realtime_animation(
    pipeline: Dict[str, Any],
    window_sec: float,
    tick_sec: float = 0.1,
    speed: float = 1.0,
) -> go.Figure:
    """
    Build one self-contained animated Plotly figure for Page 2's replay.

    The trick that keeps this smooth: traces are built ONCE with the full session's
    normalized data (no per-frame data slicing/duplication). Each animation frame only
    changes the x-axis range (to scroll the trailing window) and two small annotations
    (current phase name, fall/normal badge) — a handful of numbers per frame instead of
    re-sending the windowed signal every tick. Playback itself (Play/Pause + scrub
    slider) runs entirely client-side in the browser via Plotly's native animate(),
    so there's no Streamlit rerun (and therefore no server round-trip) per frame.
    """
    time_sec = pipeline["time_sec"]
    fs_hz = pipeline["fs_hz"]
    n_samples = len(time_sec)
    duration = float(time_sec[-1]) if n_samples else 0.0

    amp = pipeline["H_S_norm"]
    mv = pipeline["mv_per_stream_norm"]
    origins = pipeline["stream_stats"].get("selected_stream_origins", [])
    n_streams = amp.shape[1]
    names = [f"Ant{a + 1}-Sub{b + 1}" for a, b in origins] if origins else [f"stream {i}" for i in range(n_streams)]
    phase_boundaries = pipeline["phase_boundaries"]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.15,
        subplot_titles=("Selected Subcarriers — Normalized Amplitude", "Moving Variance — Normalized"),
    )

    for i, name in enumerate(names):
        fig.add_trace(
            go.Scattergl(x=time_sec, y=amp[:, i], mode="lines", name=name,
                         legendgroup=name, line=dict(width=1)),
            row=1, col=1,
        )
    for i, name in enumerate(names):
        fig.add_trace(
            go.Scattergl(x=time_sec, y=mv[:, i], mode="lines", name=name,
                         legendgroup=name, showlegend=False, line=dict(width=1)),
            row=2, col=1,
        )

    frame_times = np.arange(0.0, duration + 1e-9, tick_sec)
    if len(frame_times) == 0:
        frame_times = np.array([0.0])

    frames = []
    for t in frame_times:
        x_range = [max(0.0, t - window_sec), max(t, window_sec)]
        phase = _phase_at_time(phase_boundaries, fs_hz, n_samples, t)
        phase_text = f"t={t:0.1f}s — {phase['activity_name']}" if phase else f"t={t:0.1f}s"
        is_fall = bool(phase and phase["is_fall"])

        frames.append(go.Frame(
            name=f"{t:.2f}",
            layout=go.Layout(
                xaxis=dict(range=x_range),
                xaxis2=dict(range=x_range),
                annotations=[
                    dict(xref="paper", yref="paper", x=0.0, y=1.14, xanchor="left",
                         showarrow=False, text=phase_text, font=dict(size=13)),
                    dict(xref="paper", yref="paper", x=1.0, y=1.14, xanchor="right",
                         showarrow=False, text="FALL" if is_fall else "normal",
                         font=dict(size=13, color="white"),
                         bgcolor=FALL_BADGE_COLOR if is_fall else NORMAL_BADGE_COLOR, borderpad=4),
                ],
            ),
        ))

    fig.frames = frames

    # Slider shows one tick roughly every second (independent of the finer-grained
    # frame_times used for smooth Play animation) to keep it visually readable.
    slider_stride = max(1, int(round(1.0 / tick_sec)))
    slider_times = frame_times[::slider_stride]

    initial_range = [0.0, max(window_sec, tick_sec)]
    frame_duration_ms = tick_sec * 1000.0 / max(speed, 1e-6)

    fig.update_layout(
        xaxis=dict(range=initial_range),
        xaxis2=dict(range=initial_range, title="Time (s)"),
        height=650, margin=dict(t=110, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        updatemenus=[dict(
            type="buttons", direction="left", x=0.0, y=1.22, xanchor="left", showactive=False,
            buttons=[
                dict(label="Play", method="animate", args=[None, {
                    "frame": {"duration": frame_duration_ms, "redraw": False},
                    "fromcurrent": True, "transition": {"duration": 0},
                }]),
                dict(label="Pause", method="animate", args=[[None], {
                    "frame": {"duration": 0, "redraw": False},
                    "mode": "immediate", "transition": {"duration": 0},
                }]),
            ],
        )],
        sliders=[dict(
            active=0, x=0.12, y=1.22, len=0.85,
            currentvalue=dict(prefix="t = ", suffix="s", font=dict(size=12)),
            steps=[
                dict(method="animate", label=f"{t:.0f}",
                     args=[[f"{t:.2f}"], {"frame": {"duration": 0, "redraw": False}, "mode": "immediate"}])
                for t in slider_times
            ],
        )],
    )
    return fig
