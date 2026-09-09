"""Plotly chart builders for Page 3's subcarrier-selection statistics."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

ANTENNA_LABELS = {0: "Ant1", 1: "Ant2", 2: "Ant3"}


def _with_antenna_label(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(antenna_label=df["antenna"].map(ANTENNA_LABELS))


def plot_antenna_group_bar(df_free: pd.DataFrame, group_col: str) -> go.Figure:
    """Grouped bar chart: proportion of selected streams per antenna, by group_col."""
    df = _with_antenna_label(df_free)
    counts = df.groupby([group_col, "antenna_label"]).size().reset_index(name="count")
    totals = counts.groupby(group_col)["count"].transform("sum")
    counts["proportion"] = counts["count"] / totals

    fig = px.bar(
        counts, x=group_col, y="proportion", color="antenna_label", barmode="group",
        category_orders={"antenna_label": ["Ant1", "Ant2", "Ant3"]},
        labels={"proportion": "Proportion of selected streams", "antenna_label": "Antenna"},
        title=f"Selected-Antenna Proportion by {group_col}",
    )
    fig.update_layout(height=350, margin=dict(t=60, b=40))
    return fig


def plot_group_subcarrier_heatmap(df_free: pd.DataFrame, group_col: str) -> go.Figure:
    """Heatmap of selection frequency across (group_col, subcarrier), e.g. group_col="antenna"/"env"/"class_id"."""
    if group_col == "antenna":
        df = _with_antenna_label(df_free)
        row_col, row_order = "antenna_label", ["Ant1", "Ant2", "Ant3"]
    else:
        df = df_free
        row_col, row_order = group_col, sorted(df_free[group_col].unique().tolist())

    counts = df.groupby([row_col, "subcarrier"]).size().reset_index(name="count")
    pivot = counts.pivot(index=row_col, columns="subcarrier", values="count")
    pivot = pivot.reindex(index=row_order, columns=range(30), fill_value=0).fillna(0)

    fig = px.imshow(
        pivot, labels=dict(x="Subcarrier", y=group_col, color="Selection count"),
        aspect="auto", color_continuous_scale="Viridis",
        title=f"Subcarrier Selection Frequency by {group_col}",
    )
    fig.update_layout(height=300, margin=dict(t=60, b=40))
    return fig


def plot_cramers_v_heatmap(matrix: pd.DataFrame) -> go.Figure:
    """Annotated heatmap of pairwise Cramér's V among a set of categorical factors."""
    fig = px.imshow(
        matrix, text_auto=".2f", zmin=0, zmax=1, color_continuous_scale="Viridis",
        labels=dict(color="Cramér's V"),
        title="Correlation Between Factors (Cramér's V)",
    )
    fig.update_layout(height=400, margin=dict(t=60, b=40))
    return fig


def plot_q_value_box(df_free: pd.DataFrame, group_col: str) -> go.Figure:
    """Boxplot of q(h) grouped by group_col (e.g. "antenna_label", "env", "class_id")."""
    df = _with_antenna_label(df_free) if group_col == "antenna" else df_free
    x_col = "antenna_label" if group_col == "antenna" else group_col

    fig = px.box(
        df, x=x_col, y="q_value", color=x_col,
        title=f"q(h) Distribution by {group_col}",
    )
    fig.update_layout(height=350, margin=dict(t=60, b=40), showlegend=False)
    return fig


def plot_forced_antenna_box(df_forced: pd.DataFrame) -> go.Figure:
    """Paired boxplot of q(h) across the 3 forced-single-antenna conditions."""
    modes = ["ant0_only", "ant1_only", "ant2_only"]
    mode_labels = {"ant0_only": "Ant1 only", "ant1_only": "Ant2 only", "ant2_only": "Ant3 only"}
    subset = df_forced[df_forced["selection_mode"].isin(modes)].assign(
        mode_label=lambda d: d["selection_mode"].map(mode_labels)
    )

    fig = px.box(
        subset, x="mode_label", y="q_value", color="mode_label",
        category_orders={"mode_label": list(mode_labels.values())},
        title="q(h) Distribution — Forced Single-Antenna Selection",
    )
    fig.update_layout(height=350, margin=dict(t=60, b=40), showlegend=False)
    return fig
