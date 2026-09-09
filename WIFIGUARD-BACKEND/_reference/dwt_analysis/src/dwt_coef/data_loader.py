"""
Mendeley CSI Dataset Loader

Load and parse Wi-Fi CSI data from Mendeley dataset (Alsaify et al. 2020).
Dataset: Intel 5300 NIC, 1x3 MIMO, 30 subjects across 3 environments.

File naming: E{env}_S{subject:02d}_C{class}_A{activity:02d}_T{trial:02d}.csv
CSI format: 103 columns (13 metadata + 90 complex CSI values)
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import pandas as pd


def parse_complex(s: str) -> complex:
    """
    Parse complex number from string format.

    Handles special format where negative imaginary parts use '+-':
    - "15+15i" → (15+15j)
    - "-17+-23i" → (-17-23j)

    Args:
        s: Complex number string (e.g., "15+15i")

    Returns:
        Complex number
    """
    s = str(s).strip()
    s = s.replace('i', 'j').replace('+-', '-')
    return complex(s)


def parse_filename(filepath: Union[str, Path]) -> Dict[str, Union[int, bool]]:
    """
    Parse metadata from Mendeley filename pattern.

    Pattern: E{env}_S{subject:02d}_C{class}_A{activity:02d}_T{trial:02d}.csv
    Example: E1_S04_C03_A07_T17.csv

    Args:
        filepath: Path to CSV file

    Returns:
        Dict with keys: env, subject, class_id, activity, trial, is_fall
    """
    filename = Path(filepath).stem

    match = re.match(r'E(\d+)_S(\d+)_C(\d+)_A(\d+)_T(\d+)', filename)
    if not match:
        raise ValueError(f"Filename does not match pattern: {filename}")

    env, subject, class_id, activity, trial = map(int, match.groups())

    is_fall = activity in [2, 5]

    return {
        "env": env,
        "subject": subject,
        "class_id": class_id,
        "activity": activity,
        "trial": trial,
        "is_fall": is_fall
    }


def load_csi_file(filepath: Union[str, Path]) -> Dict:
    """
    Load single CSI file and parse to numpy arrays.

    Args:
        filepath: Path to CSV file

    Returns:
        Dict with keys:
            - filepath: Path object
            - env, subject, class_id, activity, trial: int
            - is_fall: bool
            - csi: ndarray of shape (n_packets, 3, 30) with dtype=complex128
            - timestamps: ndarray of shape (n_packets,)
    """
    filepath = Path(filepath)

    metadata = parse_filename(filepath)

    df = pd.read_csv(filepath)

    timestamps = df["timestamp_low"].values.astype(np.float64)

    # Fix corrupted timestamp data:
    # Some files have spurious large values at the end (overflow artifacts)
    # Detect and remove rows with unreasonable timestamp values
    if len(timestamps) > 1:
        ts_diffs = np.diff(timestamps)
        median_diff = np.median(ts_diffs[ts_diffs > 0])
        max_reasonable_diff = median_diff * 100

        valid_mask = np.ones(len(timestamps), dtype=bool)
        for i in range(1, len(timestamps)):
            if ts_diffs[i - 1] > max_reasonable_diff:
                valid_mask[i:] = False
                break

        df = df[valid_mask].reset_index(drop=True)
        timestamps = timestamps[valid_mask]

    timestamps = timestamps.astype(np.uint64)

    csi_data = np.zeros((len(df), 3, 30), dtype=np.complex128)

    for rx in range(1, 4):
        for subcarrier in range(1, 31):
            col_name = f"csi_1_{rx}_{subcarrier}"
            if col_name in df.columns:
                csi_data[:, rx - 1, subcarrier - 1] = df[col_name].apply(parse_complex).values

    return {
        "filepath": filepath,
        "env": metadata["env"],
        "subject": metadata["subject"],
        "class_id": metadata["class_id"],
        "activity": metadata["activity"],
        "trial": metadata["trial"],
        "is_fall": metadata["is_fall"],
        "csi": csi_data,
        "timestamps": timestamps
    }


def get_file_index(
    mendeley_root: Union[str, Path],
) -> pd.DataFrame:
    """
    Create index of all CSV files in Mendeley dataset without loading data.

    Args:
        mendeley_root: Root directory of Mendeley dataset

    Returns:
        DataFrame with columns: filepath, env, subject, class_id, activity, trial, is_fall
    """
    mendeley_root = Path(mendeley_root)

    csv_files = list(mendeley_root.glob("**/E*_S*.csv"))

    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {mendeley_root}")

    records = []
    for filepath in csv_files:
        metadata = parse_filename(filepath)
        metadata["filepath"] = filepath
        records.append(metadata)

    df = pd.DataFrame(records)
    df = df.sort_values(["env", "subject", "class_id", "activity", "trial"]).reset_index(drop=True)

    return df[["filepath", "env", "subject", "class_id", "activity", "trial", "is_fall"]]


def load_dataset(
    mendeley_root: Union[str, Path] = "Mendeley",
    env: Optional[List[int]] = None,
    subjects: Optional[List[int]] = None,
    activities: Optional[List[int]] = None,
    fall_only: bool = False,
    max_files: Optional[int] = None
) -> List[Dict]:
    """
    Load and filter CSI dataset.

    Args:
        mendeley_root: Root directory of Mendeley dataset
        env: List of environments to load (1, 2, 3). None = all.
        subjects: List of subject IDs (1-30). None = all.
        activities: List of activity IDs (1-12). None = all.
        fall_only: If True, load only fall activities (A2, A5).
        max_files: Maximum number of files to load (for testing).

    Returns:
        List of dicts from load_csi_file()
    """
    df = get_file_index(mendeley_root)

    if env is not None:
        df = df[df["env"].isin(env)]

    if subjects is not None:
        df = df[df["subject"].isin(subjects)]

    if activities is not None:
        df = df[df["activity"].isin(activities)]

    if fall_only:
        df = df[df["is_fall"]]

    if max_files is not None:
        df = df.head(max_files)

    data = []
    for _, row in df.iterrows():
        data.append(load_csi_file(row["filepath"]))

    return data
