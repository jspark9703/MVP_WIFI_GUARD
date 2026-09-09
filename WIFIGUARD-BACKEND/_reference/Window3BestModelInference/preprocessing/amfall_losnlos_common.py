from __future__ import annotations

import csv
import json
import math
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np


CSI_START_COL = 13
RX_COUNT = 3
SUBCARRIERS_PER_RX = 30
DEFAULT_FS_HZ = 320.0
DEFAULT_SEGMENT_SEC = 3.0
FALL_ACTIVITIES = {"A02", "A05"}
HARD_NEGATIVE_ACTIVITIES = {"A03", "A06", "A07", "A08", "A09", "A10", "A11", "A12"}


@dataclass(frozen=True)
class LosNlosMeta:
    env: str
    subject: str
    condition: str
    activity: str
    trial: str

    @property
    def source_id(self) -> str:
        return f"{self.env}_{self.subject}_{self.condition}_{self.activity}_{self.trial}"

    @property
    def binary_label(self) -> str:
        return "fall" if self.activity in FALL_ACTIVITIES else "non_fall"

    @property
    def trial_block(self) -> str:
        trial_num = int(self.trial[1:])
        if trial_num <= 6:
            return "early"
        if trial_num <= 13:
            return "mid"
        return "late"

    @property
    def hard_negative(self) -> bool:
        return self.activity in HARD_NEGATIVE_ACTIVITIES


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def seconds_to_eta(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "unknown"
    seconds_int = int(round(seconds))
    hours, rem = divmod(seconds_int, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


class ProgressLogger:
    def __init__(
        self,
        run_name: str,
        stage: str,
        total: int,
        progress_jsonl: Path,
        log_interval_sec: float = 30.0,
    ) -> None:
        self.run_name = run_name
        self.stage = stage
        self.total = max(0, int(total))
        self.progress_jsonl = progress_jsonl
        self.log_interval_sec = log_interval_sec
        self.start_time = time.monotonic()
        self.last_log_time = 0.0
        self.last_update_time = self.start_time
        self.done = 0
        self.recent_item_times: deque[float] = deque(maxlen=100)
        self.progress_jsonl.parent.mkdir(parents=True, exist_ok=True)

    def update(
        self,
        done: int,
        current_item: str = "",
        metrics: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        delta_done = max(0, done - self.done)
        if delta_done:
            elapsed_delta = max(now - self.last_update_time, 1e-9)
            per_item = elapsed_delta / delta_done
            for _ in range(min(delta_done, 100)):
                self.recent_item_times.append(per_item)
            self.last_update_time = now
        self.done = done
        should_log = force or self.done >= self.total or now - self.last_log_time >= self.log_interval_sec
        if not should_log:
            return
        self.last_log_time = now
        elapsed = now - self.start_time
        rate = self.done / elapsed if elapsed > 0 and self.done > 0 else 0.0
        eta = (elapsed / self.done * (self.total - self.done)) if self.done > 0 else None
        rolling_eta = None
        if self.recent_item_times and self.total >= self.done:
            rolling_eta = float(np.mean(self.recent_item_times)) * (self.total - self.done)

        record = {
            "timestamp": utc_now_iso(),
            "run_name": self.run_name,
            "stage": self.stage,
            "done": self.done,
            "total": self.total,
            "elapsed_sec": elapsed,
            "rate_per_sec": rate,
            "eta_sec": eta,
            "rolling_eta_sec": rolling_eta,
            "current_item": current_item,
            "metrics": metrics or {},
            "warnings": warnings or [],
        }
        with self.progress_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        extra = ""
        if metrics:
            extra = " " + " ".join(f"{key}={value}" for key, value in metrics.items())
        print(
            f"[{record['timestamp']}] stage={self.stage} "
            f"done={self.done}/{self.total} rate={rate:.3f}/s "
            f"eta={seconds_to_eta(eta)} rolling_eta={seconds_to_eta(rolling_eta)}{extra}",
            flush=True,
        )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv_dicts(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def append_csv_dict(path: Path, row: dict[str, Any], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow({name: row.get(name, "") for name in fieldnames})


def discover_losnlos_csvs(dataset_root: Path) -> list[Path]:
    return sorted(path for path in dataset_root.rglob("*.csv") if path.is_file())


def parse_losnlos_metadata(path: Path) -> LosNlosMeta:
    match = re.match(r"^(E\d+)_(S\d+)_(C\d+)_(A\d+)_(T\d+)$", path.stem)
    if not match:
        raise ValueError(f"Unexpected LOS/NLOS filename: {path.name}")
    return LosNlosMeta(*match.groups())


def activity_to_index(activity: str) -> int:
    return int(activity[1:]) - 1


def binary_to_index(label: str) -> int:
    return 1 if label == "fall" else 0


def load_timestamps(csv_path: Path) -> np.ndarray:
    timestamps = np.loadtxt(
        csv_path,
        delimiter=",",
        skiprows=1,
        usecols=(0,),
        dtype=np.float64,
        ndmin=1,
    )
    # Two public LOS/NLOS files contain wrapped timestamp values written near 2^64.
    # Interpret those rows as a wrapped 32-bit microsecond counter so resampling
    # does not try to allocate an enormous time grid.
    huge_mask = timestamps > 1e18
    if np.any(huge_mask):
        timestamps = timestamps.copy()
        timestamps[huge_mask] = timestamps[huge_mask] - float(2**64) + float(2**32)
    return timestamps


def load_rx_amplitude(csv_path: Path, rx: int) -> tuple[np.ndarray, np.ndarray]:
    if not 1 <= rx <= RX_COUNT:
        raise ValueError(f"rx must be 1..{RX_COUNT}, got {rx}")
    timestamps = load_timestamps(csv_path)
    start = CSI_START_COL + (rx - 1) * SUBCARRIERS_PER_RX
    stop = start + SUBCARRIERS_PER_RX
    raw = np.loadtxt(
        csv_path,
        delimiter=",",
        skiprows=1,
        usecols=tuple(range(start, stop)),
        dtype=str,
        ndmin=2,
    )
    normalized = np.char.replace(raw, "+-", "-")
    normalized = np.char.replace(normalized, "i", "j")
    csi = normalized.astype(np.complex64)
    return timestamps, np.abs(csi).astype(np.float32)


def gap_diagnostics(
    timestamps: np.ndarray,
    grid_us: float,
    tolerance_us: float,
    max_interp_gap_steps: int = 64,
) -> dict[str, Any]:
    if len(timestamps) < 2:
        return {
            "row_count": int(len(timestamps)),
            "diff_count": 0,
            "nonpositive_count": 0,
            "irregular_count": 0,
            "missing_steps": 0,
            "positive_diff_count": 0,
        }
    diffs = np.diff(timestamps)
    positive = diffs[diffs > 0]
    nonpositive_count = int(np.sum(diffs <= 0))
    irregular_count = 0
    missing_steps = 0
    for gap in positive:
        steps = max(1, int(round(gap / grid_us)))
        if steps <= max_interp_gap_steps and abs(gap - steps * grid_us) <= tolerance_us:
            missing_steps += max(0, steps - 1)
        else:
            irregular_count += 1
    return {
        "row_count": int(len(timestamps)),
        "diff_count": int(len(diffs)),
        "nonpositive_count": nonpositive_count,
        "irregular_count": irregular_count,
        "missing_steps": int(missing_steps),
        "positive_diff_count": int(len(positive)),
    }


def resample_amplitude(
    timestamps: np.ndarray,
    amplitude: np.ndarray,
    grid_us: float,
    tolerance_us: float,
    max_interp_gap_steps: int = 64,
) -> tuple[np.ndarray, dict[str, int]]:
    if len(amplitude) == 0:
        return amplitude.astype(np.float32), {
            "interp_steps": 0,
            "fallback_steps": 0,
            "irregular_gaps": 0,
            "nonpositive_gaps": 0,
        }

    rows = [amplitude[0].astype(np.float32)]
    interp_steps = 0
    fallback_steps = 0
    irregular_gaps = 0
    nonpositive_gaps = 0
    for idx in range(1, len(amplitude)):
        gap = timestamps[idx] - timestamps[idx - 1]
        if gap <= 0:
            rows.append(amplitude[idx].astype(np.float32))
            fallback_steps += 1
            nonpositive_gaps += 1
            continue

        nearest_steps = max(1, int(round(gap / grid_us)))
        can_reconstruct = (
            nearest_steps <= max_interp_gap_steps
            and abs(gap - nearest_steps * grid_us) <= tolerance_us
        )
        if can_reconstruct:
            for step in range(1, nearest_steps):
                alpha = step / nearest_steps
                interpolated = (1.0 - alpha) * amplitude[idx - 1] + alpha * amplitude[idx]
                rows.append(interpolated.astype(np.float32))
                interp_steps += 1
            rows.append(amplitude[idx].astype(np.float32))
        else:
            rows.append(amplitude[idx].astype(np.float32))
            fallback_steps += 1
            irregular_gaps += 1
    return np.stack(rows, axis=0).astype(np.float32), {
        "interp_steps": interp_steps,
        "fallback_steps": fallback_steps,
        "irregular_gaps": irregular_gaps,
        "nonpositive_gaps": nonpositive_gaps,
    }


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
    mean_sq = moving_mean(values * values, radius)
    return np.maximum(mean_sq - mean * mean, 0.0)


def moving_sum(values: np.ndarray, radius: int) -> np.ndarray:
    radius = max(0, int(radius))
    if radius == 0:
        return values.astype(np.float64)
    kernel = np.ones(2 * radius + 1, dtype=np.float64)
    return np.convolve(values.astype(np.float64), kernel, mode="same")


def q_metric(values: np.ndarray, radius: int, eps: float = 1e-8) -> float:
    variance = moving_variance(np.abs(values), radius)
    return float(np.max(variance) / max(float(np.mean(variance)), eps))


def select_streams(amplitude: np.ndarray, omega: int, w_radius: int) -> tuple[np.ndarray, np.ndarray]:
    q_values = np.asarray([q_metric(amplitude[:, idx], w_radius) for idx in range(amplitude.shape[1])])
    order = np.argsort(q_values)[::-1]
    top = order[: min(max(1, omega), len(order))]
    q_top = q_values[top]
    q_sum = max(float(np.sum(q_top)), 1e-8)
    q_norm = q_top / q_sum
    selected_mask = q_norm >= (1.0 / max(1, len(top)))
    selected = top[selected_mask]
    if len(selected) == 0:
        selected = top[:1]
    return selected.astype(np.int32), q_values


def select_pc_signal(
    amplitude: np.ndarray,
    selected_streams: np.ndarray,
    w_radius: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    x = amplitude[:, selected_streams].astype(np.float64)
    x_centered = x - np.mean(x, axis=0, keepdims=True)
    if x_centered.shape[1] == 1:
        pc = x_centered[:, 0]
        return pc.astype(np.float32), {
            "selected_pc_indices": "0",
            "candidate_pc_count": 1,
            "selected_pc_count": 1,
            "eigenvalues": "1.0",
        }

    _, singular_values, vt = np.linalg.svd(x_centered, full_matrices=False)
    denom = max(x_centered.shape[0] - 1, 1)
    eigenvalues = (singular_values * singular_values) / denom
    eigen_sum = max(float(np.sum(eigenvalues)), 1e-12)
    normalized = eigenvalues / eigen_sum
    threshold = 1.0 / x_centered.shape[1]
    candidate_indices = np.where(normalized > threshold)[0]
    if len(candidate_indices) == 0:
        candidate_indices = np.asarray([int(np.argmax(normalized))])
    pcs = x_centered @ vt.T

    if len(candidate_indices) == 1:
        selected_pc_indices = candidate_indices
    else:
        q_values = np.asarray([q_metric(pcs[:, idx], w_radius) for idx in candidate_indices])
        q_norm = q_values / max(float(np.sum(q_values)), 1e-8)
        pc_threshold = 1.0 / max(len(candidate_indices) - 1, 1)
        selected_pc_indices = candidate_indices[q_norm >= pc_threshold]
        if len(selected_pc_indices) == 0:
            selected_pc_indices = np.asarray([candidate_indices[int(np.argmax(q_values))]])

    signal = np.sum(pcs[:, selected_pc_indices], axis=1)
    return signal.astype(np.float32), {
        "selected_pc_indices": ";".join(str(int(idx)) for idx in selected_pc_indices),
        "candidate_pc_count": int(len(candidate_indices)),
        "selected_pc_count": int(len(selected_pc_indices)),
        "eigenvalues": ";".join(f"{value:.8g}" for value in normalized[: min(16, len(normalized))]),
    }


def crop_signal_around_activity(
    signal: np.ndarray,
    fs_hz: float,
    segment_sec: float,
    w_radius: int,
    wm_radius: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    target_len = int(round(segment_sec * fs_hz))
    if len(signal) == 0:
        return np.zeros(target_len, dtype=np.float32), {
            "segment_peak": 0,
            "crop_start": 0,
            "crop_end": 0,
            "pad_left": target_len,
            "pad_right": 0,
        }

    variance = moving_variance(np.abs(signal), w_radius)
    summed = moving_sum(variance, wm_radius)
    peak = int(np.argmax(summed))
    start = peak - target_len // 2
    end = start + target_len
    if start < 0:
        end -= start
        start = 0
    if end > len(signal):
        start = max(0, start - (end - len(signal)))
        end = len(signal)
    cropped = signal[start:end]
    pad_left = 0
    pad_right = 0
    if len(cropped) < target_len:
        deficit = target_len - len(cropped)
        # Keep the activity peak as centered as possible when padding is needed.
        pad_left = min(deficit, max(0, target_len // 2 - (peak - start)))
        pad_right = deficit - pad_left
        cropped = np.pad(cropped, (pad_left, pad_right), mode="edge")
    return cropped.astype(np.float32), {
        "segment_peak": peak,
        "crop_start": int(start),
        "crop_end": int(end),
        "pad_left": int(pad_left),
        "pad_right": int(pad_right),
    }


def resize_time_axis(matrix: np.ndarray, output_time_bins: int) -> np.ndarray:
    if matrix.shape[1] == output_time_bins:
        return matrix.astype(np.float32)
    old_x = np.linspace(0.0, 1.0, matrix.shape[1], dtype=np.float64)
    new_x = np.linspace(0.0, 1.0, output_time_bins, dtype=np.float64)
    resized = np.empty((matrix.shape[0], output_time_bins), dtype=np.float32)
    for row_idx in range(matrix.shape[0]):
        resized[row_idx] = np.interp(new_x, old_x, matrix[row_idx]).astype(np.float32)
    return resized


def general_denoise(s0: np.ndarray, signal_q: float, th_scmax: float) -> np.ndarray:
    if signal_q <= 1.0 or not math.isfinite(signal_q):
        th_sc = th_scmax
    else:
        log_q = math.log10(signal_q)
        th_sc = th_scmax if log_q <= 0 else min(1.0 / log_q, th_scmax)
    s1 = s0.copy()
    row_means = np.mean(s1, axis=1)
    thresholds = row_means * th_sc
    s1[s1 < thresholds[:, None]] = 0.0
    return s1


def vertical_denoise(s1: np.ndarray, freqs: np.ndarray, d_hz_per_step: float) -> np.ndarray:
    s2 = s1.copy()
    descending = np.argsort(freqs)[::-1]
    fp_max = 0.0
    for col_idx in range(s2.shape[1]):
        for row_idx in descending:
            if s2[row_idx, col_idx] <= 0:
                continue
            freq = float(freqs[row_idx])
            if fp_max <= 0.0:
                fp_max = freq
                break
            if freq <= fp_max or (freq - fp_max) <= d_hz_per_step:
                fp_max = max(fp_max, freq)
                break
            s2[row_idx, col_idx] = 0.0
    return s2


def horizontal_denoise(
    s2: np.ndarray,
    freqs: np.ndarray,
    fs_hz: float,
    carrier_hz: float,
    kappa: float,
) -> np.ndarray:
    s3 = s2.copy()
    c = 299_792_458.0
    wavelength = c / carrier_hz
    g = 9.80665
    ts = 1.0 / fs_hz
    if len(freqs) == 1:
        bandwidths = np.ones_like(freqs)
    else:
        bandwidths = np.gradient(freqs)
        bandwidths = np.maximum(np.abs(bandwidths), np.min(np.abs(bandwidths[np.nonzero(bandwidths)])))
    min_runs = np.maximum(
        1,
        np.ceil((bandwidths * wavelength) / max(kappa * g * ts, 1e-12)).astype(int),
    )
    for row_idx in range(s3.shape[0]):
        min_run = int(min_runs[row_idx])
        nonzero = s3[row_idx] > 0
        idx = 0
        while idx < len(nonzero):
            if not nonzero[idx]:
                idx += 1
                continue
            start = idx
            while idx < len(nonzero) and nonzero[idx]:
                idx += 1
            if idx - start < min_run:
                s3[row_idx, start:idx] = 0.0
    return s3


def compute_scalogram_stages(
    signal: np.ndarray,
    fs_hz: float,
    freq_min_hz: float,
    freq_max_hz: float,
    image_size: int,
    th_scmax: float,
    kappa: float,
    carrier_hz: float,
    wavelet_name: str = "gmw",
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    effective_freq_max = min(freq_max_hz, fs_hz / 2.0 - 1e-6)
    freqs_low_to_high = np.linspace(freq_min_hz, effective_freq_max, image_size, dtype=np.float64)
    freqs = freqs_low_to_high[::-1]
    try:
        from ssqueezepy import cwt
        from ssqueezepy.experimental import freq_to_scale
        from ssqueezepy.wavelets import Wavelet

        wavelet = Wavelet(wavelet_name, N=len(signal))
        # ssqueezepy.freq_to_scale expects ascending frequencies and returns descending
        # scales, while cwt expects positive increasing scales. Reverse both arrays so
        # each CWT row still has the matching frequency label.
        scales = freq_to_scale(freqs_low_to_high, wavelet, N=len(signal), fs=fs_hz)[::-1]
        wx, _ = cwt(
            signal.astype(np.float64),
            wavelet=wavelet,
            scales=scales,
            fs=fs_hz,
            l1_norm=True,
            astensor=False,
        )
    except ModuleNotFoundError:
        wx = fallback_cwt(
            signal=signal.astype(np.float64),
            freqs=freqs,
            fs_hz=fs_hz,
        )
    s0 = np.abs(wx).astype(np.float32)
    max_value = float(np.max(s0))
    if max_value > 0:
        s0 /= max_value
    signal_q = q_metric(signal, max(1, int(round(0.4 * fs_hz))))
    s1 = general_denoise(s0, signal_q, th_scmax=th_scmax)
    d_hz_per_step = 170.0 / fs_hz
    s2 = vertical_denoise(s1, freqs, d_hz_per_step=d_hz_per_step)
    s3 = horizontal_denoise(s2, freqs, fs_hz=fs_hz, carrier_hz=carrier_hz, kappa=kappa)
    stages = {
        "s0": resize_time_axis(s0, image_size).astype(np.float32),
        "s1": resize_time_axis(s1, image_size).astype(np.float32),
        "s2": resize_time_axis(s2, image_size).astype(np.float32),
        "s3": resize_time_axis(s3, image_size).astype(np.float32),
    }
    return stages, {
        "signal_q": signal_q,
        "effective_freq_max_hz": float(effective_freq_max),
        "s0_nonzero_ratio": float(np.mean(s0 > 0)),
        "s1_nonzero_ratio": float(np.mean(s1 > 0)),
        "s2_nonzero_ratio": float(np.mean(s2 > 0)),
        "s3_nonzero_ratio": float(np.mean(s3 > 0)),
    }


def fallback_cwt(signal: np.ndarray, freqs: np.ndarray, fs_hz: float) -> np.ndarray:
    """Small NumPy-only CWT fallback used when ssqueezepy is unavailable."""
    centered = signal - float(np.mean(signal))
    outputs = np.empty((len(freqs), len(centered)), dtype=np.complex64)
    for row_idx, freq in enumerate(freqs):
        cycles = 6.0
        sigma_sec = cycles / max(2.0 * math.pi * float(freq), 1e-6)
        radius = int(min(max(8, math.ceil(3.0 * sigma_sec * fs_hz)), max(8, len(centered) // 2)))
        t = np.arange(-radius, radius + 1, dtype=np.float64) / fs_hz
        envelope = np.exp(-0.5 * np.square(t / max(sigma_sec, 1e-6)))
        carrier = np.exp(2j * math.pi * float(freq) * t)
        wavelet = envelope * carrier
        norm = np.sqrt(np.sum(np.square(np.abs(wavelet))))
        if norm > 0:
            wavelet = wavelet / norm
        convolved = np.convolve(centered, np.conj(wavelet[::-1]), mode="same")
        if len(convolved) != len(centered):
            start = max(0, (len(convolved) - len(centered)) // 2)
            convolved = convolved[start : start + len(centered)]
        outputs[row_idx] = convolved.astype(np.complex64)
    return outputs


def compute_s3_scalogram(
    signal: np.ndarray,
    fs_hz: float,
    freq_min_hz: float,
    freq_max_hz: float,
    image_size: int,
    th_scmax: float,
    kappa: float,
    carrier_hz: float,
    wavelet_name: str = "gmw",
) -> tuple[np.ndarray, dict[str, Any]]:
    stages, stats = compute_scalogram_stages(
        signal=signal,
        fs_hz=fs_hz,
        freq_min_hz=freq_min_hz,
        freq_max_hz=freq_max_hz,
        image_size=image_size,
        th_scmax=th_scmax,
        kappa=kappa,
        carrier_hz=carrier_hz,
        wavelet_name=wavelet_name,
    )
    return stages["s3"], stats


def extract_amfall_feature(
    csv_path: Path,
    rx: int,
    fs_hz: float = DEFAULT_FS_HZ,
    interp_tol_ms: float = 2.0,
    segment_sec: float = DEFAULT_SEGMENT_SEC,
    omega: int = 30,
    w_sec: float = 0.4,
    wm_sec: float = 1.0,
    freq_min_hz: float = 1.0,
    freq_max_hz: float = 170.0,
    image_size: int = 224,
    th_scmax: float = 1.0,
    kappa: float = 1.0,
    carrier_hz: float = 2.4e9,
) -> tuple[np.ndarray, dict[str, Any]]:
    timestamps, amplitude = load_rx_amplitude(csv_path, rx)
    grid_us = 1_000_000.0 / fs_hz
    resampled, resample_stats = resample_amplitude(
        timestamps,
        amplitude,
        grid_us=grid_us,
        tolerance_us=interp_tol_ms * 1000.0,
    )
    w_radius = max(1, int(round(w_sec * fs_hz)))
    wm_radius = max(1, int(round(wm_sec * fs_hz)))
    selected_streams, stream_q = select_streams(resampled, omega=omega, w_radius=w_radius)
    signal, pc_stats = select_pc_signal(resampled, selected_streams, w_radius=w_radius)
    cropped_signal, crop_stats = crop_signal_around_activity(
        signal,
        fs_hz=fs_hz,
        segment_sec=segment_sec,
        w_radius=w_radius,
        wm_radius=wm_radius,
    )
    s3, cwt_stats = compute_s3_scalogram(
        cropped_signal,
        fs_hz=fs_hz,
        freq_min_hz=freq_min_hz,
        freq_max_hz=freq_max_hz,
        image_size=image_size,
        th_scmax=th_scmax,
        kappa=kappa,
        carrier_hz=carrier_hz,
    )
    metadata = {
        **resample_stats,
        **crop_stats,
        **pc_stats,
        **cwt_stats,
        "raw_rows": int(len(amplitude)),
        "resampled_rows": int(len(resampled)),
        "selected_stream_indices": ";".join(str(int(idx)) for idx in selected_streams),
        "selected_stream_count": int(len(selected_streams)),
        "stream_q_max": float(np.max(stream_q)) if len(stream_q) else 0.0,
        "stream_q_mean": float(np.mean(stream_q)) if len(stream_q) else 0.0,
    }
    return s3, metadata
