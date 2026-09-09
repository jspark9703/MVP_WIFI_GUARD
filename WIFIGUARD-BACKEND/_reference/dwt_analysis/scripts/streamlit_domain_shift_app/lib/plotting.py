"""
go.Figure 빌더. **st.* 를 절대 호출하지 않는다** (기존 두 앱의 규약).

레이아웃 규약도 기존 앱을 따른다: height 300~520, margin dict(t=50, b=40),
시계열은 go.Scattergl + line width 1, legend 는 orientation="h", y=1.02,
colorbar 는 thickness=12. (도형 안에 제목을 두지 않으므로 상단 여백은 legend 전용이다.)

색 규칙:
- 범주형(환경/클래스)은 constants 의 고정 색을 **엔티티에** 붙인다. 순서가 바뀌거나
  필터로 계열 수가 줄어도 색이 재배치되지 않는다.
- 크기(magnitude)는 단일 색상 순차 램프, 극성(polarity, 예: Cliff's δ ∈ [-1,1])은
  중립 회색 중점을 가진 발산 램프(zmid=0)를 쓴다.
- 숫자·라벨·범례 텍스트는 잉크 토큰을 쓰고 계열 색을 입히지 않는다.
- 이중 y축은 쓰지 않는다. 척도가 다른 두 지표는 행을 나눈 서브플롯으로 그린다.
- **제목을 도형 안에 넣지 않는다.** 상단 가로 legend 와 겹쳐 범례 라벨을 가리기 때문에,
  제목은 layout.meta["title"] 로 전달하고 lib/render.chart() 가 차트 위 텍스트로 그린다.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from lib.constants import (
    ANTENNA_LABELS,
    ARM_COLORS,
    ARM_LABELS,
    ENV_COLORS,
    ENV_LABELS,
    FALL_COLORS,
    STAGE_LABELS,
    STAGE_ORDER,
    env_color,
)

# scripts/07_agc_distribution.py 와 동일한 잉크/그리드 토큰
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID_COLOR = "#e1e0d9"

SEQUENTIAL_SCALE = "Viridis"       # 크기(magnitude)
DIVERGING_SCALE = "RdBu"           # 극성(polarity), zmid=0 과 함께
SELECTED_COLOR = "#d62728"
UNSELECTED_COLOR = "#c7c7c7"
THRESHOLD_COLOR = "#52514e"


def _base_layout(fig: go.Figure, title: str, x_title: str = "", y_title: str = "",
                 height: int = 350, showlegend: bool = True) -> go.Figure:
    # 제목을 도형 안에 그리지 않는다 — 상단 가로 legend 와 같은 띠를 차지해 범례 라벨을 가린다.
    # 대신 layout.meta 에 실어 보내고 lib/render.chart() 가 차트 **위 텍스트**로 렌더한다.
    fig.update_layout(
        meta={"title": title},
        xaxis_title=x_title, yaxis_title=y_title,
        height=height, margin=dict(t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        showlegend=showlegend,
        font=dict(color=INK_SECONDARY),
    )
    fig.update_xaxes(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR)
    fig.update_yaxes(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR)
    return fig


def _empty(title: str, message: str = "표시할 데이터가 없습니다", height: int = 300) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False,
                       font=dict(color=INK_MUTED, size=13),
                       xref="paper", yref="paper", x=0.5, y=0.5)
    return _base_layout(fig, title, height=height, showlegend=False)


def _group_color(group_col: str, value: Any) -> str:
    if group_col == "env":
        return env_color(value)
    if group_col in ("fall_label", "is_fall"):
        key = value if isinstance(value, str) else ("Fall" if value else "Non-fall")
        return FALL_COLORS.get(key, INK_MUTED)
    return INK_MUTED


def _group_label(group_col: str, value: Any) -> str:
    if group_col == "env":
        return ENV_LABELS.get(int(value), f"E{value}")
    if group_col == "antenna":
        return ANTENNA_LABELS.get(int(value), f"Ant{value}")
    return str(value)


# --- 분포 비교 ---------------------------------------------------------------

def plot_group_box(df: pd.DataFrame, value_col: str, group_col: str = "env",
                   title: Optional[str] = None, y_title: Optional[str] = None,
                   height: int = 350) -> go.Figure:
    if df.empty or value_col not in df.columns:
        return _empty(title or value_col)

    fig = go.Figure()
    for g in sorted(df[group_col].dropna().unique()):
        vals = df.loc[df[group_col] == g, value_col].to_numpy(dtype=float)
        vals = vals[np.isfinite(vals)]
        if not vals.size:
            continue
        fig.add_trace(go.Box(
            y=vals, name=_group_label(group_col, g),
            marker_color=_group_color(group_col, g), boxmean=True,
            line=dict(width=1.5),
        ))
    return _base_layout(fig, title or value_col, group_col, y_title or value_col, height)


def plot_group_ecdf(df: pd.DataFrame, value_col: str, group_col: str = "env",
                    title: Optional[str] = None, height: int = 350) -> go.Figure:
    """
    ECDF 겹쳐 그리기 — 분포 시프트를 가장 직접적으로 보여주는 형태.
    박스플롯은 요약통계로 압축하지만 ECDF 는 KS D(두 곡선의 최대 수직 거리)를
    눈으로 읽게 해준다.
    """
    if df.empty or value_col not in df.columns:
        return _empty(title or value_col)

    fig = go.Figure()
    for g in sorted(df[group_col].dropna().unique()):
        vals = df.loc[df[group_col] == g, value_col].to_numpy(dtype=float)
        vals = np.sort(vals[np.isfinite(vals)])
        if not vals.size:
            continue
        y = np.arange(1, vals.size + 1) / vals.size
        fig.add_trace(go.Scattergl(
            x=vals, y=y, mode="lines", line_shape="hv",
            name=f"{_group_label(group_col, g)} (n={vals.size})",
            line=dict(width=2, color=_group_color(group_col, g)),
        ))
    return _base_layout(fig, title or f"{value_col} ECDF", value_col, "누적 비율", height)


def plot_grouped_box_2factor(df: pd.DataFrame, value_col: str, x_col: str = "fall_label",
                             color_col: str = "env", title: Optional[str] = None,
                             height: int = 380) -> go.Figure:
    """x 축은 활동(클래스), 색은 환경 — 두 격차를 한 화면에서 비교."""
    if df.empty or value_col not in df.columns:
        return _empty(title or value_col)

    fig = go.Figure()
    for g in sorted(df[color_col].dropna().unique()):
        sub = df[df[color_col] == g]
        fig.add_trace(go.Box(
            x=sub[x_col].astype(str), y=sub[value_col].astype(float),
            name=_group_label(color_col, g), marker_color=_group_color(color_col, g),
            boxmean=True, line=dict(width=1.5),
        ))
    fig.update_layout(boxmode="group")
    return _base_layout(fig, title or value_col, x_col, value_col, height)


def plot_gap_histogram(meta_df: pd.DataFrame, value_col: str = "median_gap_ms",
                       group_col: str = "env", log_x: bool = True,
                       nominal: Optional[float] = None, height: int = 360) -> go.Figure:
    """
    패킷 간격 히스토그램. 정상 3.125 ms 와 병리적 ~100 ms 의 이봉성을 드러내려면
    로그 x축이 필요하다 (선형에서는 정상 봉우리가 한 칸으로 뭉갠다).
    """
    if meta_df.empty or value_col not in meta_df.columns:
        return _empty("패킷 간격 분포")

    fig = go.Figure()
    for g in sorted(meta_df[group_col].dropna().unique()):
        vals = meta_df.loc[meta_df[group_col] == g, value_col].to_numpy(dtype=float)
        vals = vals[np.isfinite(vals) & (vals > 0)]
        if not vals.size:
            continue
        fig.add_trace(go.Histogram(
            x=np.log10(vals) if log_x else vals,
            name=f"{_group_label(group_col, g)} (n={vals.size})",
            marker_color=_group_color(group_col, g), opacity=0.65, nbinsx=60,
        ))
    fig.update_layout(barmode="overlay")

    if nominal and nominal > 0:
        xpos = np.log10(nominal) if log_x else nominal
        fig.add_vline(x=xpos, line=dict(color=THRESHOLD_COLOR, width=1.5, dash="dash"),
                      annotation_text=f"정상 {nominal:.3f} ms",
                      annotation_font=dict(color=INK_SECONDARY, size=11))

    x_title = "log10(패킷 간격 ms)" if log_x else "패킷 간격 (ms)"
    return _base_layout(fig, "파일별 패킷 간격 중앙값 분포", x_title, "파일 수", height)


def plot_categorical_share_bar(df: pd.DataFrame, cat_col: str, group_col: str = "env",
                               title: Optional[str] = None, labels: Optional[Dict] = None,
                               height: int = 340) -> go.Figure:
    """그룹별 범주 점유율 (합=1). 선택 안테나 분포 / 최강 안테나 분포 등."""
    if df.empty or cat_col not in df.columns:
        return _empty(title or cat_col)

    cats = sorted(df[cat_col].dropna().unique())
    fig = go.Figure()
    for g in sorted(df[group_col].dropna().unique()):
        sub = df[df[group_col] == g]
        counts = sub[cat_col].value_counts(normalize=True)
        fig.add_trace(go.Bar(
            x=[(labels or {}).get(c, str(c)) for c in cats],
            y=[float(counts.get(c, 0.0)) for c in cats],
            name=_group_label(group_col, g), marker_color=_group_color(group_col, g),
        ))
    fig.update_layout(barmode="group")
    return _base_layout(fig, title or f"{cat_col} 점유율", cat_col, "비율", height)


def plot_scatter_with_fit(df: pd.DataFrame, x_col: str, y_col: str,
                          group_col: str = "env", title: Optional[str] = None,
                          height: int = 400) -> go.Figure:
    """그룹별 산점도 + 최소제곱 직선. 두 지표가 같은 축을 공유하는지 확인용."""
    if df.empty or x_col not in df.columns or y_col not in df.columns:
        return _empty(title or f"{y_col} vs {x_col}")

    fig = go.Figure()
    for g in sorted(df[group_col].dropna().unique()):
        sub = df[df[group_col] == g]
        x = sub[x_col].to_numpy(dtype=float)
        y = sub[y_col].to_numpy(dtype=float)
        ok = np.isfinite(x) & np.isfinite(y)
        x, y = x[ok], y[ok]
        if x.size < 2:
            continue
        color = _group_color(group_col, g)
        label = _group_label(group_col, g)
        fig.add_trace(go.Scattergl(
            x=x, y=y, mode="markers", name=label,
            marker=dict(size=6, color=color, line=dict(width=1, color="white")),
        ))
        if np.ptp(x) > 1e-12:
            slope, intercept = np.polyfit(x, y, 1)
            xs = np.array([x.min(), x.max()])
            fig.add_trace(go.Scattergl(
                x=xs, y=slope * xs + intercept, mode="lines",
                name=f"{label} 기울기 {slope:.3g}",
                line=dict(width=2, color=color, dash="dot"),
            ))
    return _base_layout(fig, title or f"{y_col} vs {x_col}", x_col, y_col, height)


def plot_subcarrier_profile(sub_long: pd.DataFrame, value_col: str = "amp_mean",
                            group_col: str = "env", antenna: Optional[int] = None,
                            title: Optional[str] = None, height: int = 380) -> go.Figure:
    """
    서브캐리어별 평균 ± IQR 밴드, 환경별 1개 선. 각 방의 주파수 선택적 페이딩 지문.
    """
    if sub_long.empty or value_col not in sub_long.columns:
        return _empty(title or "서브캐리어 프로파일")

    df = sub_long if antenna is None else sub_long[sub_long["antenna"] == antenna]
    if df.empty:
        return _empty(title or "서브캐리어 프로파일")

    fig = go.Figure()
    for g in sorted(df[group_col].dropna().unique()):
        agg = (df[df[group_col] == g]
               .groupby("subcarrier")[value_col]
               .agg(med="median",
                    lo=lambda s: np.nanpercentile(s, 25),
                    hi=lambda s: np.nanpercentile(s, 75))
               .reset_index())
        if agg.empty:
            continue
        color = _group_color(group_col, g)
        x = agg["subcarrier"].to_numpy() + 1  # 1-based 표시
        fig.add_trace(go.Scatter(
            x=np.concatenate([x, x[::-1]]),
            y=np.concatenate([agg["hi"].to_numpy(), agg["lo"].to_numpy()[::-1]]),
            fill="toself", fillcolor=color, opacity=0.15,
            line=dict(width=0), hoverinfo="skip", showlegend=False,
        ))
        fig.add_trace(go.Scattergl(
            x=x, y=agg["med"].to_numpy(), mode="lines",
            name=_group_label(group_col, g), line=dict(width=2, color=color),
        ))

    suffix = "" if antenna is None else f" — {ANTENNA_LABELS.get(antenna, antenna)}"
    return _base_layout(fig, (title or f"서브캐리어별 {value_col}") + suffix,
                        "서브캐리어 (1-30)", value_col, height)


def plot_antenna_amplitude_share(records: pd.DataFrame, group_col: str = "env",
                                 height: int = 340) -> go.Figure:
    """환경별 안테나 진폭 점유율 누적 막대."""
    cols = ["amp_ant1_mean", "amp_ant2_mean", "amp_ant3_mean"]
    if records.empty or any(c not in records.columns for c in cols):
        return _empty("안테나 진폭 점유율")

    fig = go.Figure()
    groups = sorted(records[group_col].dropna().unique())
    means = records.groupby(group_col)[cols].mean()
    totals = means.sum(axis=1).replace(0, np.nan)

    for i, c in enumerate(cols):
        fig.add_trace(go.Bar(
            x=[_group_label(group_col, g) for g in groups],
            y=[float(means.loc[g, c] / totals.loc[g]) for g in groups],
            name=ANTENNA_LABELS[i],
            marker=dict(line=dict(width=2, color="white")),  # 2px 서피스 갭
        ))
    fig.update_layout(barmode="stack")
    return _base_layout(fig, "환경별 안테나 진폭 점유율", group_col, "비율", height)


# --- q 파이프라인 ------------------------------------------------------------

def plot_stage_signals(signals: Dict[str, Any], height: int = 720) -> go.Figure:
    """
    한 파일의 단계별 신호. 2행에서 **리샘플러가 만들어낸 보간 샘플**을
    원본 샘플 마커와 대비시켜 보여주는 것이 핵심이다 — 병리적 파일에서는
    마커가 드문드문 찍히고 그 사이가 전부 선형 보간이라는 게 눈에 보인다.
    """
    if not signals:
        return _empty("단계별 신호", height=height)

    fs = float(signals.get("fs_hz", 320.0))
    ant = ANTENNA_LABELS.get(signals.get("best_antenna", 0), "Ant?")
    sub = int(signals.get("best_subcarrier", 0)) + 1

    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        subplot_titles=(
            f"S0 raw 진폭 — {ant} Sub{sub} (불규칙 타임스탬프)",
            "S1 리샘플 320 Hz (● = 원본 샘플 위치, 그 사이는 보간)",
            "S2 대역통과 후",
            "S5 최종 대표신호 (z-score)",
        ),
    )

    raw_t = np.asarray(signals["raw_time_sec"], dtype=float)
    fig.add_trace(go.Scattergl(x=raw_t, y=signals["raw_amp"], mode="lines",
                               line=dict(width=1, color=ENV_COLORS[1]), name="raw"),
                  row=1, col=1)

    res = np.asarray(signals["resampled"], dtype=float)
    t_res = np.arange(res.size) / fs
    fig.add_trace(go.Scattergl(x=t_res, y=res, mode="lines",
                               line=dict(width=1, color=ENV_COLORS[2]), name="resampled"),
                  row=2, col=1)

    orig_idx = np.asarray(signals.get("original_sample_idx", []), dtype=int)
    orig_idx = np.unique(orig_idx[(orig_idx >= 0) & (orig_idx < res.size)])
    if orig_idx.size:
        fig.add_trace(go.Scattergl(
            x=orig_idx / fs, y=res[orig_idx], mode="markers",
            marker=dict(size=4, color=INK_PRIMARY), name="원본 샘플",
        ), row=2, col=1)

    flt = np.asarray(signals["filtered"], dtype=float)
    fig.add_trace(go.Scattergl(x=np.arange(flt.size) / fs, y=flt, mode="lines",
                               line=dict(width=1, color=ENV_COLORS[3]), name="filtered"),
                  row=3, col=1)

    rep = np.asarray(signals["rep"], dtype=float)
    fig.add_trace(go.Scattergl(x=np.arange(rep.size) / fs, y=rep, mode="lines",
                               line=dict(width=1, color=SELECTED_COLOR), name="대표신호"),
                  row=4, col=1)

    fig.update_layout(
        meta={"title": f"단계별 신호 — {ant} Sub{sub}"},
        height=height, margin=dict(t=100, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.04),
        font=dict(color=INK_SECONDARY),
    )
    fig.update_xaxes(gridcolor=GRID_COLOR)
    fig.update_yaxes(gridcolor=GRID_COLOR)
    fig.update_xaxes(title_text="시간 (s)", row=4, col=1)
    return fig


def plot_q_landscape(q_map: np.ndarray, title: str = "q landscape",
                     zmin: Optional[float] = None, zmax: Optional[float] = None,
                     height: int = 320) -> go.Figure:
    """(안테나 × 서브캐리어) q 히트맵. q 는 크기이므로 단일 램프 순차 스케일."""
    q = np.asarray(q_map, dtype=float)
    if q.size == 0 or not np.any(np.isfinite(q)):
        return _empty(title, height=height)

    fig = go.Figure(go.Heatmap(
        z=q, x=[str(i + 1) for i in range(q.shape[1])],
        y=[ANTENNA_LABELS.get(i, f"Ant{i+1}") for i in range(q.shape[0])],
        colorscale=SEQUENTIAL_SCALE, zmin=zmin, zmax=zmax,
        colorbar=dict(thickness=12, title="q"),
        hovertemplate="%{y} Sub%{x}<br>q=%{z:.3f}<extra></extra>",
    ))
    return _base_layout(fig, title, "서브캐리어", "", height, showlegend=False)


def plot_q_bar(q_top: np.ndarray, Q_top: np.ndarray, q_threshold: float,
               selected_count: int, top_origins: Optional[Sequence[Tuple[int, int]]] = None,
               height: int = 360) -> go.Figure:
    """
    상위 후보의 정규화 Q 막대 + 1/n_top 임계선.

    Q-임계는 **내림차순 정렬된** 배열에 적용되므로 선택 집합은 항상 prefix 다.
    따라서 막대 i 의 선택 여부는 ``i < selected_count`` 로 판정한다.
    (풀 인덱스인 selected_stream_indices 로 판정하면 틀린다 — 인덱스 공간이 다르다.)
    """
    Q = np.asarray(Q_top, dtype=float)
    if Q.size == 0:
        return _empty("스트림 Q 분포", height=height)

    colors = [SELECTED_COLOR if i < selected_count else UNSELECTED_COLOR for i in range(Q.size)]
    if top_origins is not None and len(top_origins) == Q.size:
        labels = [f"{ANTENNA_LABELS.get(a, a)}·S{s+1}" for a, s in top_origins]
    else:
        labels = [str(i + 1) for i in range(Q.size)]

    q_vals = np.asarray(q_top, dtype=float)
    fig = go.Figure(go.Bar(
        x=labels, y=Q, marker_color=colors,
        customdata=np.stack([q_vals, np.arange(Q.size)], axis=1),
        hovertemplate="%{x}<br>Q=%{y:.4f}<br>q=%{customdata[0]:.3f}<br>순위 %{customdata[1]}<extra></extra>",
        name="Q(h)",
    ))
    fig.add_hline(y=q_threshold, line=dict(color=THRESHOLD_COLOR, width=1.5, dash="dash"),
                  annotation_text=f"Q 임계 1/n = {q_threshold:.4f}",
                  annotation_font=dict(color=INK_SECONDARY, size=11))
    return _base_layout(
        fig, f"스트림 선택 — 상위 {Q.size}개 중 {selected_count}개 선택 (빨강)",
        "후보 (q 내림차순)", "정규화 Q", height, showlegend=False,
    )


def plot_eigen_spectrum(eigenvalues: np.ndarray, threshold: float,
                        height: int = 340) -> go.Figure:
    """PCA 고유값 스펙트럼(내림차순) + 후보 임계선. 로그 y축."""
    ev = np.asarray(eigenvalues, dtype=float)
    ev = np.sort(ev[np.isfinite(ev)])[::-1]
    if ev.size == 0:
        return _empty("고유값 스펙트럼", height=height)

    pos = ev[ev > 0]
    fig = go.Figure(go.Bar(x=[str(i + 1) for i in range(pos.size)], y=pos,
                           marker_color=ENV_COLORS[1], name="고유값"))
    if threshold == threshold and threshold > 0:
        fig.add_hline(y=threshold, line=dict(color=THRESHOLD_COLOR, width=1.5, dash="dash"),
                      annotation_text=f"후보 임계 {threshold:.3g}",
                      annotation_font=dict(color=INK_SECONDARY, size=11))
    fig.update_yaxes(type="log")
    return _base_layout(fig, "PCA 고유값 스펙트럼", "성분 (큰 순)", "고유값 (log)",
                        height, showlegend=False)


def plot_pc_q_bar(q_pc: np.ndarray, Q_pc: np.ndarray, pc_q_threshold: float,
                  selected_mask: Sequence[bool], height: int = 340) -> go.Figure:
    """후보 PC 별 정규화 Q + 1/(n_c−1) 임계선. 프로덕션은 이 값을 전부 버린다."""
    Q = np.asarray(Q_pc, dtype=float)
    if Q.size == 0:
        return _empty("PC 선택", height=height)

    colors = [SELECTED_COLOR if bool(m) else UNSELECTED_COLOR for m in selected_mask]
    fig = go.Figure(go.Bar(
        x=[f"PC{i+1}" for i in range(Q.size)], y=Q, marker_color=colors,
        customdata=np.asarray(q_pc, dtype=float),
        hovertemplate="%{x}<br>Q(p)=%{y:.4f}<br>q(p)=%{customdata:.3f}<extra></extra>",
    ))
    if pc_q_threshold == pc_q_threshold:
        fig.add_hline(y=pc_q_threshold, line=dict(color=THRESHOLD_COLOR, width=1.5, dash="dash"),
                      annotation_text=f"PC 임계 1/(n_c−1) = {pc_q_threshold:.3f}",
                      annotation_font=dict(color=INK_SECONDARY, size=11))
    return _base_layout(fig, "PC 선택 — 후보별 정규화 Q(p)", "후보 PC", "정규화 Q(p)",
                        height, showlegend=False)


def plot_stage_q_lines(stage_df: pd.DataFrame, group_col: str = "env",
                       height: int = 400) -> go.Figure:
    """
    파이프라인 단계별 q 중앙값 + IQR 밴드, 환경별 1개 선.
    "amfall 전처리 과정 중 q_value 변화"에 대한 직접적인 답.
    """
    if stage_df.empty or "stage" not in stage_df.columns:
        return _empty("단계별 q 변화", height=height)

    order = [s for s in STAGE_ORDER if s in set(stage_df["stage"])]
    x_labels = [STAGE_LABELS.get(s, s) for s in order]

    fig = go.Figure()
    for g in sorted(stage_df[group_col].dropna().unique()):
        sub = stage_df[stage_df[group_col] == g]
        med, lo, hi = [], [], []
        for s in order:
            v = sub.loc[sub["stage"] == s, "q"].to_numpy(dtype=float)
            v = v[np.isfinite(v)]
            if v.size:
                med.append(float(np.median(v)))
                lo.append(float(np.percentile(v, 25)))
                hi.append(float(np.percentile(v, 75)))
            else:
                med.append(np.nan); lo.append(np.nan); hi.append(np.nan)

        color = _group_color(group_col, g)
        fig.add_trace(go.Scatter(
            x=x_labels + x_labels[::-1], y=hi + lo[::-1],
            fill="toself", fillcolor=color, opacity=0.13,
            line=dict(width=0), hoverinfo="skip", showlegend=False,
        ))
        fig.add_trace(go.Scatter(
            x=x_labels, y=med, mode="lines+markers",
            name=_group_label(group_col, g),
            line=dict(width=2, color=color), marker=dict(size=8, color=color),
        ))
    return _base_layout(fig, "파이프라인 단계별 q (중앙값 ± IQR)", "단계", "q", height)


def plot_stage_gap_bars(gap_df: pd.DataFrame, height: int = 340) -> go.Figure:
    """
    단계별 **환경 격차 크기** (env 쌍 최대 |Cliff's δ|). q 절대값이 아니라
    '환경끼리 얼마나 벌어졌는가'를 보는 차트 — 시프트가 파이프라인을 거치며
    커지는지 작아지는지가 여기서 드러난다.
    """
    if gap_df.empty or "stage" not in gap_df.columns:
        return _empty("단계별 환경 격차", height=height)

    order = [s for s in STAGE_ORDER if s in set(gap_df["stage"])]
    vals = [float(gap_df.loc[gap_df["stage"] == s, "env_gap"].iloc[0]) if
            (gap_df["stage"] == s).any() else np.nan for s in order]

    fig = go.Figure(go.Bar(
        x=[STAGE_LABELS.get(s, s) for s in order], y=vals,
        marker_color=ENV_COLORS[1],
        hovertemplate="%{x}<br>최대 |δ| = %{y:.3f}<extra></extra>",
    ))
    for bound, label in ((0.147, "negligible"), (0.330, "small"), (0.474, "medium")):
        fig.add_hline(y=bound, line=dict(color=GRID_COLOR, width=1, dash="dot"),
                      annotation_text=label, annotation_position="right",
                      annotation_font=dict(color=INK_MUTED, size=10))
    return _base_layout(fig, "단계별 환경 격차 (env 쌍 최대 |Cliff's δ|)",
                        "단계", "최대 |δ|", height, showlegend=False)


def plot_q_landscape_group_mean(q_long: pd.DataFrame, group_col: str = "env",
                                height: int = 380) -> go.Figure:
    """환경별 '순위 vs 정규화 q' 곡선. 평평할수록 임계를 통과하는 스트림이 많아진다."""
    if q_long.empty or "rank_filt" not in q_long.columns:
        return _empty("q landscape 모양", height=height)

    df = q_long[q_long["rank_filt"] >= 0]
    if df.empty:
        return _empty("q landscape 모양", height=height)

    fig = go.Figure()
    for g in sorted(df[group_col].dropna().unique()):
        agg = (df[df[group_col] == g].groupby("rank_filt")["Q_filt"]
               .median().reset_index().sort_values("rank_filt"))
        fig.add_trace(go.Scattergl(
            x=agg["rank_filt"] + 1, y=agg["Q_filt"], mode="lines",
            name=_group_label(group_col, g),
            line=dict(width=2, color=_group_color(group_col, g)),
        ))
    return _base_layout(fig, "q landscape 모양 — 순위별 정규화 Q 중앙값",
                        "q 순위", "정규화 Q", height)


def plot_selection_heatmap(q_long_selected: pd.DataFrame, row_col: str = "env",
                           height: int = 320) -> go.Figure:
    """(환경 × 서브캐리어) 선택 빈도 — 행별로 정규화해 환경 간 모양을 비교."""
    if q_long_selected.empty or "subcarrier" not in q_long_selected.columns:
        return _empty("서브캐리어 선택 분포", height=height)

    tab = pd.crosstab(q_long_selected[row_col], q_long_selected["subcarrier"], normalize="index")
    if tab.empty:
        return _empty("서브캐리어 선택 분포", height=height)

    full = tab.reindex(columns=range(30), fill_value=0.0)
    fig = go.Figure(go.Heatmap(
        z=full.to_numpy(), x=[str(c + 1) for c in full.columns],
        y=[_group_label(row_col, r) for r in full.index],
        colorscale=SEQUENTIAL_SCALE, colorbar=dict(thickness=12, title="비율"),
        hovertemplate="%{y} Sub%{x}<br>선택 비율 %{z:.3f}<extra></extra>",
    ))
    return _base_layout(fig, "환경별 선택 서브캐리어 분포", "서브캐리어", "",
                        height, showlegend=False)


# --- FallDeFi ----------------------------------------------------------------

def plot_falldefi_spectrogram(arrays: Dict[str, Any], show_curves: bool = True,
                              height: int = 420) -> go.Figure:
    """
    FallDeFi 스펙트로그램 + 극단/몸통 주파수 곡선 + 검출된 이벤트 구간.
    스펙트로그램 크기는 단일 색상 순차 램프, 두 곡선은 고정 색으로 구분한다.
    """
    if not arrays:
        return _empty("FallDeFi 스펙트로그램", height=height)

    freqs = np.asarray(arrays["freqs"], dtype=float)
    times = np.asarray(arrays["times"], dtype=float)
    mag = np.asarray(arrays["mag"], dtype=float)

    fig = go.Figure(go.Heatmap(
        z=20.0 * np.log10(mag + 1e-12), x=times, y=freqs,
        colorscale=SEQUENTIAL_SCALE, colorbar=dict(thickness=12, title="dB"),
        hovertemplate="t=%{x:.2f}s  f=%{y:.0f}Hz<br>%{z:.1f} dB<extra></extra>",
    ))

    if show_curves:
        ev0, ev1 = int(arrays.get("event_slice_start", 0)), int(arrays.get("event_slice_stop", 0))
        t_ev = times[ev0:ev1] if ev1 > ev0 else times
        for key, color, name in (("extreme_curve", SELECTED_COLOR, "극단 주파수"),
                                 ("torso_curve", "#ffffff", "몸통 주파수")):
            c = np.asarray(arrays.get(key, []), dtype=float)
            if c.size and c.size == len(t_ev):
                fig.add_trace(go.Scattergl(
                    x=t_ev, y=c, mode="lines", name=name,
                    line=dict(width=2, color=color),
                ))

    if arrays.get("event_end_sec", 0) > arrays.get("event_start_sec", 0):
        fig.add_vrect(x0=arrays["event_start_sec"], x1=arrays["event_end_sec"],
                      line=dict(width=1, color=SELECTED_COLOR), fillcolor="rgba(0,0,0,0)",
                      annotation_text="검출 이벤트",
                      annotation_font=dict(color=SELECTED_COLOR, size=11))

    return _base_layout(fig, "FallDeFi 평균 스펙트로그램 (PC별 STFT 평균)",
                        "시간 (s)", "주파수 (Hz)", height)


def plot_power_burst_curve(arrays: Dict[str, Any], height: int = 300) -> go.Figure:
    """논문 식 (5): 5-25 Hz 대역 합 PBC 와 임계선, 검출된 이벤트 구간."""
    if not arrays:
        return _empty("Power Burst Curve", height=height)

    times = np.asarray(arrays["times"], dtype=float)
    pbc = np.asarray(arrays["pbc"], dtype=float)
    thr = float(arrays.get("pbc_threshold", 0.0))

    fig = go.Figure()
    fig.add_trace(go.Scattergl(x=times, y=pbc, mode="lines", name="PBC (5-25 Hz)",
                               line=dict(width=2, color=ENV_COLORS[1])))
    fig.add_hline(y=thr, line=dict(color=THRESHOLD_COLOR, width=1.5, dash="dash"),
                  annotation_text=f"PBCth = {thr:.3g}",
                  annotation_font=dict(color=INK_SECONDARY, size=11))
    if arrays.get("event_end_sec", 0) > arrays.get("event_start_sec", 0):
        fig.add_vrect(x0=arrays["event_start_sec"], x1=arrays["event_end_sec"],
                      fillcolor=SELECTED_COLOR, opacity=0.12, line=dict(width=0))
    return _base_layout(fig, "Power Burst Curve — 이벤트 검출", "시간 (s)", "진폭 합",
                        height)


def plot_of_sf_gap_scatter(gap_df: pd.DataFrame, sf_metrics: Sequence[str],
                           height: int = 440) -> go.Figure:
    """
    FallDeFi 피처의 활동 격차(x) vs 환경 격차(y) 산점도.

    대각선 아래(환경 < 활동)에 있어야 도메인 간 일반화에 쓸 만한 피처다.
    논문이 환경 강건하다고 지목한 SF 는 OF 보다 아래쪽에 몰려야 한다 — 그 주장이
    이 데이터셋에서도 성립하는지 한 화면에서 판정할 수 있다.
    """
    if gap_df.empty:
        return _empty("FallDeFi OF vs SF 격차", height=height)

    df = gap_df.dropna(subset=["env_gap", "class_gap"]).copy()
    if df.empty:
        return _empty("FallDeFi OF vs SF 격차", height=height)
    df["is_sf"] = df["metric"].isin(list(sf_metrics))

    fig = go.Figure()
    lim = float(max(df["env_gap"].max(), df["class_gap"].max(), 0.1)) * 1.1
    fig.add_trace(go.Scattergl(
        x=[0, lim], y=[0, lim], mode="lines", name="환경 = 활동",
        line=dict(width=1.5, color=INK_MUTED, dash="dash"), hoverinfo="skip",
    ))
    for is_sf, color, name, symbol in (
        (True, SELECTED_COLOR, "SF (논문: 환경 강건)", "star"),
        (False, INK_MUTED, "OF only", "circle"),
    ):
        sub = df[df["is_sf"] == is_sf]
        if sub.empty:
            continue
        fig.add_trace(go.Scattergl(
            x=sub["class_gap"], y=sub["env_gap"], mode="markers+text",
            name=name, text=[m.replace("fd_", "") for m in sub["metric"]],
            textposition="top center", textfont=dict(size=9, color=INK_SECONDARY),
            marker=dict(size=12 if is_sf else 9, color=color, symbol=symbol,
                        line=dict(width=1, color="white")),
            hovertemplate="%{text}<br>활동 격차 %{x:.3f}<br>환경 격차 %{y:.3f}<extra></extra>",
        ))
    return _base_layout(fig, "FallDeFi 피처: 활동 격차 vs 환경 격차 (|Cliff's δ|)",
                        "활동 격차 (fall vs non-fall)", "환경 격차 (env 쌍 최대)", height)


def plot_of_sf_summary_bar(gap_df: pd.DataFrame, sf_metrics: Sequence[str],
                           height: int = 360) -> go.Figure:
    """SF 묶음과 OF-only 묶음의 평균 환경/활동 격차 비교 — 논문 주장의 요약 판정."""
    if gap_df.empty:
        return _empty("SF vs OF-only 요약", height=height)

    df = gap_df.dropna(subset=["env_gap", "class_gap"]).copy()
    if df.empty:
        return _empty("SF vs OF-only 요약", height=height)
    df["group"] = np.where(df["metric"].isin(list(sf_metrics)), "SF", "OF only")

    agg = df.groupby("group")[["env_gap", "class_gap"]].mean().reindex(["SF", "OF only"]).dropna()
    if agg.empty:
        return _empty("SF vs OF-only 요약", height=height)

    fig = go.Figure()
    fig.add_trace(go.Bar(x=list(agg.index), y=agg["env_gap"], name="환경 격차 (낮을수록 강건)",
                         marker_color=ENV_COLORS[1]))
    fig.add_trace(go.Bar(x=list(agg.index), y=agg["class_gap"], name="활동 격차 (높을수록 유용)",
                         marker_color=FALL_COLORS["Fall"]))
    fig.update_layout(barmode="group")
    return _base_layout(fig, "SF vs OF-only — 평균 격차", "피처 묶음", "평균 |Cliff's δ|", height)


# --- 종합 -------------------------------------------------------------------

def plot_divergence_heatmap(table: pd.DataFrame, value_col: str = "ks_d",
                            height: int = 520) -> go.Figure:
    """
    (지표 × env쌍) 발산 행렬.

    KS D 와 표준화 Wasserstein 은 크기(0 이상)이므로 순차 램프,
    Cliff's δ 는 극성([-1,1])이므로 중립 중점 발산 램프를 쓴다.
    """
    if table.empty or value_col not in table.columns:
        return _empty("발산 행렬", height=height)

    t = table.copy()
    t["pair"] = t["group_a"].map(lambda v: _group_label("env", v)) + "–" + \
                t["group_b"].map(lambda v: _group_label("env", v))
    mat = t.pivot_table(index="metric", columns="pair", values=value_col, aggfunc="first")
    if mat.empty:
        return _empty("발산 행렬", height=height)

    diverging = value_col == "cliffs_delta"
    fig = go.Figure(go.Heatmap(
        z=mat.to_numpy(), x=list(mat.columns), y=list(mat.index),
        colorscale=DIVERGING_SCALE if diverging else SEQUENTIAL_SCALE,
        zmid=0.0 if diverging else None,
        zmin=-1.0 if diverging else 0.0, zmax=1.0 if diverging else None,
        colorbar=dict(thickness=12, title=value_col),
        hovertemplate="%{y}<br>%{x}<br>" + value_col + "=%{z:.3f}<extra></extra>",
    ))
    return _base_layout(fig, f"도메인 시프트 발산 행렬 ({value_col})",
                        "환경 쌍", "", height, showlegend=False)


def plot_gap_comparison_bar(gap_df: pd.DataFrame, height: int = 440) -> go.Figure:
    """
    지표별 환경 격차 vs 활동 격차. 클래스 내부에서 재계산한 환경 격차를
    마커로 겹쳐, 격차가 클래스 불균형 때문에 생긴 착시가 아님을 보인다.
    """
    if gap_df.empty:
        return _empty("환경 격차 vs 활동 격차", height=height)

    df = gap_df.dropna(subset=["env_gap", "class_gap"]).copy()
    if df.empty:
        return _empty("환경 격차 vs 활동 격차", height=height)
    df = df.sort_values("env_gap", ascending=True)

    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["env_gap"], y=df["metric"], orientation="h",
                         name="환경 격차 (max |δ|)", marker_color=ENV_COLORS[1]))
    fig.add_trace(go.Bar(x=df["class_gap"], y=df["metric"], orientation="h",
                         name="활동 격차 (fall vs non-fall |δ|)",
                         marker_color=FALL_COLORS["Fall"]))
    for col, symbol, label in (("env_gap_within_fall", "diamond", "fall 내부 환경격차"),
                               ("env_gap_within_nonfall", "square", "non-fall 내부 환경격차")):
        if col in df.columns:
            fig.add_trace(go.Scatter(
                x=df[col], y=df["metric"], mode="markers", name=label,
                marker=dict(symbol=symbol, size=9, color=INK_PRIMARY,
                            line=dict(width=1, color="white")),
            ))
    fig.update_layout(barmode="group")
    return _base_layout(fig, "환경 격차 vs 활동 격차 (|Cliff's δ|)",
                        "|δ|", "", height)


def plot_confusion(conf_df: pd.DataFrame, height: int = 380) -> go.Figure:
    """혼동행렬 — 개수는 크기이므로 순차 램프."""
    if conf_df is None or conf_df.empty:
        return _empty("혼동행렬", height=height)
    fig = go.Figure(go.Heatmap(
        z=conf_df.to_numpy(), x=list(conf_df.columns), y=list(conf_df.index),
        colorscale=SEQUENTIAL_SCALE, colorbar=dict(thickness=12, title="개수"),
        text=conf_df.to_numpy(), texttemplate="%{text}",
        hovertemplate="%{y} → %{x}<br>%{z}건<extra></extra>",
    ))
    return _base_layout(fig, "환경 분류 혼동행렬 (피험자 LOGO)", "예측", "실제",
                        height, showlegend=False)


def plot_permutation_null(observed: float, perm_scores: np.ndarray, chance: float,
                          height: int = 320) -> go.Figure:
    """순열 귀무분포 vs 관측 정확도. n 이 작을 때 '우연 이상'인지 판정하는 근거."""
    perm = np.asarray(perm_scores, dtype=float)
    perm = perm[np.isfinite(perm)]
    fig = go.Figure()
    if perm.size:
        fig.add_trace(go.Histogram(x=perm, nbinsx=max(8, min(24, perm.size)),
                                   marker_color=UNSELECTED_COLOR, name="순열 귀무분포"))
    fig.add_vline(x=chance, line=dict(color=INK_MUTED, width=1.5, dash="dot"),
                  annotation_text=f"우연 {chance:.3f}",
                  annotation_font=dict(color=INK_MUTED, size=11))
    fig.add_vline(x=observed, line=dict(color=SELECTED_COLOR, width=2),
                  annotation_text=f"관측 {observed:.3f}",
                  annotation_font=dict(color=SELECTED_COLOR, size=11))
    return _base_layout(fig, "순열 귀무분포 (피험자 단위 라벨 셔플)",
                        "balanced accuracy", "횟수", height, showlegend=False)


def plot_sample_size_curve(curve_df: pd.DataFrame, height: int = 460) -> go.Figure:
    """
    표본 크기에 따른 KS D 와 p.

    **이중 y축을 쓰지 않는다** — 척도가 전혀 다른 두 지표라 한 축에 겹치면
    비교가 왜곡된다. 행을 나눠 x축만 공유시키면 "D 는 평평한데 p 만 급락한다"가
    오히려 더 선명하게 읽힌다.
    """
    if curve_df.empty:
        return _empty("표본 크기 곡선", height=height)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.12,
                        subplot_titles=("효과크기 KS D — 표본 크기에 거의 무관",
                                        "p-값 — 표본 크기에 따라 급락"))

    x = curve_df["n"].to_numpy()
    fig.add_trace(go.Scatter(
        x=np.concatenate([x, x[::-1]]),
        y=np.concatenate([curve_df["ks_d_hi"].to_numpy(), curve_df["ks_d_lo"].to_numpy()[::-1]]),
        fill="toself", fillcolor=ENV_COLORS[1], opacity=0.15, line=dict(width=0),
        hoverinfo="skip", showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=curve_df["ks_d_mean"], mode="lines+markers",
                             name="KS D (평균, 90% 구간)",
                             line=dict(width=2, color=ENV_COLORS[1]),
                             marker=dict(size=8)), row=1, col=1)

    fig.add_trace(go.Scatter(x=x, y=curve_df["p_median"], mode="lines+markers",
                             name="p 중앙값", line=dict(width=2, color=FALL_COLORS["Fall"]),
                             marker=dict(size=8)), row=2, col=1)
    fig.add_hline(y=0.05, line=dict(color=THRESHOLD_COLOR, width=1.5, dash="dash"),
                  annotation_text="p = 0.05",
                  annotation_font=dict(color=INK_SECONDARY, size=11), row=2, col=1)
    fig.update_yaxes(type="log", row=2, col=1)
    fig.update_xaxes(title_text="그룹당 표본 크기 n", row=2, col=1)
    fig.update_yaxes(title_text="KS D", row=1, col=1)
    fig.update_yaxes(title_text="p (log)", row=2, col=1)

    fig.update_layout(meta={"title": "표본 크기에 따른 효과크기와 p-값"},
                      height=height, margin=dict(t=100, b=40),
                      legend=dict(orientation="h", yanchor="bottom", y=1.04),
                      font=dict(color=INK_SECONDARY))
    fig.update_xaxes(gridcolor=GRID_COLOR)
    fig.update_yaxes(gridcolor=GRID_COLOR)
    return fig


def plot_agc_sample_vs_full(sample_means: Dict[int, float],
                            full_means: Dict[int, float],
                            height: int = 340) -> go.Figure:
    """표본 평균 vs 전체 데이터셋 평균 — 발견인 동시에 샘플러 무편향 검증."""
    envs = sorted(set(sample_means) | set(full_means))
    if not envs:
        return _empty("AGC 표본 vs 전체", height=height)

    fig = go.Figure()
    fig.add_trace(go.Bar(x=[ENV_LABELS.get(e, str(e)) for e in envs],
                         y=[sample_means.get(e, np.nan) for e in envs],
                         name="표본", marker_color=ENV_COLORS[1]))
    fig.add_trace(go.Bar(x=[ENV_LABELS.get(e, str(e)) for e in envs],
                         y=[full_means.get(e, np.nan) for e in envs],
                         name="전체 9000 파일", marker_color=UNSELECTED_COLOR))
    fig.update_layout(barmode="group")
    return _base_layout(fig, "AGC 평균 — 표본 vs 전체 데이터셋", "환경", "AGC 평균", height)


# --- 교차 도메인 분류 (P7) ---------------------------------------------------

def plot_cwt_scalogram(s3, fs_hz=320.0, freq_min_hz=1.0, freq_max_hz=170.0,
                       title="CWT S3 스케일로그램", segment_sec=3.0,
                       height: int = 380) -> go.Figure:
    """
    S3 는 [0,1] 크기 맵이라 단일 색상 순차 램프. **행 0 이 최고주파수**(features 규약)라
    y 축 라벨을 그에 맞춘다.
    """
    a = np.asarray(s3, dtype=float)
    if a.size == 0:
        return _empty(title, height=height)

    f_max = min(freq_max_hz, fs_hz / 2.0)
    freqs = np.linspace(freq_min_hz, f_max, a.shape[0])[::-1]
    times = np.linspace(0.0, segment_sec, a.shape[1])

    fig = go.Figure(go.Heatmap(
        z=a, x=times, y=freqs, colorscale=SEQUENTIAL_SCALE,
        colorbar=dict(thickness=12, title="S3"),
        hovertemplate="t=%{x:.2f}s  f=%{y:.0f}Hz<br>%{z:.3f}<extra></extra>",
    ))
    return _base_layout(fig, title, "시간 (s)", "주파수 (Hz)", height, showlegend=False)


def plot_acf_map(acf, lag_seconds=0.4, segment_sec=3.0,
                 title="PCA-ACF (lag × 시간)", height: int = 380) -> go.Figure:
    """
    ACF 는 lag-product 라 **부호가 있다**([-1,1]) — 중립 회색 중점의 발산 램프를 쓴다.
    순차 램프로 그리면 음의 상관과 0 이 구분되지 않는다.
    """
    a = np.asarray(acf, dtype=float)
    if a.size == 0:
        return _empty(title, height=height)

    lags = np.linspace(0.0, lag_seconds, a.shape[0])
    times = np.linspace(0.0, segment_sec, a.shape[1])
    fig = go.Figure(go.Heatmap(
        z=a, x=times, y=lags, colorscale=DIVERGING_SCALE, zmid=0.0, zmin=-1.0, zmax=1.0,
        colorbar=dict(thickness=12, title="x(t)·x(t−τ)"),
        hovertemplate="t=%{x:.2f}s  τ=%{y:.3f}s<br>%{z:.3f}<extra></extra>",
    ))
    return _base_layout(fig, title, "시간 (s)", "lag τ (s)", height, showlegend=False)


def plot_arm_dotplot(summary: pd.DataFrame, statistic: str = "auc",
                     height: int = 400) -> go.Figure:
    """환경별 arm 성능 + 피험자 클러스터 CI. 우연선(0.5)을 함께 그린다."""
    if summary.empty:
        return _empty("arm 별 성능", height=height)

    fig = go.Figure()
    for arm in [a for a in ARM_LABELS if a in set(summary["arm"])]:
        sub = summary[summary["arm"] == arm].sort_values("target_env")
        if sub.empty:
            continue
        lo = sub["ci_lo"] if "ci_lo" in sub else sub[statistic]
        hi = sub["ci_hi"] if "ci_hi" in sub else sub[statistic]
        fig.add_trace(go.Scatter(
            x=[f"E{int(e)}" for e in sub["target_env"]], y=sub[statistic],
            mode="markers", name=ARM_LABELS.get(arm, arm),
            marker=dict(size=12, color=ARM_COLORS.get(arm, INK_MUTED),
                        line=dict(width=1, color="white")),
            error_y=dict(type="data", symmetric=False,
                         array=(hi - sub[statistic]).clip(lower=0),
                         arrayminus=(sub[statistic] - lo).clip(lower=0),
                         color=ARM_COLORS.get(arm, INK_MUTED), thickness=1.5, width=6),
        ))
    fig.add_hline(y=0.5, line=dict(color=INK_MUTED, width=1.5, dash="dot"),
                  annotation_text="우연 0.5",
                  annotation_font=dict(color=INK_MUTED, size=11))
    return _base_layout(fig, f"환경별 arm 성능 ({statistic})", "테스트 환경",
                        statistic.upper(), height)


def plot_gap_bars(gaps: pd.DataFrame, height: int = 400) -> go.Figure:
    """
    브랜치별 paired gap (IN − CROSS) 과 **MDE₈₀**. MDE 를 넘지 못한 막대는 회색으로
    칠해 "검출력 부족"임을 색으로도 알린다 — 작은 격차를 강건성으로 오독하는 것이
    이 분석에서 가장 흔한 실패다.
    """
    if gaps.empty:
        return _empty("브랜치별 도메인 격차", height=height)

    df = gaps.dropna(subset=["gap_mean"]).sort_values("gap_mean")
    if df.empty:
        return _empty("브랜치별 도메인 격차", height=height)

    under = df["underpowered"] if "underpowered" in df else [False] * len(df)
    colors = [UNSELECTED_COLOR if bool(u) else SELECTED_COLOR for u in under]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["gap_mean"], y=df["branch"], orientation="h", marker_color=colors,
        name="gap (IN − CROSS)",
        error_x=dict(type="data", symmetric=False,
                     array=(df["gap_hi"] - df["gap_mean"]).clip(lower=0),
                     arrayminus=(df["gap_mean"] - df["gap_lo"]).clip(lower=0),
                     color=INK_SECONDARY, thickness=1.5, width=6),
    ))
    fig.add_trace(go.Scatter(
        x=df["mde80"], y=df["branch"], mode="markers", name="MDE₈₀ (검출 하한)",
        marker=dict(symbol="line-ns", size=16, line=dict(width=2, color=INK_PRIMARY)),
    ))
    fig.add_vline(x=0.0, line=dict(color=INK_MUTED, width=1.5, dash="dot"))
    return _base_layout(fig, "브랜치별 도메인 격차 (회색 = 검출력 부족)",
                        "gap = IN − CROSS", "", height)


def plot_metric_triple(summary: pd.DataFrame, height: int = 360) -> go.Figure:
    """
    arm 별 AUC / BA@0.5 / BA@oracle. AUC 는 유지되는데 BA@0.5 만 무너지면
    **캘리브레이션 이동**이고, AUC 도 무너지면 **진짜 피처 이동**이다.
    """
    if summary.empty:
        return _empty("지표 삼종", height=height)

    metrics = [("auc", "AUC"), ("ba_at_half", "BA@0.5"), ("ba_at_oracle", "BA@oracle")]
    fig = go.Figure()
    for arm in [a for a in ARM_LABELS if a in set(summary["arm"])]:
        sub = summary[summary["arm"] == arm]
        fig.add_trace(go.Bar(
            x=[label for _, label in metrics],
            y=[float(sub[key].mean()) for key, _ in metrics],
            name=ARM_LABELS.get(arm, arm),
            marker_color=ARM_COLORS.get(arm, INK_MUTED),
        ))
    fig.update_layout(barmode="group")
    fig.add_hline(y=0.5, line=dict(color=INK_MUTED, width=1.5, dash="dot"))
    return _base_layout(fig, "임계값 문제인가 피처 문제인가", "지표", "값", height)


def plot_placebo_null(in_vals, cross_vals, perm_vals=None, height: int = 360) -> go.Figure:
    """
    격차의 진짜 귀무가설은 "라벨이 무작위"가 아니라 **"학습한 사람들이 녹화된 방은
    상관없다"** 이고, 그 분포를 IN arm 이 공급한다. CROSS 가 IN 밴드 안에 들어오면
    측정 가능한 방 효과가 없는 것이다.
    """
    fig = go.Figure()
    for vals, name, color in ((in_vals, "IN (플라시보: 같은 방)", ARM_COLORS["IN"]),
                              (cross_vals, "CROSS (다른 방)", ARM_COLORS["CROSS"]),
                              (perm_vals, "라벨 순열 귀무", UNSELECTED_COLOR)):
        v = np.asarray(list(vals or []), dtype=float)
        v = v[np.isfinite(v)]
        if not v.size:
            continue
        fig.add_trace(go.Histogram(x=v, name=name, marker_color=color,
                                   opacity=0.6, nbinsx=max(8, min(24, v.size))))
    fig.update_layout(barmode="overlay")
    fig.add_vline(x=0.5, line=dict(color=INK_MUTED, width=1.5, dash="dot"),
                  annotation_text="우연", annotation_font=dict(color=INK_MUTED, size=11))
    return _base_layout(fig, "플라시보 · 순열 참조 분포", "AUC", "횟수", height)


def plot_branch_scatter(summary: pd.DataFrame, height: int = 440) -> go.Figure:
    """
    x = CROSS AUC(다른 방에서의 실제 성능), y = gap(방이 바뀔 때 잃는 양).
    **오른쪽 아래**가 좋은 피처군이다 — 정확하고 격차가 작다.
    """
    if summary.empty:
        return _empty("브랜치 비교", height=height)

    df = summary.dropna(subset=["cross_auc", "gap_mean"])
    if df.empty:
        return _empty("브랜치 비교", height=height)

    kinds = df["kind"] if "kind" in df else ["map"] * len(df)
    fig = go.Figure()
    fig.add_hline(y=0.0, line=dict(color=INK_MUTED, width=1, dash="dot"))
    fig.add_vline(x=0.5, line=dict(color=INK_MUTED, width=1, dash="dot"),
                  annotation_text="우연", annotation_font=dict(color=INK_MUTED, size=10))
    fig.add_trace(go.Scatter(
        x=df["cross_auc"], y=df["gap_mean"], mode="markers+text",
        text=df["branch"], textposition="top center",
        textfont=dict(size=10, color=INK_SECONDARY),
        marker=dict(size=np.clip(df["in_auc"].fillna(0.5) * 26, 8, 30),
                    color=[SELECTED_COLOR if k == "map" else ENV_COLORS[2] for k in kinds],
                    line=dict(width=1, color="white")),
        hovertemplate="%{text}<br>CROSS AUC %{x:.3f}<br>gap %{y:+.3f}<extra></extra>",
        name="브랜치",
    ))
    return _base_layout(fig, "피처군 비교 — 오른쪽 아래가 좋다",
                        "CROSS AUC (다른 방 성능)", "gap = IN − CROSS",
                        height, showlegend=False)


def plot_per_subject_strip(per_subject: pd.DataFrame, height: int = 340) -> go.Figure:
    """피험자별 점수 — 한 사람이 결과를 끌고 가는지 집계값보다 훨씬 잘 보인다."""
    if per_subject.empty or "arm" not in per_subject.columns:
        return _empty("피험자별 점수", height=height)

    fig = go.Figure()
    for arm in [a for a in ARM_LABELS if a in set(per_subject["arm"])]:
        sub = per_subject[per_subject["arm"] == arm]
        if sub.empty:
            continue
        fig.add_trace(go.Box(
            x=[ARM_LABELS.get(arm, arm)] * len(sub), y=sub["value"],
            name=ARM_LABELS.get(arm, arm), boxpoints="all", jitter=0.5, pointpos=0,
            marker=dict(size=8, color=ARM_COLORS.get(arm, INK_MUTED)),
            line=dict(width=1.5, color=ARM_COLORS.get(arm, INK_MUTED)),
        ))
    fig.add_hline(y=0.5, line=dict(color=INK_MUTED, width=1.5, dash="dot"))
    return _base_layout(fig, "피험자별 점수 분포", "arm", "AUC", height, showlegend=False)
