#!/usr/bin/env python3
"""
Raw CSI Visualization: All Activities & Environments

Task:
1. Visualize raw CSI (antenna 1,2,3, subcarrier 15) for all activities/environments
   - Sample one trial per activity (A01-A12) per environment (E1-E3) = 36 samples
   - Plot grid: 12 rows (activities) × 3 columns (environments)
   - Include condition information in titles (C1-C5)
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dwt_coef.data_loader import get_file_index, load_csi_file


ACTIVITY_MAP = {
    1:  "Sit still",         # C1, C4
    2:  "Fall (from sit)",   # C1
    3:  "Lie down",          # C1, C2
    4:  "Stand still",       # C2, C4
    5:  "Fall (from stand)", # C2
    6:  "Walk TX→RX",        # C3
    7:  "Turn",              # C3
    8:  "Walk RX→TX",        # C3
    9:  "Turn",              # C3
    10: "Stand up",          # C4
    11: "Sit down",          # C4
    12: "Pick up pen",       # C5
}

CONDITION_MAP = {
    "C1 (Fall from sit)":   [1, 2, 3],
    "C2 (Fall from stand)": [4, 5, 3],
    "C3 (Walking)":         [6, 7, 8, 9],
    "C4 (Sit/Stand)":       [1, 10, 4, 11],
    "C5 (Pick pen)":        [12],
}

ACTIVITY_IDS = list(range(1, 13))
ENV_IDS = [1, 2, 3]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visualize raw CSI for all activities and environments",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mendeley-root",
        type=Path,
        default=Path("Mendeley"),
        help="Root directory of Mendeley dataset (for raw CSI)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/analysis"),
        help="Output directory for visualizations",
    )
    parser.add_argument(
        "--subcarrier",
        type=int,
        default=15,
        help="Subcarrier index to plot (1-30)",
    )
    return parser.parse_args()


def load_config(path: Path) -> Dict[str, Any]:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def get_sample_per_activity_env(
    file_index: pd.DataFrame,
    activities: List[int] = None,
    environments: List[int] = None,
    trial: int = 1,
) -> Dict[Tuple[int, int], Path]:
    """
    Select one file per (activity, environment) combination.

    Returns:
        Dict mapping (activity, env) → filepath
    """
    if activities is None:
        activities = ACTIVITY_IDS
    if environments is None:
        environments = ENV_IDS

    result = {}
    for act in activities:
        for env in environments:
            subset = file_index[
                (file_index["activity"] == act) &
                (file_index["env"] == env) &
                (file_index["trial"] == trial)
            ]
            if len(subset) > 0:
                result[(act, env)] = subset.iloc[0]["filepath"]

    return result


def get_condition_for_activity(activity: int) -> str:
    """Get condition label (C1-C5) for given activity."""
    for condition, activities in CONDITION_MAP.items():
        if activity in activities:
            return condition.split()[0]  # Return just "C1", "C2", etc.
    return "Unknown"


def plot_raw_csi_samples(
    file_map: Dict[Tuple[int, int], Path],
    output_dir: Path,
    subcarrier: int = 15,
):
    """
    Plot raw CSI for all antennas across all activities/environments.

    Creates per-antenna visualizations:
    - 12 subplots (activities) × 3 columns (environments) per antenna
    - Each subplot: time (x-axis) vs amplitude (y-axis)
    - Titles include activity name and condition
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate visualization for each antenna
    for antenna_idx in range(3):
        antenna_num = antenna_idx + 1
        print(f"\n  [Antenna {antenna_num}] Generating visualization...")

        fig = plt.figure(figsize=(18, 20))
        gs = GridSpec(12, 3, figure=fig, hspace=0.35, wspace=0.3)

        for act_idx, act in enumerate(ACTIVITY_IDS):
            condition = get_condition_for_activity(act)
            activity_label = ACTIVITY_MAP.get(act, f"A{act:02d}")

            for env_idx, env in enumerate(ENV_IDS):
                if (act, env) not in file_map:
                    continue

                filepath = file_map[(act, env)]

                try:
                    csi_data = load_csi_file(filepath)
                except Exception:
                    continue

                # Extract CSI: shape (n_packets, 3_antennas, 30_subcarriers)
                csi = csi_data["csi"]
                timestamps = csi_data["timestamps"]

                # Get specified antenna and subcarrier
                signal_amp = np.abs(csi[:, antenna_idx, subcarrier - 1])

                # Time axis (relative to first packet, in seconds)
                time_sec = (timestamps - timestamps[0]) / 1e6  # microseconds → seconds

                ax = fig.add_subplot(gs[act_idx, env_idx])
                ax.plot(time_sec, signal_amp, linewidth=0.8, alpha=0.8)
                ax.set_xlabel("Time (s)", fontsize=8)
                ax.set_ylabel("Amplitude", fontsize=8)
                ax.set_title(
                    f"{condition}: {activity_label} - E{env}",
                    fontsize=9, fontweight='bold'
                )
                ax.grid(True, alpha=0.3)
                ax.tick_params(labelsize=7)

        fig.suptitle(f"Antenna {antenna_num} - Raw CSI (Subcarrier {subcarrier})",
                     fontsize=16, fontweight='bold', y=0.995)
        output_file = output_dir / f"04a_raw_csi_antenna{antenna_num}_subcarrier{subcarrier}.png"
        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        print(f"    [OK] Saved: {output_file}")
        plt.close()


def main():
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Raw CSI Visualization")
    print("=" * 70)

    # Load file index
    print("\n[1/2] Loading file index...")
    file_index = get_file_index(args.mendeley_root)
    print(f"  Found {len(file_index)} files")

    # Get sample files
    print("\n[2/2] Selecting samples (1 per activity-environment)...")
    file_map = get_sample_per_activity_env(file_index)
    print(f"  Selected {len(file_map)} samples")

    # Visualize raw CSI
    print("\n[Task] Visualizing raw CSI (antenna 2, subcarrier {})...".format(args.subcarrier))
    plot_raw_csi_samples(file_map, args.output, subcarrier=args.subcarrier)

    print("\n" + "=" * 70)
    print("Visualization complete!")
    print(f"Output directory: {args.output}")
    print("=" * 70)


if __name__ == "__main__":
    main()
