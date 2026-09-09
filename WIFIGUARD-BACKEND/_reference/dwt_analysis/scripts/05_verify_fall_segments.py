#!/usr/bin/env python3
"""
Verify Fall Segment Extraction

Visualize extracted fall segments against raw CSI data to validate correctness.
For each file: plot antenna 1,2,3 (subcarrier 15) with fall segment overlays.
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
import yaml
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dwt_coef.data_loader import get_file_index, load_csi_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify fall segments against raw CSI data",
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
        default=Path("results/analysis/fall_segment_verification"),
        help="Output directory for verification plots",
    )
    parser.add_argument(
        "--subcarrier",
        type=int,
        default=15,
        help="Subcarrier index (1-30, default: 15)",
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


def plot_fall_segment_verification(
    csi_dict: Dict,
    fall_segments: List[Dict],
    filename: str,
    output_path: Path,
    subcarrier: int = 15,
):
    """
    Plot raw CSI (3 antennas) with fall segment overlays.

    Args:
        csi_dict: CSI data from load_csi_file
        fall_segments: List of fall segments with start_sec, end_sec
        filename: Filename for title
        output_path: Path to save figure
        subcarrier: Subcarrier index (1-30)
    """
    csi = csi_dict["csi"]
    timestamps = csi_dict["timestamps"]
    time_sec = (timestamps - timestamps[0]) / 1e6

    fig, axes = plt.subplots(3, 1, figsize=(16, 10))
    fig.suptitle(f"Fall Segment Verification - {filename}", fontsize=13, fontweight='bold')

    antenna_labels = ["Antenna 1", "Antenna 2", "Antenna 3"]
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c"]

    for ant_idx in range(3):
        # Extract amplitude for this antenna, subcarrier
        sig = np.abs(csi[:, ant_idx, subcarrier - 1])

        ax = axes[ant_idx]

        # Plot raw CSI signal
        ax.plot(time_sec, sig, linewidth=1.0, color=colors[ant_idx], alpha=0.8, label="Raw CSI Amplitude")

        # Overlay fall segment regions
        for seg_idx, seg in enumerate(fall_segments):
            start = seg["start_sec"]
            end = seg["end_sec"]
            duration = seg["duration"]

            # Add semi-transparent rectangle for fall region
            rect = mpatches.Rectangle(
                (start, 0),
                duration,
                ax.get_ylim()[1] * 1.2,
                alpha=0.25,
                facecolor='red',
                edgecolor='darkred',
                linewidth=1.5,
                linestyle='--',
                label=f'Fall Segment {seg_idx+1}' if seg_idx == 0 else None,
            )
            ax.add_patch(rect)

            # Add text label
            ax.text(
                start + duration/2,
                ax.get_ylim()[1] * 0.9,
                f'Fall {seg_idx+1}\n[{start:.2f}s - {end:.2f}s]',
                ha='center',
                fontsize=9,
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.7),
                color='darkred',
                fontweight='bold',
            )

        ax.set_ylabel("Amplitude", fontsize=10)
        ax.set_title(
            f"{antenna_labels[ant_idx]} - Subcarrier {subcarrier}",
            fontsize=11,
            fontweight='bold'
        )
        ax.grid(True, alpha=0.3, linestyle='--')
        ax.legend(fontsize=9, loc='upper right')

    axes[-1].set_xlabel("Time (s)", fontsize=10)

    # Add overall info box
    info_text = f"Total Fall Segments: {len(fall_segments)}\n"
    if fall_segments:
        avg_duration = np.mean([s["duration"] for s in fall_segments])
        info_text += f"Avg Duration: {avg_duration:.2f}s\n"
        q_val = fall_segments[0].get('q_avg', 0.0) if isinstance(fall_segments[0].get('q_avg'), (int, float)) else 0.0
        info_text += f"MV-avg: {q_val:.3f}"

    fig.text(
        0.99, 0.01,
        info_text,
        ha='right',
        va='bottom',
        fontsize=9,
        bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8),
        family='monospace',
    )

    plt.tight_layout(rect=[0, 0.05, 1, 0.97])
    plt.savefig(output_path, dpi=120, bbox_inches='tight')
    plt.close()


def main():
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Fall Segment Verification")
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
    print(f"\n[2] Generating verification plots...")
    print(f"    Subcarrier: {args.subcarrier}")
    print(f"    Antennas: 1, 2, 3")

    success_count = 0
    error_count = 0

    for result in tqdm(results, total=len(results)):
        filename = result["filename"]
        fall_segments = result["fall_segments"]

        # Find the filepath by searching Mendeley directory
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
            output_file = args.output / f"{filename.replace('.csv', '')}_verify.png"
            plot_fall_segment_verification(
                csi_dict,
                fall_segments,
                filename,
                output_file,
                subcarrier=args.subcarrier,
            )
            success_count += 1
        except Exception as e:
            print(f"  [ERROR] Plotting {filename}: {e}")
            error_count += 1

    # Print summary statistics
    total_segments = sum(r["n_fall_segments"] for r in results)
    avg_duration = np.mean([
        s["duration"]
        for r in results
        for s in r["fall_segments"]
    ]) if total_segments > 0 else 0

    print("\n" + "=" * 70)
    print("Verification Complete!")
    print("=" * 70)
    print(f"Files processed: {success_count}/{len(results)}")
    print(f"Errors: {error_count}")
    print(f"Total fall segments verified: {total_segments}")
    print(f"Average fall duration: {avg_duration:.2f}s")
    print(f"Output directory: {args.output}")
    print("=" * 70)


if __name__ == "__main__":
    main()
