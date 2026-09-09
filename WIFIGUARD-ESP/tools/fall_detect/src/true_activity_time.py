"""
이 모듈을 그대로 사용하지 마시오. 다른 프로젝트에서 사용하기 위해서는 적절한 수정이 필요합니다.

CSI Preprocessing Pipeline (amfall-based)
Activity Time Segment Detection using Moving Variance

Estimates activity segments from raw CSI data using moving variance:
1. Load samples for specified activities (activity-agnostic)
2. Compute per-file moving variance signal
3. Extract activity segments via threshold
4. Save results with intermediate computation values (max_moving_variance, max_q_value, etc.)
5. Optionally visualize and save per-activity results

Supports arbitrary activity IDs and saves results per-activity when multiple activities specified.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yaml
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dwt_coef.data_loader import get_file_index, load_csi_file
from src.dwt_coef.preprocessing import _moving_variance, _compute_q

# Activity ID to name mapping
ACTIVITY_MAP = {
    1: "Walk", 2: "Fall", 3: "Sit", 4: "Stand", 5: "Fall",
    6: "Walk", 7: "Walk", 8: "Walk", 9: "Walk",
    10: "Sit-Down", 11: "Sit-Down", 12: "Pen",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract activity segments using per-file moving variance analysis",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/fall_labelling.yaml"),
        help="Configuration YAML file (default: config/fall_labelling.yaml)",
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
        default=Path("results/analysis"),
        help="Output directory for results",
    )
    parser.add_argument(
        "--omega",
        type=int,
        default=None,
        help="Moving variance window half-width (overrides config)",
    )
    parser.add_argument(
        "--norm-threshold",
        type=float,
        default=None,
        help="Normalized moving variance threshold [0, 1] (overrides config)",
    )
    parser.add_argument(
        "--min-duration",
        type=float,
        default=None,
        help="Minimum segment duration in seconds (overrides config)",
    )
    parser.add_argument(
        "--merge-threshold",
        type=float,
        default=None,
        help="Gap threshold for merging adjacent segments in seconds (overrides config)",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=None,
        help="Maximum segment duration in seconds (overrides config)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Limit number of files to process (for testing)",
    )
    parser.add_argument(
        "--activities",
        type=int,
        nargs="+",
        default=None,
        help="Activity IDs to process (default: [2, 5] for falls; e.g., --activities 11 12)",
    )
    parser.add_argument(
        "--visualize",
        action="store_true",
        help="Generate visualization plots",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> Dict[str, Any]:
    """Load configuration from YAML file."""
    if not config_path.exists():
        return {}
    with open(config_path, "r") as f:
        return yaml.safe_load(f) or {}


def merge_config_and_args(config: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    """Merge config file with command-line arguments (args override config)."""
    # Extract nested config values
    omega = args.omega or config.get("moving_variance", {}).get("omega", 160)
    n_streams = config.get("moving_variance", {}).get("n_streams", 10)
    norm_threshold = args.norm_threshold or config.get("detection", {}).get("norm_threshold", 0.2)
    min_duration = args.min_duration or config.get("detection", {}).get("min_duration", 0.5)
    merge_threshold = args.merge_threshold or config.get("detection", {}).get("merge_threshold", 0.25)
    max_duration = args.max_duration or config.get("detection", {}).get("max_duration", 2.0)
    max_files = args.max_files or config.get("processing", {}).get("max_files", None)
    activities = args.activities or config.get("processing", {}).get("activities", [2, 5])
    visualize = args.visualize or config.get("processing", {}).get("visualize", False)

    return {
        "omega": omega,
        "n_streams": n_streams,
        "norm_threshold": norm_threshold,
        "min_duration": min_duration,
        "merge_threshold": merge_threshold,
        "max_duration": max_duration,
        "max_files": max_files,
        "activities": activities,
        "visualize": visualize,
    }


def compute_mv_signal(
    csi_dict: Dict,
    omega: int = 64,
    n_streams: int = 10,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Compute per-file min-max normalized moving variance signal.

    Full-signal (non-windowed) approach: computes moving variance for entire
    recording, selects top streams by q-value, averages them, and normalizes.

    Args:
        csi_dict: CSI data from load_csi_file
        omega: Moving variance window half-width (ω=64 → 129-sample window)
        n_streams: Number of top streams by q-value to average (default: 10)

    Returns:
        Tuple of (mv_norm, time_sec, mv_stats) where:
        - mv_norm: normalized moving variance [0, 1], shape (N,)
        - time_sec: time axis in seconds, shape (N,)
        - mv_stats: dict with intermediate computation values
    """
    csi = csi_dict["csi"]
    timestamps = csi_dict["timestamps"]
    n_packets = csi.shape[0]
    time_sec = (timestamps - timestamps[0]) / 1e6

    # Step 1: Select top n_streams by q-value (full recording basis)
    q_values = {}
    for ant in range(3):
        for sub in range(30):
            sig = np.abs(csi[:, ant, sub])
            q_values[(ant, sub)] = _compute_q(sig, omega)

    top_streams = sorted(q_values.items(), key=lambda x: x[1], reverse=True)[:n_streams]

    # Step 2: Compute moving variance for each top stream, average across streams
    mv_sum = np.zeros(n_packets)
    for (ant, sub), _ in top_streams:
        sig = np.abs(csi[:, ant, sub])
        mv = _moving_variance(sig, omega)
        mv_sum += mv

    mv_avg = mv_sum / len(top_streams)

    # Step 3: Min-max normalization to [0, 1]
    mv_min = np.min(mv_avg)
    mv_max = np.max(mv_avg)
    mv_range = mv_max - mv_min

    if mv_range < 1e-10:  # All values identical
        mv_norm = np.zeros_like(mv_avg)
    else:
        mv_norm = (mv_avg - mv_min) / mv_range

    # Collect intermediate computation values
    mv_stats = {
        "max_moving_variance": float(mv_max),
        "mean_moving_variance": float(np.mean(mv_avg)),
        "mv_range": float(mv_range),
        "max_q_value": float(max(q_values.values())) if q_values else 0.0,
        "top_stream_q_values": [float(q) for _, q in top_streams],
    }

    return mv_norm, time_sec, mv_stats


def merge_adjacent_segments(
    segments: List[Dict[str, float]],
    merge_threshold: float = 0.25,
) -> List[Dict[str, float]]:
    """
    Merge adjacent segments if gap is smaller than threshold.

    Args:
        segments: List of segments
        merge_threshold: Maximum gap (seconds) to merge segments

    Returns:
        Merged segment list
    """
    if len(segments) <= 1:
        return segments

    merged = []
    current = segments[0].copy()

    for next_seg in segments[1:]:
        gap = next_seg["start_sec"] - current["end_sec"]
        if gap <= merge_threshold:
            # Merge: extend end time and update q_avg
            current["end_sec"] = next_seg["end_sec"]
            current["duration"] = current["end_sec"] - current["start_sec"]
            current["q_avg"] = (current["q_avg"] + next_seg["q_avg"]) / 2
        else:
            # Gap too large, save current and start new
            merged.append(current)
            current = next_seg.copy()

    merged.append(current)
    return merged


def extract_fall_segments(
    mv_norm: np.ndarray,
    time_sec: np.ndarray,
    norm_threshold: float = 0.6,
    min_duration: float = 0.5,
    merge_threshold: float = 0.25,
    max_duration: float = 2.0,
) -> List[Dict[str, float]]:
    """
    Extract continuous segments from normalized moving variance signal.

    Applies threshold directly to per-sample normalized MV, groups consecutive
    above-threshold samples into segments.

    Args:
        mv_norm: normalized moving variance signal [0, 1], shape (N,)
        time_sec: time axis in seconds, shape (N,)
        norm_threshold: MV threshold [0, 1] (default: 0.6)
        min_duration: Minimum segment duration in seconds (default: 0.5s)
        merge_threshold: Gap to merge adjacent segments (default: 0.25s)
        max_duration: Maximum segment duration (default: 2.0s, hard cap)

    Returns:
        List of dicts: {start_sec, end_sec, duration, q_avg}
    """
    if len(mv_norm) == 0:
        return []

    # Per-sample boolean mask
    fall_mask = mv_norm > norm_threshold

    # Group consecutive True samples into segments
    fall_segments = []
    in_fall = False
    fall_start_idx = None

    for idx, is_fall in enumerate(fall_mask):
        if is_fall and not in_fall:
            fall_start_idx = idx
            in_fall = True
        elif not is_fall and in_fall:
            fall_end_idx = idx - 1
            fall_start = time_sec[fall_start_idx]
            fall_end = time_sec[fall_end_idx]
            duration = fall_end - fall_start
            if duration >= min_duration:
                avg_mv = np.mean(mv_norm[fall_start_idx:fall_end_idx+1])
                fall_segments.append({
                    "start_sec": fall_start,
                    "end_sec": fall_end,
                    "duration": duration,
                    "q_avg": avg_mv,
                })
            in_fall = False

    # Handle case where fall extends to end
    if in_fall and fall_start_idx is not None:
        fall_start = time_sec[fall_start_idx]
        fall_end = time_sec[-1]
        duration = fall_end - fall_start
        if duration >= min_duration:
            avg_mv = np.mean(mv_norm[fall_start_idx:])
            fall_segments.append({
                "start_sec": fall_start,
                "end_sec": fall_end,
                "duration": duration,
                "q_avg": avg_mv,
            })

    # Merge adjacent segments
    fall_segments = merge_adjacent_segments(fall_segments, merge_threshold)

    # Apply max_duration cap: clip segments exceeding max_duration to center region
    capped = []
    for seg in fall_segments:
        if seg["duration"] <= max_duration:
            capped.append(seg)
        else:
            # Clip to centered max_duration window
            center = (seg["start_sec"] + seg["end_sec"]) / 2
            new_start = center - max_duration / 2
            new_end = center + max_duration / 2
            capped.append({
                "start_sec": new_start,
                "end_sec": new_end,
                "duration": max_duration,
                "q_avg": seg["q_avg"],
            })
    fall_segments = capped

    return fall_segments


def process_all_falls(
    file_index: pd.DataFrame,
    omega: int = 64,
    norm_threshold: float = 0.2,
    min_duration: float = 0.5,
    merge_threshold: float = 0.25,
    max_duration: float = 2.0,
    max_files: Optional[int] = None,
    activities: Optional[list] = None,
    n_streams: int = 10,
) -> Dict[str, Any]:
    """
    Process all samples of specified activities using full-signal min-max normalized moving variance.

    Args:
        activities: List of activity IDs to process (default: [2, 5] for falls)

    Returns:
        Dict with: files_processed, method, config, results (list of per-file results)
    """
    if activities is None:
        activities = [2, 5]

    # Filter for specified activities
    fall_files = file_index[file_index["activity"].isin(activities)].copy()

    if max_files:
        fall_files = fall_files.head(max_files)

    print(f"Processing {len(fall_files)} files...")
    print("  Method: Full-signal moving variance (no sliding windows)")
    print(f"  Normalized threshold: {norm_threshold}")
    print(f"  Min duration: {min_duration}s")
    print(f"  Merge threshold: {merge_threshold}s")
    print(f"  Max duration (hard cap): {max_duration}s")

    results = []

    for idx, row in tqdm(fall_files.iterrows(), total=len(fall_files)):
        filepath = Path(row["filepath"])

        try:
            csi_dict = load_csi_file(filepath)
        except Exception as e:
            print(f"  Error loading {filepath.name}: {e}")
            continue

        # Compute full-signal normalized moving variance
        mv_norm, time_sec, mv_stats = compute_mv_signal(csi_dict, omega=omega, n_streams=n_streams)

        if len(mv_norm) == 0:
            continue

        # Extract segments from normalized MV directly
        fall_segs = extract_fall_segments(
            mv_norm,
            time_sec,
            norm_threshold=norm_threshold,
            min_duration=min_duration,
            merge_threshold=merge_threshold,
            max_duration=max_duration,
        )

        file_result = {
            "filename": filepath.name,
            "env": row["env"],
            "subject": row["subject"],
            "activity": row["activity"],
            "trial": row["trial"],
            "n_packets": csi_dict["csi"].shape[0],
            "duration_sec": (csi_dict["timestamps"][-1] - csi_dict["timestamps"][0]) / 1e6,
            "fall_segments": fall_segs,
            "n_fall_segments": len(fall_segs),
            "max_moving_variance": mv_stats["max_moving_variance"],
            "mean_moving_variance": mv_stats["mean_moving_variance"],
            "mv_range": mv_stats["mv_range"],
            "max_q_value": mv_stats["max_q_value"],
            "top_stream_q_values": mv_stats["top_stream_q_values"],
        }
        results.append(file_result)

    return {
        "files_processed": len(results),
        "method": "full_signal_mv_normalized",
        "config": {
            "omega": omega,
            "norm_threshold": norm_threshold,
            "min_duration": min_duration,
            "merge_threshold": merge_threshold,
            "max_duration": max_duration,
        },
        "results": results,
    }


def visualize_fall_detection(
    csi_dict: Dict,
    segment_df: pd.DataFrame,
    fall_segments: List[Dict],
    q_threshold: float,
    output_path: Path,
    title: str = "",
):
    """
    Visualize raw CSI, moving variance, and estimated segments.
    """
    csi = csi_dict["csi"]
    timestamps = csi_dict["timestamps"]
    time_sec = (timestamps - timestamps[0]) / 1e6

    # Select representative stream (high q-value)
    max_q = 0
    best_stream = (0, 0)
    for ant in range(3):
        for sub in range(30):
            sig = np.abs(csi[:, ant, sub])
            q = _compute_q(sig, 64)
            if q > max_q:
                max_q = q
                best_stream = (ant, sub)

    ant, sub = best_stream
    sig = np.abs(csi[:, ant, sub])
    mv = _moving_variance(sig, 64)

    fig, axes = plt.subplots(3, 1, figsize=(16, 10))
    fig.suptitle(title, fontsize=12, fontweight='bold')

    # Plot 1: Raw signal
    ax = axes[0]
    ax.plot(time_sec, sig, linewidth=0.8, alpha=0.7, label="Raw CSI (Amplitude)")
    for seg in fall_segments:
        ax.axvspan(seg["start_sec"], seg["end_sec"], alpha=0.2, color='red', label="Segment")
    ax.set_ylabel("Amplitude", fontsize=10)
    ax.set_title(f"Raw CSI - Antenna {ant+1}, Subcarrier {sub+1} (q={max_q:.2f})", fontsize=10)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    # Plot 2: Moving variance
    ax = axes[1]
    ax.plot(time_sec, mv, linewidth=0.8, alpha=0.7, label="Moving Variance (ω=64)")
    ax.axhline(q_threshold, color='red', linestyle='--', label=f"Threshold={q_threshold:.2f}")
    for seg in fall_segments:
        ax.axvspan(seg["start_sec"], seg["end_sec"], alpha=0.2, color='red')
    ax.set_ylabel("Variance", fontsize=10)
    ax.set_title("Moving Variance", fontsize=10)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    # Plot 3: Segment-wise q-values
    ax = axes[2]
    if not segment_df.empty:
        ax.bar(
            segment_df["time_start_sec"],
            segment_df["q_avg"],
            width=0.2,
            alpha=0.7,
            label="Segment Q-avg"
        )
    ax.axhline(q_threshold, color='red', linestyle='--', label=f"Threshold={q_threshold:.2f}")
    for seg in fall_segments:
        ax.axvspan(seg["start_sec"], seg["end_sec"], alpha=0.2, color='red')
    ax.set_xlabel("Time (s)", fontsize=10)
    ax.set_ylabel("Q-value", fontsize=10)
    ax.set_title("Per-Segment Q-values", fontsize=10)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=100, bbox_inches='tight')
    plt.close()


def main():
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Activity Time Segment Analysis Pipeline")
    print("=" * 70)

    # Load config and merge with args
    print("\n[0] Loading configuration...")
    config = load_config(args.config)
    params = merge_config_and_args(config, args)
    print(f"  Config file: {args.config}")
    print(f"  Omega: {params['omega']}")
    print(f"  Norm threshold: {params['norm_threshold']}")
    print(f"  Min duration: {params['min_duration']}s")
    print(f"  Merge threshold: {params['merge_threshold']}s")
    print(f"  Max duration: {params['max_duration']}s")

    # Load file index
    print("\n[1] Loading file index...")
    file_index = get_file_index(args.mendeley_root)
    print(f"  Found {len(file_index)} files")

    # Process all activities
    print("\n[2] Processing activity samples...")
    analysis = process_all_falls(
        file_index,
        omega=params["omega"],
        norm_threshold=params["norm_threshold"],
        min_duration=params["min_duration"],
        merge_threshold=params["merge_threshold"],
        max_duration=params["max_duration"],
        max_files=params["max_files"],
        activities=params["activities"],
        n_streams=params["n_streams"],
    )

    # Save results summary
    print("\n[3] Saving results...")

    # Group results by activity
    results_by_activity = {}
    for result in analysis["results"]:
        activity = result["activity"]
        if activity not in results_by_activity:
            results_by_activity[activity] = []
        results_by_activity[activity].append(result)

    # Check if multiple activities or single activity
    multiple_activities = len(results_by_activity) > 1

    # Save per-activity or global results
    for activity, activity_results in sorted(results_by_activity.items()):
        # Determine output directory
        if multiple_activities:
            activity_name = ACTIVITY_MAP.get(activity, f"A{activity:02d}")
            output_subdir = args.output / f"A{activity:02d}_{activity_name}"
            output_subdir.mkdir(parents=True, exist_ok=True)
        else:
            output_subdir = args.output
            output_subdir.mkdir(parents=True, exist_ok=True)

        # Save CSV
        rows = []
        for result in activity_results:
            for seg in result["fall_segments"]:
                rows.append({
                    "filename": result["filename"],
                    "env": result["env"],
                    "subject": result["subject"],
                    "activity": result["activity"],
                    "trial": result["trial"],
                    "fall_start_sec": seg["start_sec"],
                    "fall_end_sec": seg["end_sec"],
                    "fall_duration_sec": seg["duration"],
                    "mv_avg": seg["q_avg"],
                    "max_moving_variance": result["max_moving_variance"],
                    "mean_moving_variance": result["mean_moving_variance"],
                    "mv_range": result["mv_range"],
                    "max_q_value": result["max_q_value"],
                })

        csv_filename = "activity_segments.csv" if multiple_activities else "03_fall_segments_v2.csv"
        results_csv = output_subdir / csv_filename

        if rows:
            results_df = pd.DataFrame(rows)
            results_df.to_csv(results_csv, index=False)
            print(f"  [OK] Saved {len(rows)} segments to {output_subdir.name}/{csv_filename}")
        else:
            print(f"  (No segments detected for activity {activity})")

        # Save detailed JSON
        json_filename = "activity_metadata.json" if multiple_activities else "03_fall_labelling_metadata_v2.json"
        json_path = output_subdir / json_filename

        total_segs = sum(r["n_fall_segments"] for r in activity_results)
        json_data = {
            "method": analysis["method"],
            "config": analysis["config"],
            "summary": {
                "files_processed": len(activity_results),
                "total_segments": total_segs,
                "avg_segments_per_file": total_segs / len(activity_results) if activity_results else 0,
            },
            "results": activity_results
        }
        with open(json_path, "w") as f:
            json.dump(json_data, f, indent=2)
        print(f"  [OK] Saved metadata to {output_subdir.name}/{json_filename}")

    # Generate visualizations for selected files (if enabled)
    if args.visualize:
        print("\n[4] Generating visualizations...")
        for activity, activity_results in sorted(results_by_activity.items()):
            if multiple_activities:
                activity_name = ACTIVITY_MAP.get(activity, f"A{activity:02d}")
                viz_dir = args.output / f"A{activity:02d}_{activity_name}" / "visualizations"
            else:
                viz_dir = args.output / "visualizations"
            viz_dir.mkdir(parents=True, exist_ok=True)

            for idx, result in enumerate(activity_results[:5]):  # First 5 files per activity
                filepath = Path(args.mendeley_root) / result["filename"]
                try:
                    csi_dict = load_csi_file(filepath)
                    fall_segs = result["fall_segments"]

                    output_file = viz_dir / f"activity_{result['filename'].replace('.csv', '')}.png"
                    visualize_fall_detection(
                        csi_dict,
                        pd.DataFrame(),
                        fall_segs,
                        0.2,
                        output_file,
                        title=f"{result['filename']} - Activity Segments"
                    )
                    print(f"  [OK] Saved {output_file.name}")
                except Exception as e:
                    print(f"  Error visualizing {result['filename']}: {e}")

    # Print summary statistics
    total_segments = sum(r["n_fall_segments"] for r in analysis["results"])
    files_with_segments = sum(1 for r in analysis["results"] if r["n_fall_segments"] > 0)

    print("\n" + "=" * 70)
    print("Activity Time Analysis Complete!")
    print("=" * 70)
    print(f"Method: {analysis['method']}")
    print(f"Normalized threshold: {params['norm_threshold']}")
    print(f"Min duration: {params['min_duration']}s")
    print(f"Merge threshold: {params['merge_threshold']}s")
    print()
    print(f"Total files processed: {analysis['files_processed']}")

    if multiple_activities:
        print("\nPer-Activity Summary:")
        for activity in sorted(results_by_activity.keys()):
            activity_name = ACTIVITY_MAP.get(activity, f"A{activity:02d}")
            activity_results = results_by_activity[activity]
            activity_segments = sum(r["n_fall_segments"] for r in activity_results)
            activity_files_with_segs = sum(1 for r in activity_results if r["n_fall_segments"] > 0)
            n_files = len(activity_results)
            print(f"  A{activity:02d} ({activity_name}): {n_files} files, {activity_segments} segments, {activity_files_with_segs} files with segments")
    else:
        first_activity = params['activities'][0]
        activity_name = ACTIVITY_MAP.get(first_activity, f"A{first_activity:02d}")
        print(f"Activity: {activity_name}")

    print()
    print(f"Files with segments: {files_with_segments} ({100*files_with_segments/len(analysis['results']):.1f}%)")
    print(f"Total segments: {total_segments}")
    if len(analysis["results"]) > 0:
        print(f"Avg segments per file: {total_segments/len(analysis['results']):.2f}")
    print("=" * 70)


if __name__ == "__main__":
    main()
