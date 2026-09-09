"""
CSI Preprocessing Pipeline (amfall-based)

Implements signal preprocessing stages:
1. Amplitude extraction from complex CSI
2. Resampling to regular grid (linear interpolation)
3. CSI stream selection (amfall q(h) metric)
4. PCA and PC selection (amfall q(p) metric)
"""

from typing import Dict, List, Optional, Tuple, Any

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy import signal


def _moving_variance(x: np.ndarray, W: int) -> np.ndarray:
    """
    Compute moving variance using efficient convolution.

    Per AmFall spec: υ(n; h) = (1/2W) * Σ_{k=n-W}^{n+W} (||h[k]|| - μ_h(n))²
    Denominator is 2W (window runs from n-W to n+W but counts as 2W for normalization).

    Args:
        x: 1D signal, shape (N,)
        W: Half-width of window (denominator = 2W)

    Returns:
        Moving variance, shape (N,)
    """
    size = 2 * W + 1
    mu = uniform_filter1d(x, size=size, mode='nearest')
    mu2 = uniform_filter1d(x ** 2, size=size, mode='nearest')
    # Variance with denominator 2W (per spec), not 2W+1
    variance = mu2 - mu ** 2
    # Rescale to match spec denominator: (2W+1)/2W
    if W > 0:
        variance = variance * (2 * W + 1) / (2 * W)
    return variance


def _compute_q(signal: np.ndarray, omega: int) -> float:
    """
    Compute q metric: max(moving_variance) / mean(moving_variance).

    Measures signal sensitivity to human activity.

    Args:
        signal: 1D signal, shape (N,)
        omega: Window half-width for moving variance

    Returns:
        q value (float, ≥ 0)
    """
    upsilon = _moving_variance(np.abs(signal), omega)
    mean_v = np.mean(upsilon)
    if mean_v < 1e-10:
        return 0.0
    return float(np.max(upsilon) / mean_v)


def extract_amplitude(
    csi: np.ndarray,
    timestamps: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Extract amplitude from complex CSI, preserving antenna structure.

    Args:
        csi: Complex CSI array, shape (N, 3, 30)
        timestamps: Timestamps, shape (N,)

    Returns:
        (amplitude, timestamps)
        - amplitude: shape (N, 3, 30) float32 — magnitude of complex values
        - timestamps: unchanged
    """
    amplitude = np.abs(csi).astype(np.float32)
    return amplitude, timestamps


def sliding_window_raw(
    csi: np.ndarray,
    timestamps: np.ndarray,
    window_sec: float = 3.0,
    stride_sec: float = 0.25,
    min_packets: int = 10
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """
    Segment CSI using sliding window on raw irregular timestamps.

    Args:
        csi: Complex CSI, shape (N, 3, 30)
        timestamps: Timestamps in microseconds, shape (N,)
        window_sec: Window size in seconds
        stride_sec: Stride in seconds
        min_packets: Minimum packets required in a segment

    Returns:
        List of (csi_segment, timestamps_segment) tuples
        - Each segment's CSI: (n_seg_packets, 3, 30)
        - Each segment's timestamps: (n_seg_packets,)
    """
    window_us = window_sec * 1_000_000
    stride_us = stride_sec * 1_000_000

    if len(timestamps) == 0:
        return []

    timestamps = timestamps.astype(np.float64)

    segments = []
    t_start = float(timestamps[0])
    t_end = float(timestamps[-1])

    while t_start + window_us < t_end + 1.0:
        t_window_end = t_start + window_us
        mask = (timestamps >= t_start) & (timestamps < t_window_end)
        mask = mask.astype(bool)

        count = np.sum(mask, dtype=np.int64)
        if count >= min_packets:
            segments.append((csi[mask], timestamps[mask]))

        t_start += stride_us

    return segments


def resample_signal(
    timestamps: np.ndarray,
    signal: np.ndarray,
    fs_hz: float = 320.0,
    tolerance_ms: float = 2.0,
    max_interp_gap_steps: int = 64
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Resample signal to regular grid using linear interpolation.

    Handles irregular timestamp gaps by:
    - Linearly interpolating if gap fits within tolerance
    - Skipping interpolation (fallback) otherwise
    - Recording statistics for both cases

    Args:
        timestamps: Irregular timestamps in microseconds, shape (N,)
        signal: Signal to resample, shape (N, ...). Can be any shape after first dim.
        fs_hz: Target sampling frequency (Hz)
        tolerance_ms: Tolerance for grid fitting (ms)
        max_interp_gap_steps: Maximum interpolation steps

    Returns:
        (resampled_signal, stats)
        - resampled_signal: shape (M, ...) where M ≥ N
        - stats: dict with interp_steps, fallback_steps, gaps info
    """
    if len(signal) == 0:
        return signal.astype(np.float32), {
            "raw_samples": 0,
            "resampled_samples": 0,
            "interp_steps": 0,
            "fallback_steps": 0,
            "irregular_gaps": 0,
            "nonpositive_gaps": 0,
        }

    timestamps = timestamps.astype(np.float64)
    signal = signal.astype(np.float32)

    grid_us = 1_000_000.0 / fs_hz
    tolerance_us = tolerance_ms * 1000.0

    rows = [signal[0].astype(np.float32)]
    interp_steps = 0
    fallback_steps = 0
    irregular_gaps = 0
    nonpositive_gaps = 0

    for i in range(1, len(signal)):
        gap = float(timestamps[i] - timestamps[i - 1])

        if gap <= 0:
            rows.append(signal[i].astype(np.float32))
            fallback_steps += 1
            nonpositive_gaps += 1
            continue

        nearest_steps = max(1, int(round(gap / grid_us)))
        can_interp = (
            nearest_steps <= max_interp_gap_steps
            and abs(gap - nearest_steps * grid_us) <= tolerance_us
        )

        if can_interp:
            for step in range(1, nearest_steps):
                alpha = step / nearest_steps
                interpolated = (
                    (1.0 - alpha) * signal[i - 1] + alpha * signal[i]
                ).astype(np.float32)
                rows.append(interpolated)
                interp_steps += 1
            rows.append(signal[i].astype(np.float32))
        else:
            rows.append(signal[i].astype(np.float32))
            fallback_steps += 1
            irregular_gaps += 1

    resampled = np.stack(rows, axis=0).astype(np.float32)

    stats = {
        "raw_samples": int(len(signal)),
        "resampled_samples": int(len(resampled)),
        "interp_steps": int(interp_steps),
        "fallback_steps": int(fallback_steps),
        "irregular_gaps": int(irregular_gaps),
        "nonpositive_gaps": int(nonpositive_gaps),
    }

    return resampled, stats


def bandpass_filter(
    amp: np.ndarray,
    fs_hz: float = 320.0,
    low_hz: float = 0.5,
    high_hz: float = 150.0,
    order: int = 4
) -> np.ndarray:
    """
    Apply bandpass Butterworth filter to amplitude signal.

    Uses zero-phase filtering (sosfiltfilt) to preserve signal timing.

    Args:
        amp: Signal array, shape (N, ...) — any shape with first dim = time
        fs_hz: Sampling frequency
        low_hz: Low cutoff frequency
        high_hz: High cutoff frequency
        order: Filter order

    Returns:
        Filtered signal, same shape as input
    """
    sos = signal.butter(order, [low_hz, high_hz], btype='band', fs=fs_hz, output='sos')
    filtered = signal.sosfiltfilt(sos, amp, axis=0)
    return filtered.astype(np.float32)


def select_streams(
    amplitude_3d: np.ndarray,
    omega: int = 64,
    n_streams: int = 30,
    use_ant: Optional[List[int]] = None
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Select CSI streams globally from antenna pool based on q(h) metric and Q-threshold.

    Procedure:
    1. Select antennas: if use_ant is None, use all 3; else restrict to indices in use_ant
    2. Flatten selected antennas into global pool (N, n_ant * 30)
    3. Compute q(h) for all streams in pool
    4. Sort globally descending, take top n_streams
    5. Among top-n_streams: compute normalized Q(h_i) = q(h_i) / Σq
    6. Keep only streams where Q(h_i) >= 1/n_streams (Q-threshold)
    7. If none pass threshold, keep single stream with highest q (fallback)

    Args:
        amplitude_3d: Amplitude array, shape (N, 3, 30)
        omega: Window half-width for moving variance
        n_streams: Target number of top streams to consider (before Q-threshold)
        use_ant: List of antenna indices to use; None = all 3 antennas [0, 1, 2]

    Returns:
        (H_S, stats)
        - H_S: Selected streams, shape (N, n_selected)
        - stats: dict with selected indices, q values, antennas used, Q-threshold results
    """
    N, n_antenna, n_subcarrier = amplitude_3d.shape

    if use_ant is None:
        use_ant = list(range(n_antenna))

    use_ant = sorted(list(set(use_ant)))  # deduplicate and sort

    # Flatten selected antennas into a single stream pool
    stream_list = []
    stream_origins = []  # track (antenna_idx, subcarrier_idx) for each stream

    for a in use_ant:
        amp_a = amplitude_3d[:, a, :]  # (N, 30)
        for s in range(n_subcarrier):
            stream_list.append(amp_a[:, s])
            stream_origins.append((a, s))

    H_flat = np.stack(stream_list, axis=1).astype(np.float32)
    n_candidate_streams = H_flat.shape[1]

    # Compute q(h) for all streams
    q_values = np.zeros(n_candidate_streams, dtype=np.float32)
    for i in range(n_candidate_streams):
        q_values[i] = _compute_q(H_flat[:, i], omega)

    # Sort globally descending, take top n_streams
    n_top = min(n_streams, n_candidate_streams)
    top_indices = np.argsort(-q_values)[:n_top]
    top_q_values = q_values[top_indices]

    # Compute normalized Q for top streams
    sum_top_q = np.sum(top_q_values) + 1e-10
    Q_normalized = top_q_values / sum_top_q

    # Q-threshold: keep where Q(h_i) >= 1/n_top
    Q_threshold = 1.0 / max(n_top, 1)
    selected_mask = Q_normalized >= Q_threshold

    if np.sum(selected_mask) == 0:
        # Fallback: keep single stream with highest q among top-n_streams
        best_idx_local = np.argmax(top_q_values)
        selected_mask = np.zeros(n_top, dtype=bool)
        selected_mask[best_idx_local] = True

    selected_indices_local = np.where(selected_mask)[0]
    selected_indices_global = top_indices[selected_indices_local]

    # Extract selected streams
    H_S = H_flat[:, selected_indices_global].astype(np.float32)

    stats = {
        "antennas_used": use_ant,
        "candidate_stream_count": int(n_candidate_streams),
        "top_n_considered": int(n_top),
        "q_threshold": float(Q_threshold),
        "selected_stream_count": int(len(selected_indices_global)),
        "selected_stream_indices": selected_indices_global.tolist(),
        "selected_stream_origins": [stream_origins[i] for i in selected_indices_global],
        "q_values_top": top_q_values.tolist(),
        "Q_values_normalized": Q_normalized.tolist(),
    }

    return H_S, stats


def select_pcs(
    H_S: np.ndarray,
    omega: int = 30,
    eigenvalue_threshold: Optional[float] = None,
    max_pcs: int = 3
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Select PCs from selected streams using PCA and q metric.

    Procedure:
    1. PCA on H_S: R = cov(H_S.T), eigendecomposition
    2. Compute PCs: P = H_S @ V (project onto eigenvectors)
    3. Select candidate PCs by eigenvalue threshold
    4. Compute q(p) for each candidate PC
    5. Filter: Q(p) >= 1/(n_c - 1)
    6. If none match, select PC with highest q
    7. Limit to maximum max_pcs components

    Args:
        H_S: Selected streams, shape (N, n_selected)
        omega: Window half-width for q metric
        eigenvalue_threshold: Eigenvalue threshold (None = max(D) * 1e-3)
        max_pcs: Maximum number of PCs to select (default = 3)

    Returns:
        (selected_pcs, stats)
        - selected_pcs: Selected PC columns, shape (N, n_selected_pcs)
        - stats: dict with counts and indices
    """
    N, n_selected = H_S.shape

    if n_selected < 1:
        return H_S, {
            "selected_pc_count": 0,
            "selected_pc_indices": [],
            "candidate_pc_count": 0,
            "max_pcs_limit": max_pcs,
        }

    cov_matrix = np.cov(H_S.T)
    if cov_matrix.ndim == 0:
        cov_matrix = cov_matrix.reshape(1, 1)

    eigenvalues, eigenvectors = np.linalg.eigh(cov_matrix)

    if eigenvalue_threshold is None:
        eigenvalue_threshold = float(np.max(eigenvalues)) * 1e-3

    candidate_mask = eigenvalues >= eigenvalue_threshold
    candidate_indices = np.where(candidate_mask)[0]

    if len(candidate_indices) == 0:
        candidate_indices = np.array([np.argmax(eigenvalues)])

    P_candidates = H_S @ eigenvectors[:, candidate_indices]

    q_pc_values = np.zeros(len(candidate_indices), dtype=np.float32)
    for idx, pc_idx in enumerate(candidate_indices):
        q_pc_values[idx] = _compute_q(P_candidates[:, idx], omega)

    n_c = len(candidate_indices)
    Q_threshold = 1.0 / max(n_c - 1, 1)

    Q_normalized_pc = q_pc_values / (np.sum(q_pc_values) + 1e-10)
    selected_mask_pc = Q_normalized_pc >= Q_threshold

    if np.sum(selected_mask_pc) == 0:
        best_pc_idx = np.argmax(q_pc_values)
        selected_mask_pc = np.zeros(len(candidate_indices), dtype=bool)
        selected_mask_pc[best_pc_idx] = True

    selected_pc_indices_local = np.where(selected_mask_pc)[0]
    selected_pc_indices_global = candidate_indices[selected_pc_indices_local]

    # Limit to maximum max_pcs components
    if len(selected_pc_indices_global) > max_pcs:
        # Keep top max_pcs by q value
        top_indices = np.argsort(-q_pc_values[selected_pc_indices_local])[:max_pcs]
        selected_pc_indices_global = selected_pc_indices_global[top_indices]

    selected_pcs = H_S @ eigenvectors[:, selected_pc_indices_global]

    stats = {
        "candidate_pc_count": int(len(candidate_indices)),
        "selected_pc_count": int(len(selected_pc_indices_global)),
        "selected_pc_indices": selected_pc_indices_global.tolist(),
        "max_pcs_limit": max_pcs,
    }

    return selected_pcs.astype(np.float32), stats


def preprocess_segment(
    csi_raw: np.ndarray,
    timestamps_raw: np.ndarray,
    fs_hz: float = 320.0,
    tolerance_ms: float = 2.0,
    max_interp_gap_steps: int = 64,
    low_hz: float = 0.5,
    high_hz: float = 150.0,
    filter_order: int = 4,
    omega: int = 64,
    n_streams: int = 30,
    use_ant: Optional[List[int]] = None,
    eigenvalue_threshold: Optional[float] = None,
    window_sec: float = 2.5,
    max_pcs: int = 3
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Complete preprocessing for a single segment: resample → bandpass → stream select → PCA → sum → normalize.

    Args:
        csi_raw: Raw segment CSI, shape (n_raw, 3, 30)
        timestamps_raw: Raw segment timestamps, shape (n_raw,)
        fs_hz: Target sampling frequency
        tolerance_ms: Resampling tolerance
        max_interp_gap_steps: Max interpolation steps
        low_hz: Bandpass low cutoff
        high_hz: Bandpass high cutoff
        filter_order: Bandpass filter order
        omega: Window half-width for q metric
        n_streams: Target number of top streams to select globally
        use_ant: List of antenna indices to use; None = all 3
        eigenvalue_threshold: PCA eigenvalue threshold
        window_sec: Window duration (for fixed length resampled signal)
        max_pcs: Maximum number of PCs to select (default = 3)

    Returns:
        (representative_signal, stats)
        - representative_signal: Normalized 1D signal, shape (fixed_window_samples,)
        - stats: dict with metrics from all stages
    """
    amp, ts = extract_amplitude(csi_raw, timestamps_raw)
    amp_r, resample_stats = resample_signal(ts, amp, fs_hz, tolerance_ms, max_interp_gap_steps)

    fixed_window_samples = int(fs_hz * window_sec)
    if amp_r.shape[0] > fixed_window_samples:
        amp_r = amp_r[:fixed_window_samples]
    elif amp_r.shape[0] < fixed_window_samples:
        pad_len = fixed_window_samples - amp_r.shape[0]
        amp_r = np.pad(amp_r, ((0, pad_len), (0, 0), (0, 0)), mode='edge')

    amp_f = bandpass_filter(amp_r, fs_hz, low_hz, high_hz, filter_order)

    H_S, stream_stats = select_streams(amp_f, omega, n_streams, use_ant)
    pcs, pc_stats = select_pcs(H_S, omega, eigenvalue_threshold, max_pcs)

    rep_signal = pcs.sum(axis=1).astype(np.float32)

    # Normalize after summation
    signal_mean = np.mean(rep_signal)
    signal_std = np.std(rep_signal)
    if signal_std > 1e-10:
        rep_signal = (rep_signal - signal_mean) / signal_std
    else:
        rep_signal = rep_signal - signal_mean

    all_stats = {
        **resample_stats,
        **stream_stats,
        **pc_stats,
    }

    return rep_signal, all_stats


def preprocess_csi(
    csi: np.ndarray,
    timestamps: np.ndarray,
    fs_hz: float = 320.0,
    tolerance_ms: float = 2.0,
    omega: int = 30,
    n_streams: int = 30,
    use_ant: Optional[List[int]] = None,
    eigenvalue_threshold: Optional[float] = None,
    max_pcs: int = 3
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Complete preprocessing pipeline: amplitude → resample → select streams → select PCs → sum.

    Per spec: final output should be the summed PCs (single signal), not multi-column matrix.

    Args:
        csi: Complex CSI, shape (N, 3, 30)
        timestamps: Timestamps in microseconds, shape (N,)
        fs_hz: Target sampling frequency
        tolerance_ms: Resampling tolerance
        omega: Window half-width for q metric
        n_streams: Target number of top streams to select globally (was top_l)
        use_ant: List of antenna indices to use; None = all 3
        eigenvalue_threshold: PCA eigenvalue threshold
        max_pcs: Maximum number of PCs to select (default = 3)

    Returns:
        (summed_signal, all_stats)
        - summed_signal: Summed and normalized selected PCs, shape (N_resampled,)
        - all_stats: Unified stats dict from all stages
    """
    amp, ts = extract_amplitude(csi, timestamps)
    amp_r, resample_stats = resample_signal(ts, amp, fs_hz, tolerance_ms)
    H_S, stream_stats = select_streams(amp_r, omega, n_streams, use_ant)
    pcs, pc_stats = select_pcs(H_S, omega, eigenvalue_threshold, max_pcs)

    # Sum all selected PCs to form the representative signal
    summed_signal = pcs.sum(axis=1).astype(np.float32)

    # Normalize after summation
    signal_mean = np.mean(summed_signal)
    signal_std = np.std(summed_signal)
    if signal_std > 1e-10:
        summed_signal = (summed_signal - signal_mean) / signal_std
    else:
        summed_signal = summed_signal - signal_mean

    all_stats = {
        **resample_stats,
        **stream_stats,
        **pc_stats,
    }

    return summed_signal, all_stats
