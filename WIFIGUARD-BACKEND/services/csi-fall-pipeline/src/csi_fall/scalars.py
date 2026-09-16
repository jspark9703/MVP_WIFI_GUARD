"""Standard ACF and the ten predeclared scalar descriptors (no fitted state)."""
import numpy as np
from scipy.signal import find_peaks

from .standard import compute_standard_acf

LAGS = np.linspace(0.0, 0.4, 129, dtype=np.float64)
NAMES = ["decay_1e_sec", "first_zero_sec", "first_min_lag_sec", "first_min_value",
         "repeat_peak_lag_sec", "repeat_peak_prominence", "first_positive_area",
         "negative_fraction", "tail_abs_mean", "total_variation"]


def crossing(lags, curve, threshold):
    hits = np.flatnonzero(curve <= threshold)
    if not len(hits):
        return float("nan")
    j = int(hits[0])
    if j == 0:
        return float(lags[0])
    fraction = (curve[j - 1] - threshold) / (curve[j - 1] - curve[j])
    return float(lags[j - 1] + fraction * (lags[j] - lags[j - 1]))


def describe_acf(curve, lags=LAGS):
    """Missing extrema/crossings stay NaN; never encode them as lag zero.

    Extrema must have prominence >= .02 (fixed before evaluation). The first
    positive lobe area is truncated at the maximum lag if no crossing exists;
    missing first_zero_sec records that censoring. Fraction/tail omit lag zero.
    """
    curve = np.asarray(curve, dtype=np.float64)
    if not np.isfinite(curve).all():
        return np.full(10, np.nan, np.float32)
    decay, zero = crossing(lags, curve, 1 / np.e), crossing(lags, curve, 0)
    minima, _ = find_peaks(-curve, prominence=0.02)
    min_lag = min_value = peak_lag = prominence = float("nan")
    if len(minima):
        first = int(minima[0])
        min_lag, min_value = float(lags[first]), float(curve[first])
        peaks, props = find_peaks(curve, prominence=0.02)
        valid = np.flatnonzero((peaks > first) & (curve[peaks] > 0))
        if len(valid):
            j = int(valid[0])
            peak_lag, prominence = float(lags[peaks[j]]), float(props["prominences"][j])
    if np.isfinite(zero):
        keep = lags < zero
        area = np.trapz(np.r_[curve[keep], 0.0], np.r_[lags[keep], zero])
    else:
        area = np.trapz(np.maximum(curve, 0), lags)
    return np.asarray([decay, zero, min_lag, min_value, peak_lag, prominence, area,
                       np.mean(curve[1:] < 0), np.abs(curve[lags >= 0.3]).mean(),
                       np.abs(np.diff(curve)).sum()], dtype=np.float32)


def extract(signal, fs_hz):
    # One additional native sample brackets 0.4 s at non-integer sample rates.
    native_limit = (np.ceil(0.4 * fs_hz) + 0.01) / fs_hz
    native_lags, acf = compute_standard_acf(signal, fs_hz, native_limit)
    if native_lags[-1] < LAGS[-1]:
        raise ValueError("Signal is too short to cover the common 0.4-second lag grid")
    curve = np.interp(LAGS, native_lags, acf).astype(np.float32)
    scalars = describe_acf(curve)
    return curve, scalars
