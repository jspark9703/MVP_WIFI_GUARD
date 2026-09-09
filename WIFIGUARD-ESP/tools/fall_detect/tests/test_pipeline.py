"""
Unit tests for the fall detection pipeline.

Tests signal processing and state machine logic without hardware.
"""

import sys
import time
from pathlib import Path

import numpy as np

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.streaming_features import (
    compute_final_signal,
    compute_band_energy_welch,
    select_top_subcarriers_2d,
    safe_bandpass,
    sum_and_normalize
)
from src.fall_state_machine import FallDetector, FallState
from src.preprocessing import _moving_variance, _compute_q
from src.pipeline import PipelineConfig


def test_moving_variance():
    """Test moving variance computation."""
    # Flat signal
    flat = np.ones(100, dtype=np.float32)
    mv_flat = _moving_variance(flat, 10)
    assert np.allclose(mv_flat, 0), "Flat signal should have zero variance"

    # Spike signal (high variance in the middle)
    spike = np.ones(100, dtype=np.float32)
    spike[40:60] = 10.0  # Step change
    mv_spike = _moving_variance(spike, 10)

    # Variance should be high in the transition region
    max_variance = np.max(mv_spike)
    assert max_variance > 10, "Spike region should have high variance"
    print(f"✓ Moving variance test passed (max_mv={max_variance:.2f})")


def test_q_value():
    """Test q-value (signal sensitivity) computation."""
    # Flat signal
    flat = np.ones(100, dtype=np.float32)
    q_flat = _compute_q(flat, 10)
    assert q_flat < 0.1, f"Flat signal should have low q, got {q_flat}"

    # Oscillating signal (high sensitivity)
    t = np.linspace(0, 10*np.pi, 100, dtype=np.float32)
    osc = np.sin(t)
    q_osc = _compute_q(osc, 10)
    assert q_osc > 1.0, f"Oscillating signal should have high q, got {q_osc}"
    print(f"✓ Q-value test passed (q_flat={q_flat:.2f}, q_osc={q_osc:.2f})")


def test_subcarrier_selection():
    """Test subcarrier selection by q-value."""
    # Create 30 subcarriers, some with activity, some flat
    n_samples = 300
    amp_2d = np.ones((n_samples, 30), dtype=np.float32)

    # Add oscillation to subcarriers 5, 10, 15 (should be selected)
    t = np.linspace(0, 10*np.pi, n_samples, dtype=np.float32)
    amp_2d[:, 5] += 0.5 * np.sin(t)
    amp_2d[:, 10] += 0.4 * np.sin(t)
    amp_2d[:, 15] += 0.3 * np.sin(t)

    indices, q_values = select_top_subcarriers_2d(amp_2d, omega=20, n_streams=10)

    # Check that the oscillating subcarriers have high q-values
    assert len(indices) > 0, "Should select at least one subcarrier"
    assert 5 in indices or 10 in indices, "Active subcarriers should be selected"
    print(f"✓ Subcarrier selection test passed (selected={list(indices[:5])})")


def test_safe_bandpass():
    """Test Nyquist-safe bandpass filter."""
    # Test with short signal
    short_sig = np.random.randn(5).astype(np.float32)
    filtered_short, diag_short = safe_bandpass(short_sig, fs_hz=100.0)
    assert diag_short["filter_skipped"] == True, "Short signal should be skipped"

    # Test with long signal
    long_sig = np.random.randn(300).astype(np.float32)
    filtered_long, diag_long = safe_bandpass(long_sig, fs_hz=100.0, high_hz=50.0)
    assert diag_long["filter_skipped"] == False, "Long signal should be filtered"
    assert diag_long["high_hz_clamped"] == 49.0, "High cutoff should be clamped below Nyquist"
    print(f"✓ Safe bandpass test passed (clamped_high_hz={diag_long['high_hz_clamped']})")


def test_fall_state_machine():
    """Test the fall detection state machine."""
    detector = FallDetector(
        mv_threshold=1.0,
        min_duration_s=0.5,
        merge_gap_s=0.25,
        max_duration_s=2.0,
        cooldown_s=1.0
    )

    now = time.time()
    stride_sec = 0.1  # Simulate 100ms updates

    # Phase 1: No activity (IDLE)
    status = detector.update(now, 0.5)
    assert status.state == FallState.IDLE, "Should start in IDLE"

    # Phase 2: Activity starts (SUSPECT)
    status = detector.update(now + stride_sec, 1.2)
    assert status.state == FallState.SUSPECT, "Should transition to SUSPECT"

    # Phase 3: Sustained activity (FALL after min_duration_s)
    for i in range(10):  # 1 second of above-threshold activity
        now += stride_sec
        status = detector.update(now, 1.3)
    assert status.state == FallState.FALL, "Should transition to FALL after min_duration_s"
    assert status.just_triggered == True, "Should have just triggered"

    # Phase 4: Activity stops (COOLDOWN)
    status = detector.update(now + stride_sec, 0.5)
    assert status.state == FallState.COOLDOWN, "Should transition to COOLDOWN"

    # Phase 5: Cooldown expires (back to IDLE)
    for _ in range(20):  # Wait for cooldown to expire
        now += stride_sec
        status = detector.update(now, 0.5)
    assert status.state == FallState.IDLE, "Should return to IDLE after cooldown"
    print(f"✓ Fall state machine test passed")


def test_synthetic_burst():
    """Test full pipeline with synthetic burst signal."""
    # Create synthetic CSI data: 300 samples (3 seconds at 100pps)
    n_samples = 300
    n_subcarriers = 20

    # Regular timestamps (100pps)
    timestamps_us = np.arange(n_samples) * (1e6 / 100.0)

    # Create 30 subcarriers, mostly flat
    amp_2d = np.ones((n_samples, n_subcarriers), dtype=np.float32) + np.random.randn(n_samples, n_subcarriers) * 0.1

    # Inject a burst (high activity) in the middle
    t = np.linspace(0, 10*np.pi, n_samples, dtype=np.float32)
    for i in range(n_subcarriers):
        amp_2d[:, i] += 0.5 * np.sin(t) * (t > 5*np.pi) * (t < 7*np.pi)  # Burst from 50-70% of window

    # Run pipeline
    result = compute_final_signal(
        timestamps_us.astype(np.int64),
        amp_2d,
        window_sec=3.0,
        stride_sec=0.5,
        fs_hz=100.0,
        omega=25,
        n_streams=10
    )

    assert result is not None, "Pipeline should handle synthetic data"
    assert len(result.final_signal) > 0, "Should produce a final signal"

    # Moving variance should be detectably higher in the burst region
    mv = result.moving_variance
    burst_region_mv = np.max(mv[-50:])  # Last part of window (burst tail)
    baseline_mv = np.max(mv[:50])  # Early part (before burst)

    assert burst_region_mv > baseline_mv * 2, f"Burst should increase MV: burst={burst_region_mv:.2f}, baseline={baseline_mv:.2f}"
    print(f"✓ Synthetic burst test passed (mv_burst={burst_region_mv:.2f}, mv_baseline={baseline_mv:.2f})")


def test_compute_band_energy_welch_out_of_band():
    """A pure out-of-band oscillation should produce far less energy in a
    target band than in its own band."""
    fs_hz = 100.0
    n = 600
    t = np.arange(n, dtype=np.float32) / fs_hz
    sig = np.sin(2 * np.pi * 5.0 * t).astype(np.float32)  # 5Hz, well above 0.1-0.5Hz

    energy_out_of_band = compute_band_energy_welch(sig, fs_hz, 0.1, 0.5)
    energy_in_band = compute_band_energy_welch(sig, fs_hz, 4.5, 5.5)

    assert energy_in_band > energy_out_of_band * 10, (
        f"Energy should concentrate in the signal's actual band: "
        f"in_band={energy_in_band:.4f}, out_of_band={energy_out_of_band:.4f}"
    )
    print(f"✓ compute_band_energy_welch out-of-band test passed "
          f"(in_band={energy_in_band:.4f}, out_of_band={energy_out_of_band:.4f})")


def test_wander_band_energy():
    """Test compute_final_signal(..., compute_band_energy=True) with wander
    params -- sanity-checking the Welch PSD band-energy pass added for
    presence detection (pipeline.py's wander pass).

    Uses TWO distinct bands, matching PipelineConfig's wander_prefilter_*
    (wide, 0.05-5Hz) vs wander_bandpass_* (narrow, 0.1-0.5Hz, the Welch
    integration target): pre-filtering to the SAME narrow band the energy is
    later measured in would make the measurement close to input-invariant,
    since the pre-filter alone already confines ~all of final_signal's power
    to that band before normalization (empirically confirmed flaky before
    this was split into two bands -- noise sometimes scored as high as the
    genuine oscillation). With a wider pre-filter, a narrowband oscillation
    still concentrates most of its power in the narrow measurement band,
    while broadband (but pre-filtered) noise spreads across the whole wider
    band and only a small fraction lands in the narrow slice."""
    fs_hz = 100.0
    n_samples = 600  # 6 seconds, matching the current wander_window_sec
    n_subcarriers = 20
    timestamps_us = np.arange(n_samples) * (1e6 / fs_hz)

    wander_kwargs = dict(
        window_sec=6.0, stride_sec=0.5, fs_hz=fs_hz, omega=50, n_streams=10,
        bandpass_low=0.05, bandpass_high=5.0, bandpass_order=4,
        compute_band_energy=True, energy_band_low=0.1, energy_band_high=0.5,
    )

    # Broadband (but pre-filtered to 0.05-5Hz) noise, no narrowband structure
    noise_amp = np.ones((n_samples, n_subcarriers), dtype=np.float32) + \
        np.random.randn(n_samples, n_subcarriers).astype(np.float32) * 0.3
    noise_result = compute_final_signal(timestamps_us.astype(np.int64), noise_amp, **wander_kwargs)
    assert noise_result is not None and noise_result.band_energy is not None

    # 0.3Hz oscillation (inside the narrow 0.1-0.5Hz measurement band) plus a little noise
    t = np.arange(n_samples, dtype=np.float32) / fs_hz
    osc = 0.8 * np.sin(2 * np.pi * 0.3 * t)
    osc_amp = np.ones((n_samples, n_subcarriers), dtype=np.float32) + osc[:, None] + \
        np.random.randn(n_samples, n_subcarriers).astype(np.float32) * 0.05
    osc_result = compute_final_signal(timestamps_us.astype(np.int64), osc_amp, **wander_kwargs)
    assert osc_result is not None and osc_result.band_energy is not None

    assert osc_result.band_energy > noise_result.band_energy * 2, (
        f"In-band oscillation should have higher band_energy than broadband noise: "
        f"osc={osc_result.band_energy:.4f}, noise={noise_result.band_energy:.4f}"
    )
    print(f"✓ Wander band energy test passed "
          f"(osc={osc_result.band_energy:.4f}, noise={noise_result.band_energy:.4f})")


def test_timestamp_unwrap():
    """Test that the timestamp unwrapping logic handles uint32 wraparound."""
    # Simulated uint32 counter (wraps at 2^32 ≈ 4.3 billion)
    raw_ts = np.array([2**32 - 10, 2**32 - 5, 5, 10, 15], dtype=np.uint32)

    # Simulate unwrap logic
    last_raw_ts = None
    ts_offset = 0
    ts_wrap_threshold = 2**31
    unwrapped = []

    # numpy 2: uint32 + Python int 2**32 raises OverflowError — unwrap in Python ints like main.py does
    for raw_t in raw_ts.tolist():
        if last_raw_ts is not None and raw_t < last_raw_ts - ts_wrap_threshold:
            ts_offset += 2**32
        last_raw_ts = raw_t
        unwrapped.append(ts_offset + raw_t)

    # Check that unwrapped timestamps are strictly increasing
    unwrapped = np.array(unwrapped)
    diffs = np.diff(unwrapped)
    assert np.all(diffs > 0), "Unwrapped timestamps should be strictly increasing"
    print(f"✓ Timestamp unwrap test passed (diffs={list(diffs)})")


def test_pipeline_config_sensitive_defaults():
    """PipelineConfig's mv_threshold/wander_ratio_threshold default to
    deliberately sensitive interim values (favor easy detection over
    precision until retuned against real usage data)."""
    cfg = PipelineConfig()
    assert cfg.mv_threshold == 2.0, f"Expected mv_threshold=2.0, got {cfg.mv_threshold}"
    assert cfg.wander_ratio_threshold == 1.8, f"Expected wander_ratio_threshold=1.8, got {cfg.wander_ratio_threshold}"
    print("✓ PipelineConfig sensitive-defaults test passed")


if __name__ == "__main__":
    print("Running fall detection pipeline unit tests...\n")

    test_moving_variance()
    test_q_value()
    test_subcarrier_selection()
    test_safe_bandpass()
    test_fall_state_machine()
    test_synthetic_burst()
    test_compute_band_energy_welch_out_of_band()
    test_wander_band_energy()
    test_timestamp_unwrap()
    test_pipeline_config_sensitive_defaults()

    print("\n✅ All tests passed!")
