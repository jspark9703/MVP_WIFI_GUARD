#!/usr/bin/env python3
"""
Advanced Analysis: Raw CSI Visualization & Fall Segment Detection

NOTE: This file is now split into:
  - 04a_raw_csi_visualization.py (Task 1)
  - 04b_moving_variance_analysis.py (Task 2)

Tasks (original, now separated):
1. Visualize raw CSI (antenna 1,2,3, subcarrier 15) for all activities/environments
   - Sample one trial per activity (A01-A12) per environment (E1-E3) = 36 samples

2. Analyze moving variance (q-value) to identify fall segments
   - For each environment: select representative fall sample
   - Plot moving variance (2W+1) vs segment, identify threshold
   - Only plot selected streams (antenna × top streams)

3. [Next] Create true_fall_labelling.py to extract fall segments using q-value threshold
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import yaml
from scipy import signal
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dwt_coef.data_loader import get_file_index, load_csi_file, parse_filename
from src.dwt_coef.preprocessing import _moving_variance, _compute_q


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

FALL_ACTIVITIES = [2, 5]

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
        description="Advanced CSI analysis: visualization and fall detection",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/preprocess.yaml"),
        help="Path to preprocessing config for metadata",
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
        "--omega",
        type=int,
        default=64,
        help="Moving variance window half-width",
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


def plot_raw_csi_samples(
    file_map: Dict[Tuple[int, int], Path],
    output_dir: Path,
    subcarrier: int = 15,
):
    """
    Plot raw CSI for antenna 1,2,3 across all activities/environments.

    Creates:
    - 12 subplots (activities) × 3 columns (environments)
    - Each subplot: time (x-axis) vs amplitude (y-axis) for one antenna
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(18, 20))
    gs = GridSpec(12, 3, figure=fig, hspace=0.3, wspace=0.3)

    for act_idx, act in enumerate(ACTIVITY_IDS):
        for env_idx, env in enumerate(ENV_IDS):
            if (act, env) not in file_map:
                print(f"  [SKIP] Activity {act}, Environment {env}: no file")
                continue

            filepath = file_map[(act, env)]
            print(f"  Loading {filepath.name}")

            try:
                csi_data = load_csi_file(filepath)
            except Exception as e:
                print(f"    Error loading: {e}")
                continue

            # Extract CSI: shape (n_packets, 3_antennas, 30_subcarriers)
            csi = csi_data["csi"]
            timestamps = csi_data["timestamps"]

            # Get one antenna, one subcarrier
            antenna = 2
            signal_amp = np.abs(csi[:, antenna, subcarrier - 1])

            # Time axis (relative to first packet, in seconds)
            time_sec = (timestamps - timestamps[0]) / 1e6  # microseconds → seconds

            ax = fig.add_subplot(gs[act_idx, env_idx])
            ax.plot(time_sec, signal_amp, linewidth=0.8, alpha=0.8)
            ax.set_xlabel("Time (s)", fontsize=8)
            ax.set_ylabel("Amplitude", fontsize=8)
            ax.set_title(
                f"{ACTIVITY_MAP.get(act, f'A{act:02d}')} - Env{env}",
                fontsize=9, fontweight='bold'
            )
            ax.grid(True, alpha=0.3)
            ax.tick_params(labelsize=7)

    output_file = output_dir / f"01_raw_csi_subcarrier{subcarrier}.png"
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    print(f"\n[OK] Saved: {output_file}")
    plt.close()


def plot_moving_variance_analysis(
    file_map: Dict[Tuple[int, int], Path],
    output_dir: Path,
    omega: int = 160,
    n_selected_streams: int = 3,
):
    """
    Analyze moving variance for fall detection.

    For each environment:
    - Select one representative fall sample (activity 2 or 5)
    - Compute moving variance across time
    - Plot variance with segment indicators
    - Select top n_selected_streams by q-value
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find fall activities per environment
    fall_samples = {}

    for env in ENV_IDS:
        for act in FALL_ACTIVITIES:
            if (act, env) in file_map:
                fall_samples[env] = (act, file_map[(act, env)])
                break

    fig, axes = plt.subplots(3, 1, figsize=(16, 12))
    fig.suptitle("Moving Variance Analysis for Fall Detection (ω=64)", fontsize=14, fontweight='bold')

    for env_idx, env in enumerate(ENV_IDS):
        if env not in fall_samples:
            print(f"  [SKIP] Environment {env}: no fall sample")
            continue

        act, filepath = fall_samples[env]
        print(f"  Processing E{env} (Activity {ACTIVITY_MAP.get(act, f'A{act:02d}')}): {filepath.name}")

        try:
            csi_data = load_csi_file(filepath)
        except Exception as e:
            print(f"    Error: {e}")
            continue

        csi = csi_data["csi"]
        timestamps = csi_data["timestamps"]
        n_packets = csi.shape[0]

        # Compute q-value for each stream (antenna × subcarrier)
        q_values = {}

        for ant in range(3):
            for sub in range(30):
                sig = np.abs(csi[:, ant, sub])
                q = _compute_q(sig, omega)
                q_values[(ant, sub)] = q

        # Select top streams by q-value
        sorted_streams = sorted(q_values.items(), key=lambda x: x[1], reverse=True)
        top_streams = [s[0] for s in sorted_streams[:n_selected_streams]]

        ax = axes[env_idx]
        time_sec = (timestamps - timestamps[0]) / 1e6

        # Plot moving variance for top streams
        colors = ['red', 'green', 'blue']
        for stream_idx, (ant, sub) in enumerate(top_streams):
            sig = np.abs(csi[:, ant, sub])
            mv = _moving_variance(sig, omega)
            ax.plot(
                time_sec, mv,
                label=f"Ant{ant+1} Sub{sub+1} (q={q_values[(ant, sub)]:.2f})",
                color=colors[stream_idx % 3],
                linewidth=1.5,
                alpha=0.7
            )

        # Segment boundaries (2.5s window, 0.25s stride)
        window_size = 2.5
        stride = 0.25
        segment_idx = 0
        t = 0
        while t < time_sec[-1]:
            ax.axvline(t, color='gray', linestyle='--', alpha=0.4, linewidth=0.8)
            segment_idx += 1
            t += stride

        ax.set_xlabel("Time (s)", fontsize=10)
        ax.set_ylabel("Moving Variance", fontsize=10)
        ax.set_title(f"Environment {env} - {ACTIVITY_MAP.get(act, f'A{act:02d}')}", fontsize=11, fontweight='bold')
        ax.legend(fontsize=9, loc='upper right')
        ax.grid(True, alpha=0.3)

    output_file = output_dir / "02_moving_variance_analysis.png"
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    print(f"\n[OK] Saved: {output_file}")
    plt.close()


def estimate_fall_segments(
    csi_dict: Dict,
    omega: int = 64,
    q_threshold_percentile: float = 75,
) -> Dict[str, Any]:
    """
    Estimate fall segments using q-value based on moving variance.

    Returns:
        Dict with: q_values, threshold, fall_segments (list of [start_sec, end_sec])
    """
    csi = csi_dict["csi"]
    timestamps = csi_dict["timestamps"]

    # Compute q-value for each stream
    q_values = {}
    max_q = 0

    for ant in range(3):
        for sub in range(30):
            sig = np.abs(csi[:, ant, sub])
            q = _compute_q(sig, omega)
            q_values[(ant, sub)] = q
            max_q = max(max_q, q)

    # Threshold: use percentile of max q-values across streams
    q_list = list(q_values.values())
    threshold = np.percentile(q_list, q_threshold_percentile)

    # Find high-variance regions
    selected_streams = [(k, v) for k, v in q_values.items() if v > threshold]

    if len(selected_streams) == 0:
        return {
            "q_values": q_values,
            "threshold": threshold,
            "fall_segments": [],
            "n_selected_streams": 0,
        }

    # Compute moving variance for selected streams, average
    time_sec = (timestamps - timestamps[0]) / 1e6
    avg_mv = np.zeros(len(timestamps))

    for (ant, sub), _ in selected_streams:
        sig = np.abs(csi[:, ant, sub])
        mv = _moving_variance(sig, omega)
        avg_mv += mv

    avg_mv /= len(selected_streams)

    # Find peaks in moving variance
    peaks, _ = signal.find_peaks(avg_mv, height=np.mean(avg_mv) + np.std(avg_mv), distance=10)

    fall_segments = []
    for peak in peaks:
        # Expand window around peak
        start = max(0, peak - 20)
        end = min(len(timestamps) - 1, peak + 80)
        fall_segments.append([time_sec[start], time_sec[end]])

    return {
        "q_values": q_values,
        "threshold": threshold,
        "fall_segments": fall_segments,
        "n_selected_streams": len(selected_streams),
        "avg_moving_variance": avg_mv,
        "time_sec": time_sec,
    }


def main():
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Advanced CSI Analysis Pipeline")
    print("=" * 70)

    # Load file index
    print("\n[1/3] Loading file index...")
    file_index = get_file_index(args.mendeley_root)
    print(f"  Found {len(file_index)} files")

    # Get sample files
    print("\n[2/3] Selecting samples (1 per activity-environment)...")
    file_map = get_sample_per_activity_env(file_index)
    print(f"  Selected {len(file_map)} samples")

    # Task 1: Visualize raw CSI
    print("\n[Task 1/3] Visualizing raw CSI (antenna 1,2,3, subcarrier 15)...")
    plot_raw_csi_samples(file_map, args.output, subcarrier=15)

    # Task 2: Analyze moving variance
    print("\n[Task 2/3] Analyzing moving variance for fall detection...")
    plot_moving_variance_analysis(file_map, args.output, omega=args.omega)

    print("\n" + "=" * 70)
    print("Analysis complete!")
    print(f"Output directory: {args.output}")
    print("=" * 70)
    print("\n→ Next step: Run scripts/true_fall_labelling.py")


if __name__ == "__main__":
    main()
