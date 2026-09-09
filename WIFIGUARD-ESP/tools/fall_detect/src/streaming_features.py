"""
Real-time CSI signal processing for fall detection MVP.

Adapts the amfall-based preprocessing pipeline (preprocessing.py) to work with
ESP32-C5 live 2D CSI data (N, n_subcarriers), rather than the offline 3D dataset
(N, 3, 30) that preprocessing.py expects.

All functions operate on resampled, uniformly-spaced CSI windows.
"""

from dataclasses import dataclass
from typing import Tuple

import numpy as np
from scipy import signal

from .preprocessing import _moving_variance, _compute_q, resample_signal, bandpass_filter


@dataclass
class FinalSignalResult:
    """Complete per-tick pipeline output."""
    final_signal: np.ndarray  # (N,) z-scored signal
    moving_variance: np.ndarray  # (N,) moving variance of final_signal
    mv_current: float  # Last value of moving_variance (scalar for state machine)
    selected_indices: np.ndarray  # (n_selected,) indices of selected subcarriers
    q_values: np.ndarray  # (n_selected,) q-values of selected subcarriers
    resample_stats: dict  # From resample_signal
    window_sample_count: int
    window_duration_s: float
    band_energy: float | None = None  # Welch PSD band energy (only set if requested)


def select_top_subcarriers_2d(
    amp_2d: np.ndarray,
    omega: int,
    n_streams: int = 10
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Select top N subcarriers by q-value from a 2D amplitude array.

    Adapted from preprocessing.select_streams for a single-antenna 2D array.
    No Q-threshold pruning (MVP simplification).

    Args:
        amp_2d: Amplitude array, shape (N, n_subcarriers).
        omega: Moving-variance window half-width.
        n_streams: Number of top subcarriers to select.

    Returns:
        (selected_indices, q_values) where:
        - selected_indices: (n_selected,) int array of subcarrier indices.
        - q_values: (n_selected,) float array of corresponding q-values.
    """
    n_samples, n_subcarriers = amp_2d.shape
    n_select = min(n_streams, n_subcarriers)

    q_values = np.zeros(n_subcarriers, dtype=np.float32)
    for i in range(n_subcarriers):
        q_values[i] = _compute_q(amp_2d[:, i], omega)

    top_indices = np.argsort(-q_values)[:n_select]
    top_q_values = q_values[top_indices]

    return top_indices, top_q_values


def safe_bandpass(
    signal_arr: np.ndarray,
    fs_hz: float,
    low_hz: float = 2.0,
    high_hz: float = 50.0,
    order: int = 4,
    nyquist_margin_hz: float = 1.0
) -> Tuple[np.ndarray, dict]:
    """
    Apply bandpass filter with Nyquist-edge safety.

    Clamps high_hz to avoid scipy.signal.butter errors when Wn >= 1.
    Skips filtering (returns input unfiltered) if the signal is too short.

    Args:
        signal_arr: 1D or 2D signal. If 2D, filters along axis 0.
        fs_hz: Sampling frequency (Hz).
        low_hz: Low cutoff frequency.
        high_hz: High cutoff frequency (clamped if necessary).
        order: Butterworth filter order.
        nyquist_margin_hz: Safety margin below Nyquist.

    Returns:
        (filtered_signal, diagnostics) where diagnostics contains:
        - filter_skipped: bool, True if filtering was skipped due to signal length.
        - high_hz_clamped: float, actual high_hz used.
    """
    nyquist_hz = fs_hz / 2.0
    high_hz_actual = min(high_hz, nyquist_hz - nyquist_margin_hz)

    min_signal_len = 2 * max(order, 2) + 1
    if len(signal_arr) < min_signal_len:
        return signal_arr.astype(np.float32), {
            "filter_skipped": True,
            "high_hz_clamped": high_hz_actual,
            "reason": "signal_too_short"
        }

    try:
        filtered = bandpass_filter(
            signal_arr,
            fs_hz=fs_hz,
            low_hz=low_hz,
            high_hz=high_hz_actual,
            order=order
        )
        return filtered, {
            "filter_skipped": False,
            "high_hz_clamped": high_hz_actual
        }
    except Exception as e:
        return signal_arr.astype(np.float32), {
            "filter_skipped": True,
            "high_hz_clamped": high_hz_actual,
            "error": str(e)
        }


def sum_and_normalize(
    amp_2d: np.ndarray,
    indices: np.ndarray
) -> np.ndarray:
    """
    Sum selected subcarrier columns and z-score normalize.

    Args:
        amp_2d: Amplitude array, shape (N, n_subcarriers).
        indices: Indices of subcarriers to sum, shape (n_selected,).

    Returns:
        Z-scored combined signal, shape (N,).
    """
    combined = amp_2d[:, indices].sum(axis=1).astype(np.float32)
    mean_val = np.mean(combined)
    std_val = np.std(combined)
    if std_val > 1e-8:
        return (combined - mean_val) / std_val
    else:
        return combined - mean_val


def compute_band_energy_welch(
    signal_1d: np.ndarray,
    fs_hz: float,
    low_hz: float,
    high_hz: float,
) -> float:
    """
    Welch PSD band energy: integrates the power spectral density over [low_hz, high_hz].

    Uses a single segment (nperseg=len(signal_1d), no multi-segment averaging) --
    prioritizes responsiveness over variance reduction, since averaging multiple
    segments would need a much longer window than the ~6s wander window this is
    typically called with.
    """
    n = len(signal_1d)
    if n < 8:
        return 0.0
    freqs, psd = signal.welch(signal_1d, fs=fs_hz, nperseg=n)
    band_mask = (freqs >= low_hz) & (freqs <= high_hz)
    if not np.any(band_mask):
        return 0.0
    # numpy 2.x renamed trapz -> trapezoid (trapz removed later); requirements.txt still pins numpy 1.26
    _trapezoid = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    return float(_trapezoid(psd[band_mask], freqs[band_mask]))


def compute_final_signal(
    timestamps_us: np.ndarray,
    amp_2d: np.ndarray,
    window_sec: float = 3.0,
    stride_sec: float = 0.5,
    fs_hz: float = 100.0,
    omega: int = 25,
    n_streams: int = 10,
    bandpass_low: float = 2.0,
    bandpass_high: float = 50.0,
    bandpass_order: int = 4,
    compute_band_energy: bool = False,
    energy_band_low: float | None = None,
    energy_band_high: float | None = None,
) -> FinalSignalResult | None:
    """
    Complete per-tick pipeline: resample → bandpass → select → sum → moving variance.

    Args:
        timestamps_us: Timestamps in microseconds, shape (N,).
        amp_2d: Raw amplitude array, shape (N, n_subcarriers).
        window_sec: Window duration (seconds).
        stride_sec: Stride/update interval (for duration calculation only).
        fs_hz: Target resample frequency (Hz).
        omega: Moving-variance half-width.
        n_streams: Number of subcarriers to select.
        bandpass_low, bandpass_high: Bandpass cutoff frequencies (pre-filter,
            applied before subcarrier selection/summing/normalizing).
        bandpass_order: Bandpass filter order.
        compute_band_energy: If True, also compute a Welch PSD band-energy value
            from final_signal, for presence detection's wander signal.
        energy_band_low, energy_band_high: Band used for the Welch energy
            integration. Defaults to bandpass_low/bandpass_high if not given,
            but should normally be a NARROWER band nested inside
            [bandpass_low, bandpass_high] -- if they're the same band, the
            pre-filter step already confines final_signal's power almost
            entirely to that band before normalization, making the
            post-normalization Welch measurement in the same band close to
            input-invariant (near-constant regardless of whether the input had
            genuine narrowband structure or was just noise that survived the
            filter). Pre-filtering wider than the measurement band preserves
            real discriminative power.

    Returns:
        FinalSignalResult on success, None if insufficient data.
    """
    n_samples, n_subcarriers = amp_2d.shape
    if n_samples < 10:
        return None

    # Resample to regular grid
    amp_resampled, resample_stats = resample_signal(
        timestamps_us, amp_2d, fs_hz=fs_hz
    )

    # Bandpass filter (per-column)
    amp_filtered, bp_diagnostics = safe_bandpass(
        amp_resampled, fs_hz, bandpass_low, bandpass_high, bandpass_order
    )

    # Select top subcarriers
    selected_indices, q_values = select_top_subcarriers_2d(
        amp_filtered, omega, n_streams
    )

    # Sum and normalize
    final_signal = sum_and_normalize(amp_filtered, selected_indices)

    # Moving variance of final signal
    mv = _moving_variance(final_signal, omega)
    mv_current = float(mv[-1])

    band_energy = None
    if compute_band_energy:
        e_low = energy_band_low if energy_band_low is not None else bandpass_low
        e_high = energy_band_high if energy_band_high is not None else bandpass_high
        band_energy = compute_band_energy_welch(final_signal, fs_hz, e_low, e_high)

    window_duration_s = (timestamps_us[-1] - timestamps_us[0]) / 1e6

    return FinalSignalResult(
        final_signal=final_signal,
        moving_variance=mv,
        mv_current=mv_current,
        selected_indices=selected_indices,
        q_values=q_values,
        resample_stats=resample_stats,
        window_sample_count=n_samples,
        window_duration_s=window_duration_s,
        band_energy=band_energy,
    )
