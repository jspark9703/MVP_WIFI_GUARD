"""Standard fixed-denominator, energy-normalized sample ACF."""
import numpy as np

def compute_standard_acf(
    signal: np.ndarray, fs_hz: float, max_lag_seconds: float = 0.4
) -> tuple[np.ndarray, np.ndarray]:
    """Return physical lags (seconds) and the 1-D normalized sample ACF.

    Input must be a finite, real, uniformly sampled representative signal.
    With x = signal - mean(signal), r[k] = dot(x[k:], x[:N-k]) /
    dot(x, x). This is the fixed-N (unadjusted/biased) estimator, including
    lag zero. No map resizing, percentile scaling or clipping is applied.
    The maximum lag is floored to a sample and capped at N-1. Constant
    signals have undefined normalized ACF and return NaNs, including r[0].
    """
    from scipy.signal import correlate

    values = np.asarray(signal)
    if values.ndim != 1 or values.size < 2 or np.iscomplexobj(values):
        raise ValueError("signal must be a real 1-D array with at least two samples")
    values = values.astype(np.float64)
    if not np.all(np.isfinite(values)):
        raise ValueError("signal must contain only finite values")
    if not np.isfinite(fs_hz) or fs_hz <= 0:
        raise ValueError("fs_hz must be finite and positive")
    if not np.isfinite(max_lag_seconds) or max_lag_seconds < 0:
        raise ValueError("max_lag_seconds must be finite and nonnegative")
    max_lag = int(np.floor(min(max_lag_seconds, (values.size - 1) / fs_hz) * fs_hz))
    max_lag = min(max_lag, values.size - 1)
    lags = np.arange(max_lag + 1, dtype=np.float64) / fs_hz
    # Scaling before centering avoids overflow for large finite inputs;
    # the normalized ACF is invariant to this common amplitude scale.
    magnitude = float(np.max(np.abs(values)))
    if magnitude:
        values = values / magnitude
    values = values - np.mean(values)
    energy = float(np.dot(values, values))
    if energy == 0.0:
        return lags, np.full(lags.shape, np.nan, dtype=np.float64)
    covariance = correlate(values, values, mode="full", method="auto")
    acf = covariance[values.size - 1 : values.size + max_lag] / energy
    acf[0] = 1.0
    return lags, acf
