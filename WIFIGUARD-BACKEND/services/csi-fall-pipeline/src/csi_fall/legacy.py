"""Original project PCA, CWT/S3 and legacy ACF calculations."""

from __future__ import annotations

import math

from typing import Any

import numpy as np

DEFAULT_CONFIG: dict[str, Any] = {
    "window_seconds": 3.0,
    "stride_seconds": 0.25,
    "raw_csi_pairs": 245,
    "drop_pair_indices": [121, 122],
    "target_subcarriers": 30,
    "omega": 30,
    "moving_variance_radius_seconds": 0.4,
    "freq_min_hz": 1.0,
    "freq_max_hz": 170.0,
    "image_size": 224,
    "th_scmax": 1.0,
    "kappa": 1.0,
    "carrier_hz": 2.4e9,
    "acf_lag_seconds": 0.4,
    "acf_lag_output_bins": 128,
    "acf_time_bins": 64,
    "acf_clip_percentile": 99.5,
}

def input_pair_indices(raw_pair_count: int, dropped: list[int], target_count: int) -> np.ndarray:
    candidates = np.asarray(
        [index for index in range(raw_pair_count) if index not in set(dropped)],
        dtype=np.int32,
    )
    positions = np.round(np.linspace(0, len(candidates) - 1, target_count)).astype(np.int32)
    selected = candidates[positions]
    if len(np.unique(selected)) != target_count:
        raise ValueError("Subcarrier selection produced duplicate indices")
    return selected

def parse_csi_amplitude(text: str, selected_pairs: np.ndarray, raw_pair_count: int) -> np.ndarray:
    values = np.fromstring(text.strip().strip("[]"), sep=",", dtype=np.float32)
    expected = raw_pair_count * 2
    if len(values) != expected:
        raise ValueError(f"Expected {expected} CSI integers, got {len(values)}")
    chosen = values.reshape(raw_pair_count, 2)[selected_pairs]
    return np.hypot(chosen[:, 0], chosen[:, 1]).astype(np.float32)

def native_window_starts(
    grid_count: int, fs_hz: float, window_seconds: float, stride_seconds: float
) -> np.ndarray:
    window_samples = int(round(window_seconds * fs_hz))
    max_start = grid_count - window_samples
    if max_start < 0:
        return np.empty(0, dtype=np.int64)
    count = int(math.floor((max_start / fs_hz) / stride_seconds + 1e-9)) + 1
    starts = np.unique(
        np.round(np.arange(count, dtype=np.float64) * stride_seconds * fs_hz).astype(
            np.int64
        )
    )
    return starts[starts <= max_start]

def moving_mean(values: np.ndarray, radius: int) -> np.ndarray:
    radius = max(0, int(radius))
    if radius == 0:
        return values.astype(np.float64)
    kernel = np.ones(2 * radius + 1, dtype=np.float64)
    counts = np.convolve(np.ones(len(values), dtype=np.float64), kernel, mode="same")
    sums = np.convolve(values.astype(np.float64), kernel, mode="same")
    return sums / np.maximum(counts, 1.0)

def moving_variance(values: np.ndarray, radius: int) -> np.ndarray:
    values = values.astype(np.float64)
    mean = moving_mean(values, radius)
    return np.maximum(moving_mean(values * values, radius) - mean * mean, 0.0)

def q_metric(values: np.ndarray, radius: int, eps: float = 1e-8) -> float:
    variance = moving_variance(np.abs(values), radius)
    return float(np.max(variance) / max(float(np.mean(variance)), eps))

def select_streams(amplitude: np.ndarray, omega: int, radius: int) -> np.ndarray:
    q_values = np.asarray(
        [q_metric(amplitude[:, index], radius) for index in range(amplitude.shape[1])]
    )
    top = np.argsort(q_values)[::-1][: min(max(1, omega), len(q_values))]
    normalized = q_values[top] / max(float(np.sum(q_values[top])), 1e-8)
    selected = top[normalized >= (1.0 / max(1, len(top)))]
    return (selected if len(selected) else top[:1]).astype(np.int32)

def select_pc_signal(
    amplitude: np.ndarray, selected_streams: np.ndarray, radius: int
) -> np.ndarray:
    values = amplitude[:, selected_streams].astype(np.float64)
    centered = values - np.mean(values, axis=0, keepdims=True)
    if centered.shape[1] == 1:
        return centered[:, 0].astype(np.float32)
    _, singular_values, vt = np.linalg.svd(centered, full_matrices=False)
    eigenvalues = singular_values * singular_values / max(centered.shape[0] - 1, 1)
    normalized = eigenvalues / max(float(np.sum(eigenvalues)), 1e-12)
    candidates = np.where(normalized > (1.0 / centered.shape[1]))[0]
    if len(candidates) == 0:
        candidates = np.asarray([int(np.argmax(normalized))])
    pcs = centered @ vt.T
    if len(candidates) == 1:
        selected = candidates
    else:
        q_values = np.asarray([q_metric(pcs[:, index], radius) for index in candidates])
        q_normalized = q_values / max(float(np.sum(q_values)), 1e-8)
        selected = candidates[q_normalized >= (1.0 / max(len(candidates) - 1, 1))]
        if len(selected) == 0:
            selected = np.asarray([candidates[int(np.argmax(q_values))]])
    return np.sum(pcs[:, selected], axis=1).astype(np.float32)

def resize_time_axis(matrix: np.ndarray, output_bins: int) -> np.ndarray:
    if matrix.shape[1] == output_bins:
        return matrix.astype(np.float32)
    source = np.linspace(0.0, 1.0, matrix.shape[1], dtype=np.float64)
    target = np.linspace(0.0, 1.0, output_bins, dtype=np.float64)
    output = np.empty((matrix.shape[0], output_bins), dtype=np.float32)
    for row_index in range(matrix.shape[0]):
        output[row_index] = np.interp(target, source, matrix[row_index]).astype(np.float32)
    return output

def general_denoise(s0: np.ndarray, signal_q: float, threshold_max: float) -> np.ndarray:
    if signal_q <= 1.0 or not math.isfinite(signal_q):
        threshold = threshold_max
    else:
        log_q = math.log10(signal_q)
        threshold = threshold_max if log_q <= 0 else min(1.0 / log_q, threshold_max)
    output = s0.copy()
    output[output < (np.mean(output, axis=1) * threshold)[:, None]] = 0.0
    return output

def vertical_denoise(s1: np.ndarray, freqs: np.ndarray, hz_step: float) -> np.ndarray:
    output = s1.copy()
    descending = np.argsort(freqs)[::-1]
    maximum = 0.0
    for column in range(output.shape[1]):
        for row in descending:
            if output[row, column] <= 0:
                continue
            frequency = float(freqs[row])
            if maximum <= 0.0:
                maximum = frequency
                break
            if frequency <= maximum or (frequency - maximum) <= hz_step:
                maximum = max(maximum, frequency)
                break
            output[row, column] = 0.0
    return output

def horizontal_denoise(
    s2: np.ndarray, freqs: np.ndarray, fs_hz: float, carrier_hz: float, kappa: float
) -> np.ndarray:
    output = s2.copy()
    wavelength = 299_792_458.0 / carrier_hz
    if len(freqs) == 1:
        bandwidths = np.ones_like(freqs)
    else:
        bandwidths = np.gradient(freqs)
        bandwidths = np.maximum(
            np.abs(bandwidths), np.min(np.abs(bandwidths[np.nonzero(bandwidths)]))
        )
    minimum_runs = np.maximum(
        1,
        np.ceil(
            bandwidths * wavelength / max(kappa * 9.80665 * (1.0 / fs_hz), 1e-12)
        ).astype(int),
    )
    for row in range(output.shape[0]):
        nonzero = output[row] > 0
        index = 0
        while index < len(nonzero):
            if not nonzero[index]:
                index += 1
                continue
            start = index
            while index < len(nonzero) and nonzero[index]:
                index += 1
            if index - start < int(minimum_runs[row]):
                output[row, start:index] = 0.0
    return output

def compute_s3(signal: np.ndarray, fs_hz: float, config: dict[str, Any]) -> np.ndarray:
    from ssqueezepy import cwt
    from ssqueezepy.experimental import freq_to_scale
    from ssqueezepy.wavelets import Wavelet

    image_size = int(config["image_size"])
    maximum_frequency = min(float(config["freq_max_hz"]), fs_hz / 2.0 - 1e-6)
    low_to_high = np.linspace(
        float(config["freq_min_hz"]), maximum_frequency, image_size, dtype=np.float64
    )
    freqs = low_to_high[::-1]
    wavelet = Wavelet("gmw", N=len(signal))
    # Keep positive strides so ssqueezepy's optional PyTorch/CUDA backend can
    # convert the NumPy array without copying errors.
    scales = freq_to_scale(low_to_high, wavelet, N=len(signal), fs=fs_hz)[::-1].copy()
    coefficients, _ = cwt(
        signal.astype(np.float64),
        wavelet=wavelet,
        scales=scales,
        fs=fs_hz,
        l1_norm=True,
        astensor=False,
    )
    s0 = np.abs(coefficients).astype(np.float32)
    maximum = float(np.max(s0))
    if maximum > 0:
        s0 /= maximum
    signal_q = q_metric(signal, max(1, int(round(0.4 * fs_hz))))
    s1 = general_denoise(s0, signal_q, float(config["th_scmax"]))
    s2 = vertical_denoise(s1, freqs, 170.0 / fs_hz)
    s3 = horizontal_denoise(
        s2,
        freqs,
        fs_hz,
        float(config["carrier_hz"]),
        float(config["kappa"]),
    )
    return resize_time_axis(s3, image_size).astype(np.float32)

def compute_s3_batch(
    signals: np.ndarray, fs_hz: float, config: dict[str, Any]
) -> np.ndarray:
    """Compute the same S3 transform for a batch of equal-length signals.

    ssqueezepy's CWT supports a leading batch dimension.  The normalization
    and three denoising stages remain per-signal, matching ``compute_s3``.
    """
    from ssqueezepy import cwt
    from ssqueezepy.experimental import freq_to_scale
    from ssqueezepy.wavelets import Wavelet

    values = np.asarray(signals, dtype=np.float32)
    if values.ndim != 2 or values.shape[0] == 0:
        raise ValueError(f"Expected a non-empty BxT signal batch, got {values.shape}")
    image_size = int(config["image_size"])
    maximum_frequency = min(float(config["freq_max_hz"]), fs_hz / 2.0 - 1e-6)
    low_to_high = np.linspace(
        float(config["freq_min_hz"]), maximum_frequency, image_size, dtype=np.float64
    )
    freqs = low_to_high[::-1]
    wavelet = Wavelet("gmw", N=values.shape[1])
    scales = freq_to_scale(
        low_to_high, wavelet, N=values.shape[1], fs=fs_hz
    )[::-1].copy()
    coefficients, _ = cwt(
        values.astype(np.float64),
        wavelet=wavelet,
        scales=scales,
        fs=fs_hz,
        l1_norm=True,
        astensor=False,
    )
    outputs = np.empty((len(values), image_size, image_size), dtype=np.float32)
    for index, signal in enumerate(values):
        s0 = np.abs(coefficients[index]).astype(np.float32)
        maximum = float(np.max(s0))
        if maximum > 0:
            s0 /= maximum
        signal_q = q_metric(signal, max(1, int(round(0.4 * fs_hz))))
        s1 = general_denoise(s0, signal_q, float(config["th_scmax"]))
        s2 = vertical_denoise(s1, freqs, 170.0 / fs_hz)
        s3 = horizontal_denoise(
            s2,
            freqs,
            fs_hz,
            float(config["carrier_hz"]),
            float(config["kappa"]),
        )
        outputs[index] = resize_time_axis(s3, image_size)
    return outputs

def percentile_scale(values: np.ndarray, percentile: float) -> float:
    finite = np.asarray(values[np.isfinite(values)], dtype=np.float32)
    scale = float(np.percentile(np.abs(finite), percentile)) if finite.size else 1.0
    if not np.isfinite(scale) or scale < 1e-6:
        scale = float(np.max(np.abs(finite))) if finite.size else 1.0
    return scale if np.isfinite(scale) and scale >= 1e-6 else 1.0

def compute_acf(signal: np.ndarray, fs_hz: float, config: dict[str, Any]) -> np.ndarray:
    values = signal.astype(np.float32)
    scale = float(np.std(values))
    normalized = (values - float(np.mean(values))) / (scale if scale >= 1e-6 else 1.0)
    lag_steps = max(1, int(round(float(config["acf_lag_seconds"]) * fs_hz)))
    current = normalized[lag_steps:]
    raw = np.empty((lag_steps, len(normalized) - lag_steps), dtype=np.float32)
    for lag in range(1, lag_steps + 1):
        raw[lag - 1] = current * normalized[lag_steps - lag : len(normalized) - lag]
    resized_time = resize_time_axis(raw, int(config["acf_time_bins"]))
    clipped = np.clip(
        resized_time / percentile_scale(resized_time, float(config["acf_clip_percentile"])),
        -1.0,
        1.0,
    ).astype(np.float32)
    source = np.linspace(0.0, 1.0, clipped.shape[0], dtype=np.float64)
    target = np.linspace(
        0.0, 1.0, int(config["acf_lag_output_bins"]), dtype=np.float64
    )
    output = np.empty(
        (1, int(config["acf_lag_output_bins"]), clipped.shape[1]), dtype=np.float32
    )
    for time_index in range(clipped.shape[1]):
        output[0, :, time_index] = np.interp(
            target, source, clipped[:, time_index]
        ).astype(np.float32)
    return output

def compute_feature_pair(
    window: np.ndarray, fs_hz: float, config: dict[str, Any]
) -> tuple[np.ndarray, np.ndarray]:
    radius = max(
        1, int(round(float(config["moving_variance_radius_seconds"]) * fs_hz))
    )
    selected = select_streams(window, int(config["omega"]), radius)
    signal = select_pc_signal(window, selected, radius)
    return compute_s3(signal, fs_hz, config), compute_acf(signal, fs_hz, config)
