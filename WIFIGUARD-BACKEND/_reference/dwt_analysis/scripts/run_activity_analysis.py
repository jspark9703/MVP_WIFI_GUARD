#!/usr/bin/env python3
"""
Activity-Based Analysis Pipeline

Run the three analysis scripts (04, 05, 06) for specified activities.
Organizes results by activity: results/analysis/{activity_name}/

Usage:
    python scripts/run_activity_analysis.py --activities 11 12 --omega 160 --max-files 50
    python scripts/run_activity_analysis.py --activity-names "Sit-Down,Push" --omega 160
"""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import pandas as pd

# Activity ID to name mapping
ACTIVITY_MAP = {
    1: "Walk",
    2: "Fall",
    3: "Sit",
    4: "Stand",
    5: "Fall",
    6: "Pick",
    7: "Lie",
    8: "Bend",
    9: "Jump",
    10: "Turn",
    11: "Sit-Down",
    12: "Pen",
}

REVERSE_ACTIVITY_MAP = {v: k for k, v in ACTIVITY_MAP.items()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run activity-based analysis pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--activities",
        type=int,
        nargs="+",
        default=[11, 12],
        help="Activity IDs to analyze (default: 11=Sit-Down, 12=Push)",
    )
    parser.add_argument(
        "--omega",
        type=int,
        default=160,
        help="Moving variance window half-width (default: 160)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=50,
        help="Maximum files per activity (default: 50)",
    )
    parser.add_argument(
        "--mendeley-root",
        type=Path,
        default=Path("Mendeley"),
        help="Root directory of Mendeley dataset",
    )
    return parser.parse_args()


def run_activity_labelling(
    activity: int,
    activity_name: str,
    omega: int,
    max_files: int,
    mendeley_root: Path,
) -> bool:
    """
    Run true_fall_labelling.py for a specific activity.
    Outputs to results/analysis/{activity_name}/
    """
    output_dir = Path("results/analysis") / activity_name
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"[1/3] Activity Labelling: {activity_name} (Activity {activity})")
    print(f"{'='*70}")

    cmd = [
        "python",
        "scripts/true_activity_time.py",
        "--output",
        str(output_dir),
        "--omega",
        str(omega),
        "--max-files",
        str(max_files),
        "--mendeley-root",
        str(mendeley_root),
        "--activities",
        str(activity),
    ]

    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        print(f"[OK] {activity_name} labelling complete")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Error running labelling for {activity_name}: {e}")
        return False


def run_segment_verification(
    activity_name: str,
    omega: int,
    mendeley_root: Path,
) -> bool:
    """
    Run 05_verify_fall_segments.py for a specific activity.
    """
    output_dir = Path("results/analysis") / activity_name
    metadata_file = output_dir / "03_fall_labelling_metadata_v2.json"

    if not metadata_file.exists():
        print(f"Metadata file not found: {metadata_file}")
        return False

    print(f"\n{'='*70}")
    print(f"[2/3] Segment Verification: {activity_name}")
    print(f"{'='*70}")

    cmd = [
        "python",
        "scripts/05_verify_fall_segments.py",
        "--metadata",
        str(metadata_file),
        "--output",
        str(output_dir / "segment_verification"),
        "--mendeley-root",
        str(mendeley_root),
    ]

    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        print(f"[OK] {activity_name} segment verification complete")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Error running segment verification for {activity_name}: {e}")
        return False


def run_moving_variance_verification(
    activity_name: str,
    omega: int,
    mendeley_root: Path,
) -> bool:
    """
    Run 06_moving_variance_verification.py for a specific activity.
    """
    output_dir = Path("results/analysis") / activity_name
    metadata_file = output_dir / "03_fall_labelling_metadata_v2.json"

    if not metadata_file.exists():
        print(f"Metadata file not found: {metadata_file}")
        return False

    print(f"\n{'='*70}")
    print(f"[3/3] Moving Variance Verification: {activity_name}")
    print(f"{'='*70}")

    cmd = [
        "python",
        "scripts/06_moving_variance_verification.py",
        "--metadata",
        str(metadata_file),
        "--output",
        str(output_dir / "moving_variance_verification"),
        "--omega",
        str(omega),
        "--mendeley-root",
        str(mendeley_root),
    ]

    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        print(f"[OK] {activity_name} moving variance verification complete")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Error running moving variance verification for {activity_name}: {e}")
        return False


def main():
    args = parse_args()

    print("=" * 70)
    print("ACTIVITY-BASED ANALYSIS PIPELINE")
    print("=" * 70)
    print(f"\nActivities to analyze:")
    for activity in args.activities:
        activity_name = ACTIVITY_MAP.get(activity, f"Unknown_A{activity:02d}")
        print(f"  - {activity_name} (Activity {activity})")
    print(f"\nParameters:")
    print(f"  Omega: {args.omega}")
    print(f"  Max files: {args.max_files}")
    print(f"  Mendeley root: {args.mendeley_root}")

    # Run pipeline for each activity
    results = {}

    for activity in args.activities:
        activity_name = ACTIVITY_MAP.get(activity, f"Unknown_A{activity:02d}")
        print(f"\n{'='*70}")
        print(f"PROCESSING: {activity_name} (Activity {activity})")
        print(f"{'='*70}")

        # Step 1: Labelling
        success1 = run_activity_labelling(
            activity, activity_name, args.omega, args.max_files, args.mendeley_root
        )

        if not success1:
            print(f"[SKIP] Skipping verification steps for {activity_name}")
            results[activity_name] = "FAILED"
            continue

        # Step 2: Segment Verification
        success2 = run_segment_verification(activity_name, args.omega, args.mendeley_root)

        # Step 3: Moving Variance Verification
        success3 = run_moving_variance_verification(activity_name, args.omega, args.mendeley_root)

        if success2 and success3:
            results[activity_name] = "COMPLETE"
        elif success1:
            results[activity_name] = "PARTIAL"
        else:
            results[activity_name] = "FAILED"

    # Summary
    print(f"\n{'='*70}")
    print("ANALYSIS SUMMARY")
    print(f"{'='*70}")
    for activity_name, status in results.items():
        symbol = "[OK]" if status == "COMPLETE" else "[WARN]" if status == "PARTIAL" else "[FAIL]"
        print(f"{symbol} {activity_name}: {status}")
        output_dir = Path("results/analysis") / activity_name
        print(f"   Output: {output_dir}")

    print(f"\n{'='*70}")


if __name__ == "__main__":
    main()
