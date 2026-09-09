#!/usr/bin/env python3
"""
AGC (Automatic Gain Control) Distribution Analysis

Visualizes the distribution of raw `agc` values recorded in the Mendeley CSI
dataset, broken down independently by:
- Environment (E1-E3)
- Experiment condition (C1-C5, the `class_id` field)
- Activity (A01-A12)

AGC is read directly from the raw CSV `agc` column (no CSI amplitude/phase
parsing needed), so this is much cheaper than a full `load_csi_file()` pass.
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dwt_coef.data_loader import get_file_index


ACTIVITY_MAP = {
    1: "Sit still", 2: "Fall (sit)", 3: "Lie down", 4: "Stand still",
    5: "Fall (stand)", 6: "Walk TX->RX", 7: "Turn", 8: "Walk RX->TX",
    9: "Turn", 10: "Stand up", 11: "Sit down", 12: "Pick up pen",
}
ACTIVITY_IDS = list(range(1, 13))

CONDITION_MAP = {
    1: "C1 (Fall-sit)",
    2: "C2 (Fall-stand)",
    3: "C3 (Walking)",
    4: "C4 (Sit/Stand)",
    5: "C5 (Pick pen)",
}
CONDITION_IDS = list(range(1, 6))

ENV_IDS = [1, 2, 3]
ENV_LABELS = {1: "E1", 2: "E2", 3: "E3"}
# Categorical slots 1-3 (blue, aqua, yellow) from the project's validated palette.
# Environment keeps the same color across every plot in this script.
ENV_COLORS = {1: "#2a78d6", 2: "#1baf7a", 3: "#eda100"}

INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID_COLOR = "#e1e0d9"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visualize AGC value distribution by environment, condition (C), and activity (A)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mendeley-root",
        type=Path,
        default=Path("Mendeley"),
        help="Root directory of Mendeley dataset",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/analysis/agc_distribution"),
        help="Output directory for plots and summary tables",
    )
    parser.add_argument(
        "--env",
        type=int,
        nargs="+",
        default=None,
        help="Restrict to specific environments (e.g. --env 1 2)",
    )
    parser.add_argument(
        "--activities",
        type=int,
        nargs="+",
        default=None,
        help="Restrict to specific activity IDs (e.g. --activities 2 5)",
    )
    parser.add_argument(
        "--subjects",
        type=int,
        nargs="+",
        default=None,
        help="Restrict to specific subject IDs",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Limit number of files processed (for a quick test run)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Output figure DPI",
    )
    return parser.parse_args()


def collect_agc_data(file_index: pd.DataFrame) -> pd.DataFrame:
    """
    Read the `agc` column from every file in file_index and pool it into one
    long-format DataFrame (one row per CSI packet).

    Returns:
        DataFrame with columns: env, class_id, activity, subject, trial, agc
    """
    chunks = []
    n_errors = 0

    for _, row in tqdm(file_index.iterrows(), total=len(file_index), desc="Reading AGC"):
        filepath = Path(row["filepath"])
        try:
            agc = pd.read_csv(filepath, usecols=["agc"])["agc"].values.astype(np.int16)
        except Exception as e:
            n_errors += 1
            tqdm.write(f"  Error reading {filepath.name}: {e}")
            continue

        n = len(agc)
        chunks.append(pd.DataFrame({
            "env": np.full(n, row["env"], dtype=np.int8),
            "class_id": np.full(n, row["class_id"], dtype=np.int8),
            "activity": np.full(n, row["activity"], dtype=np.int8),
            "subject": np.full(n, row["subject"], dtype=np.int16),
            "trial": np.full(n, row["trial"], dtype=np.int8),
            "agc": agc,
        }))

    if n_errors:
        print(f"  ({n_errors} files failed to read and were skipped)")

    if not chunks:
        raise RuntimeError("No AGC data could be read from any file")

    return pd.concat(chunks, ignore_index=True)


def build_file_manifest(file_index: pd.DataFrame, agc_df: pd.DataFrame) -> pd.DataFrame:
    """Per-file AGC summary (mean/std/min/max/n_packets), keyed like other pipeline manifests."""
    per_file = (
        agc_df.groupby(["env", "class_id", "activity", "subject", "trial"])["agc"]
        .agg(agc_mean="mean", agc_std="std", agc_min="min", agc_max="max", n_packets="count")
        .reset_index()
    )
    return per_file.sort_values(["env", "subject", "class_id", "activity", "trial"]).reset_index(drop=True)


def compute_group_stats(agc_df: pd.DataFrame) -> pd.DataFrame:
    """Combined summary table (n, mean, std, quartiles) for each grouping dimension."""
    rows = []
    for group_col, id_list, label_map in [
        ("env", ENV_IDS, ENV_LABELS),
        ("class_id", CONDITION_IDS, CONDITION_MAP),
        ("activity", ACTIVITY_IDS, ACTIVITY_MAP),
    ]:
        for group_val in id_list:
            values = agc_df.loc[agc_df[group_col] == group_val, "agc"]
            if len(values) == 0:
                continue
            rows.append({
                "group_type": group_col,
                "group_value": group_val,
                "group_label": label_map.get(group_val, str(group_val)),
                "n": len(values),
                "mean": values.mean(),
                "std": values.std(),
                "min": values.min(),
                "q25": values.quantile(0.25),
                "median": values.median(),
                "q75": values.quantile(0.75),
                "max": values.max(),
            })
    return pd.DataFrame(rows)


def _style_axis(ax):
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color=GRID_COLOR, linewidth=1.0)
    ax.xaxis.grid(False)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(INK_MUTED)
    ax.tick_params(colors=INK_SECONDARY, labelsize=9)


def _draw_box(ax, data: np.ndarray, position: float, width: float, color: str):
    bp = ax.boxplot(
        [data],
        positions=[position],
        widths=width,
        patch_artist=True,
        showfliers=False,
        medianprops=dict(color=INK_PRIMARY, linewidth=1.5),
        boxprops=dict(facecolor=color, edgecolor=INK_PRIMARY, alpha=0.85, linewidth=1.0),
        whiskerprops=dict(color=INK_SECONDARY, linewidth=1.0),
        capprops=dict(color=INK_SECONDARY, linewidth=1.0),
    )
    return bp


def plot_by_environment(agc_df: pd.DataFrame, output_path: Path, dpi: int = 150):
    """Single-factor boxplot: one box per environment, direct-labeled (only 3 boxes)."""
    fig, ax = plt.subplots(figsize=(6, 5))

    # Direct-label median + n above each box (few enough boxes for this to be legible).
    for i, env in enumerate(ENV_IDS):
        data = agc_df.loc[agc_df["env"] == env, "agc"].values
        if len(data) == 0:
            continue
        _draw_box(ax, data, position=i, width=0.6, color=ENV_COLORS[env])
        median = np.median(data)
        ax.annotate(
            f"median={median:.0f}\nn={len(data):,}",
            xy=(i, np.max(data)),
            xytext=(0, 6), textcoords="offset points",
            ha="center", va="bottom", fontsize=8, color=INK_SECONDARY,
        )

    ax.set_xticks(range(len(ENV_IDS)))
    ax.set_xticklabels([ENV_LABELS[e] for e in ENV_IDS], fontsize=10, color=INK_PRIMARY)
    ax.set_ylabel("AGC value", fontsize=10, color=INK_PRIMARY)
    ax.set_title("AGC Distribution by Environment", fontsize=13, fontweight="bold", color=INK_PRIMARY)
    _style_axis(ax)
    ax.set_xlim(-0.6, len(ENV_IDS) - 1 + 0.6)
    ax.margins(y=0.15)

    plt.tight_layout()
    plt.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close()


def _grouped_boxplot(
    ax,
    agc_df: pd.DataFrame,
    x_col: str,
    x_ids: List[int],
    x_labels: Dict[int, str],
):
    """Grouped boxplot: one x-group per x_ids value, one sub-box per environment (hue)."""
    n_hue = len(ENV_IDS)
    group_width = n_hue + 1  # 1-slot gap between groups
    box_width = 0.8

    for g, x_val in enumerate(x_ids):
        for e, env in enumerate(ENV_IDS):
            data = agc_df.loc[(agc_df[x_col] == x_val) & (agc_df["env"] == env), "agc"].values
            if len(data) == 0:
                continue
            position = g * group_width + e
            _draw_box(ax, data, position=position, width=box_width, color=ENV_COLORS[env])

    tick_positions = [g * group_width + (n_hue - 1) / 2 for g in range(len(x_ids))]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels([x_labels.get(v, str(v)) for v in x_ids])
    ax.set_xlim(-1, (len(x_ids) - 1) * group_width + n_hue)

    legend_handles = [Patch(facecolor=ENV_COLORS[e], edgecolor=INK_PRIMARY, alpha=0.85, label=ENV_LABELS[e]) for e in ENV_IDS]
    ax.legend(handles=legend_handles, title="Environment", loc="upper right", fontsize=9, title_fontsize=9, frameon=False)


def plot_by_condition(agc_df: pd.DataFrame, output_path: Path, dpi: int = 150):
    fig, ax = plt.subplots(figsize=(11, 6))

    _grouped_boxplot(ax, agc_df, "class_id", CONDITION_IDS,
                      {c: CONDITION_MAP[c].replace(" (", "\n(") for c in CONDITION_IDS})

    ax.set_ylabel("AGC value", fontsize=10, color=INK_PRIMARY)
    ax.set_title("AGC Distribution by Experiment Condition (C)", fontsize=13, fontweight="bold", color=INK_PRIMARY)
    ax.tick_params(axis="x", labelsize=9, colors=INK_PRIMARY)
    _style_axis(ax)

    plt.tight_layout()
    plt.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close()


def plot_by_activity(agc_df: pd.DataFrame, output_path: Path, dpi: int = 150):
    fig, ax = plt.subplots(figsize=(18, 6))

    labels = {a: f"A{a:02d}\n{ACTIVITY_MAP[a]}" for a in ACTIVITY_IDS}
    _grouped_boxplot(ax, agc_df, "activity", ACTIVITY_IDS, labels)

    ax.set_ylabel("AGC value", fontsize=10, color=INK_PRIMARY)
    ax.set_title("AGC Distribution by Activity (A)", fontsize=13, fontweight="bold", color=INK_PRIMARY)
    ax.tick_params(axis="x", labelsize=8, colors=INK_PRIMARY)
    _style_axis(ax)

    plt.tight_layout()
    plt.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close()


def main():
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("AGC Distribution Analysis")
    print("=" * 70)

    print("\n[1/4] Loading file index...")
    file_index = get_file_index(args.mendeley_root)
    if args.env is not None:
        file_index = file_index[file_index["env"].isin(args.env)]
    if args.activities is not None:
        file_index = file_index[file_index["activity"].isin(args.activities)]
    if args.subjects is not None:
        file_index = file_index[file_index["subject"].isin(args.subjects)]
    if args.max_files is not None:
        file_index = file_index.head(args.max_files)
    print(f"  {len(file_index)} files selected")

    print("\n[2/4] Reading AGC values from raw CSVs...")
    agc_df = collect_agc_data(file_index)
    print(f"  Pooled {len(agc_df):,} AGC samples from {file_index['filepath'].nunique()} files")

    print("\n[3/4] Saving summary tables...")
    manifest = build_file_manifest(file_index, agc_df)
    manifest_path = args.output / "agc_manifest.csv"
    manifest.to_csv(manifest_path, index=False)
    print(f"  [OK] Saved per-file manifest: {manifest_path}")

    stats = compute_group_stats(agc_df)
    stats_path = args.output / "agc_summary_stats.csv"
    stats.to_csv(stats_path, index=False)
    print(f"  [OK] Saved group summary stats: {stats_path}")

    print("\n[4/4] Generating plots...")
    plot_by_environment(agc_df, args.output / "agc_by_environment.png", dpi=args.dpi)
    print(f"  [OK] Saved: {args.output / 'agc_by_environment.png'}")
    plot_by_condition(agc_df, args.output / "agc_by_condition.png", dpi=args.dpi)
    print(f"  [OK] Saved: {args.output / 'agc_by_condition.png'}")
    plot_by_activity(agc_df, args.output / "agc_by_activity.png", dpi=args.dpi)
    print(f"  [OK] Saved: {args.output / 'agc_by_activity.png'}")

    print("\n" + "=" * 70)
    print("AGC Distribution Analysis Complete!")
    print(f"Output directory: {args.output}")
    print("=" * 70)


if __name__ == "__main__":
    main()
