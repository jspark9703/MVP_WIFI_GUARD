#!/usr/bin/env python3
"""
Moving Variance Verification with Fall Segments

Visualize moving variance (2W+1 = 129 samples, ω=64) for selected streams
with overlaid fall segment regions to verify segment accuracy.

Generates per-file plots with:
- Moving variance time series for top 3 selected streams
- Fall segment regions highlighted
- Segment boundaries marked with timestamps
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dwt_coef.data_loader import load_csi_file
from src.dwt_coef.preprocessing import _moving_variance, _compute_q


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify fall segments against moving variance analysis",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mendeley-root",
        type=Path,
        default=Path("Mendeley"),
        help="Root directory of Mendeley dataset",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path("results/analysis/03_fall_labelling_metadata_v2.json"),
        help="Path to fall labelling metadata JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/analysis/fall_moving_variance_verification"),
        help="Output directory for verification plots",
    )
    parser.add_argument(
        "--omega",
        type=int,
        default=64,
        help="Moving variance window half-width (2W+1 total)",
    )
    parser.add_argument(
        "--n-streams",
        type=int,
        default=3,
        help="Number of top streams to visualize",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Limit number of files to visualize (for testing)",
    )
    return parser.parse_args()


def load_metadata(json_path: Path) -> Dict[str, Any]:
    """Load fall segment metadata."""
    with open(json_path, "r") as f:
        return json.load(f)


def select_top_streams(
    csi_dict: Dict,
    omega: int = 64,
    n_streams: int = 3,
) -> List[tuple]:
    """
    Select top N streams by q-value.

    Returns:
        List of (antenna, subcarrier) tuples sorted by q-value descending
    """
    csi = csi_dict["csi"]
    q_values = {}

    for ant in range(3):
        for sub in range(30):
            sig = np.abs(csi[:, ant, sub])
            if len(sig) > 2 * omega:
                q = _compute_q(sig, omega)
                q_values[(ant, sub)] = q

    sorted_streams = sorted(q_values.items(), key=lambda x: x[1], reverse=True)
    return [s[0] for s in sorted_streams[:n_streams]]


def plot_moving_variance_with_segments(
    csi_dict: Dict,
    fall_segments: List[Dict],
    filename: str,
    output_path: Path,
    omega: int = 64,
    n_streams: int = 3,
):
    """
    Plot moving variance for top streams with fall segment overlays.

    Args:
        csi_dict: CSI data from load_csi_file
        fall_segments: List of fall segments with start_sec, end_sec
        filename: Filename for title
        output_path: Path to save figure
        omega: Moving variance window half-width
        n_streams: Number of top streams to plot
    """
    csi = csi_dict["csi"]
    timestamps = csi_dict["timestamps"]
    time_sec = (timestamps - timestamps[0]) / 1e6

    # Select top streams
    top_streams = select_top_streams(csi_dict, omega, n_streams)

    # Create subplots: one per selected stream
    fig, axes = plt.subplots(n_streams, 1, figsize=(16, 3 * n_streams))
    if n_streams == 1:
        axes = [axes]

    fig.suptitle(
        f"Moving Variance Verification - {filename}\n(ω={omega}, window size=2×{omega}+1={2*omega+1} samples)",
        fontsize=13,
        fontweight='bold'
    )

    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]

    for stream_idx, (ant, sub) in enumerate(top_streams):
        sig = np.abs(csi[:, ant, sub])
        mv = _moving_variance(sig, omega)
        q_val = _compute_q(sig, omega)

        ax = axes[stream_idx]

        # Plot moving variance
        ax.plot(
            time_sec, mv,
            linewidth=1.5,
            color=colors[stream_idx % len(colors)],
            alpha=0.85,
            label=f"Moving Variance (q={q_val:.2f})"
        )

        # Add fall segment regions
        for seg_idx, seg in enumerate(fall_segments):
            start = seg["start_sec"]
            end = seg["end_sec"]
            duration = seg["duration"]

            # Filled region
            ax.axvspan(
                start, end,
                alpha=0.2,
                facecolor='red',
                edgecolor='darkred',
                linewidth=1.5,
                linestyle='--',
                label='Fall Segment' if seg_idx == 0 else None,
            )

            # Boundary lines
            ax.axvline(start, color='darkred', linestyle=':', linewidth=1, alpha=0.6)
            ax.axvline(end, color='darkred', linestyle=':', linewidth=1, alpha=0.6)

            # Text label
            mid = (start + end) / 2
            max_mv = np.max(mv)
            ax.text(
                mid,
                max_mv * 0.85,
                f'Fall {seg_idx+1}\n[{start:.2f}s, {end:.2f}s]\ndur: {duration:.2f}s',
                ha='center',
                fontsize=8,
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8),
                color='darkred',
                fontweight='bold',
            )

        ax.set_ylabel("Moving Variance", fontsize=10)
        ax.set_title(
            f"Stream {stream_idx+1}: Antenna {ant+1}, Subcarrier {sub+1}",
            fontsize=11,
            fontweight='bold'
        )
        ax.grid(True, alpha=0.3, linestyle='--')
        ax.legend(fontsize=9, loc='upper right')

    axes[-1].set_xlabel("Time (s)", fontsize=10)

    # Add summary info
    info_text = f"Total Fall Segments: {len(fall_segments)}\n"
    if fall_segments:
        durations = [s["duration"] for s in fall_segments]
        info_text += f"Duration Range: {min(durations):.2f}s - {max(durations):.2f}s\n"
        info_text += f"Mean Duration: {np.mean(durations):.2f}s"

    fig.text(
        0.99, 0.01,
        info_text,
        ha='right',
        va='bottom',
        fontsize=9,
        bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8),
        family='monospace',
    )

    plt.tight_layout(rect=[0, 0.03, 1, 0.96])
    plt.savefig(output_path, dpi=120, bbox_inches='tight')
    plt.close()


def main():
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Moving Variance Verification with Fall Segments")
    print("=" * 70)

    # Load metadata
    print("\n[1] Loading metadata...")
    metadata = load_metadata(args.metadata)
    results = metadata["results"]
    print(f"  Found {len(results)} files with fall segments")

    if args.max_files:
        results = results[:args.max_files]
        print(f"  (Limited to {len(results)} files for testing)")

    # Generate visualizations
    print(f"\n[2] Generating moving variance verification plots...")
    print(f"    Window half-width (ω): {args.omega}")
    print(f"    Window size: {2*args.omega+1} samples")
    print(f"    Top streams to plot: {args.n_streams}")

    success_count = 0
    error_count = 0
    total_segments = 0

    for result in tqdm(results, total=len(results)):
        filename = result["filename"]
        fall_segments = result["fall_segments"]
        total_segments += len(fall_segments)

        # Find the filepath
        mendeley_root = Path(args.mendeley_root)
        potential_paths = list(mendeley_root.glob(f"**/{filename}"))

        if not potential_paths:
            error_count += 1
            continue

        filepath = potential_paths[0]

        try:
            csi_dict = load_csi_file(filepath)
        except Exception as e:
            print(f"  [ERROR] {filename}: {e}")
            error_count += 1
            continue

        # Generate visualization
        try:
            output_file = args.output / f"{filename.replace('.csv', '')}_mv_verify.png"
            plot_moving_variance_with_segments(
                csi_dict,
                fall_segments,
                filename,
                output_file,
                omega=args.omega,
                n_streams=args.n_streams,
            )
            success_count += 1
        except Exception as e:
            print(f"  [ERROR] Plotting {filename}: {e}")
            error_count += 1

    # Print summary
    print("\n" + "=" * 70)
    print("Moving Variance Verification Complete!")
    print("=" * 70)
    print(f"Files processed: {success_count}/{len(results)}")
    print(f"Errors: {error_count}")
    print(f"Total fall segments verified: {total_segments}")
    if success_count > 0:
        print(f"Average segments per file: {total_segments/success_count:.2f}")
    print(f"Output directory: {args.output}")
    print(f"Visualizations: {success_count} PNG files")
    print("=" * 70)


if __name__ == "__main__":
    main()
