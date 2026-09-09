"""
First-login onboarding orchestration (functional spec 3.2).

Holds in-memory onboarding/session state (no persistence layer, matching the
rest of this backend) and drives the guided calibration flow for step 3:
send the firmware's "train" command, wait out its AGC-settle window, then
capture a baseline CSI window with the room empty to derive mv_threshold
(mean+k*std of MV moving variance) and wander_baseline (a single Welch PSD
band-energy value) from the ambient noise floor.
"""

import asyncio
import time
from dataclasses import dataclass, field

import numpy as np

from .pipeline import PipelineConfig
from .streaming_features import compute_final_signal


@dataclass
class CalibrationState:
    """Progress/result of the onboarding step-3 calibration run."""
    phase: str = "idle"  # idle | leaving | waiting_ack | waiting_agc | measuring | done | error
    started_at: float | None = None
    phase_started_at: float | None = None  # when the current phase began; reset on every transition
    agc_duration_s: float | None = None  # how long waiting_agc actually took (observability, not a success check)
    mv_threshold: float | None = None
    wander_baseline: float | None = None
    error: str | None = None


@dataclass
class OnboardingState:
    """In-memory onboarding/session state. Resets on backend restart, matching
    the existing PipelineConfig/fall-history in-memory-only behavior."""
    onboarded: bool = False
    step: int = 1
    service_type: str | None = None
    device_name: str | None = None
    room_name: str | None = None
    calibration: CalibrationState = field(default_factory=CalibrationState)


def derive_threshold(values: np.ndarray, k: float, floor: float) -> float:
    """
    Derive a detection threshold from a baseline (quiet-room) signal sample.

    threshold = mean + k*std, clamped to a minimum floor so a perfectly quiet
    baseline (near-zero std) doesn't produce a degenerate near-zero threshold
    that would trigger on any tiny noise.
    """
    return float(max(np.mean(values) + k * np.std(values), floor))


def _compute_baseline_thresholds(
    window: tuple[np.ndarray, np.ndarray],
    cfg: PipelineConfig,
    k_mv: float,
    mv_floor: float,
    wander_baseline_floor: float,
) -> tuple[float, float]:
    """Run the MV and wander signal chains over a captured baseline window and
    derive a threshold/baseline from each. Runs in a thread-pool executor
    (numpy/scipy work).

    MV gets a mean+k*std threshold from its moving_variance time series, same
    as before. Wander is different: a Welch PSD band-energy value is a single
    scalar per window (no time series to average), so wander_baseline is just
    that scalar directly, clamped to a floor to avoid a near-zero baseline
    blowing up the wander_current/wander_baseline ratio at runtime.
    """
    ts, amp = window

    mv_result = compute_final_signal(
        ts, amp,
        window_sec=cfg.window_sec, stride_sec=cfg.stride_sec, fs_hz=cfg.fs_hz,
        omega=cfg.omega, n_streams=cfg.n_streams,
        bandpass_low=cfg.bandpass_low, bandpass_high=cfg.bandpass_high,
        bandpass_order=cfg.bandpass_order,
    )
    wander_result = compute_final_signal(
        ts, amp,
        window_sec=cfg.wander_window_sec, stride_sec=cfg.stride_sec, fs_hz=cfg.fs_hz,
        omega=cfg.wander_omega, n_streams=cfg.n_streams,
        bandpass_low=cfg.wander_prefilter_low, bandpass_high=cfg.wander_prefilter_high,
        bandpass_order=cfg.bandpass_order,
        compute_band_energy=True,
        energy_band_low=cfg.wander_bandpass_low, energy_band_high=cfg.wander_bandpass_high,
    )

    mv_threshold = (
        derive_threshold(mv_result.moving_variance, k_mv, mv_floor)
        if mv_result is not None else mv_floor
    )
    wander_baseline = (
        max(wander_result.band_energy, wander_baseline_floor)
        if wander_result is not None and wander_result.band_energy is not None
        else wander_baseline_floor
    )
    return mv_threshold, wander_baseline


async def run_calibration(
    monitor,
    cfg: PipelineConfig,
    state: OnboardingState,
    executor,
    *,
    leave_wait_s: float = 10.0,
    silence_confirm_s: float = 0.2,
    silence_timeout_s: float = 3.0,
    resume_timeout_s: float = 20.0,
    baseline_window_s: float = 20.0,
    poll_interval_s: float = 0.05,
    k_mv: float = 2.0,
    mv_floor: float = 0.3,
    wander_baseline_floor: float = 0.05,
) -> None:
    """
    Drive onboarding step 3: wait leave_wait_s for the installer to vacate the
    room, send "train", wait for the firmware's AGC-settle window to elapse,
    then capture a baseline_window_s CSI window (expected to be an empty/quiet
    room) and derive mv_threshold/wander_baseline from it.

    leave_wait_s defaults to 10s -- gives the installer time to physically
    leave the room before anything (train command, AGC, baseline capture)
    begins. Runs before send_line("train") so no measurement starts early.

    baseline_window_s defaults to 20s (longer than the live 6s wander window) --
    calibration is one-shot so responsiveness doesn't matter, and a longer
    capture gives a more stable Welch PSD baseline estimate.

    silence_confirm_s defaults to 0.2s -- comfortably above the ~7ms it takes
    in-flight UART bytes to drain at 921600 baud (one CSI frame is ~650 bytes),
    while leaving ~0.8s of margin under the firmware's ~1s AGC-settle window
    (csi_recv_calibrate's CSI_TRAIN_DURATION_US). A prior 0.7s default left
    only ~0.3s of margin, risking an intermittent waiting_ack timeout if
    scheduling jitter pushed past it.

    k_mv defaults to 2.0 (was 4.0) -- deliberately tuned for sensitivity over
    precision as an interim measure; expect more false positives until this
    is retuned against real usage data.

    Mutates state.calibration in place as it progresses so GET /onboarding/calibrate/status
    can report live progress: calib.phase_started_at resets on every phase
    transition (unlike calib.started_at, which is fixed at the very start), so
    callers can compute per-phase elapsed time. calib.agc_duration_s records
    how long the waiting_agc phase actually took, once known -- an
    observability signal only (whether the timing looks sane), not proof the
    firmware's AGC calibration itself succeeded; the firmware has no protocol
    to report that.

    All durations/k-constants are parameters (not module globals) so tests
    can pass small values and run in milliseconds.
    """
    calib = state.calibration
    calib.phase = "idle"
    calib.error = None
    calib.mv_threshold = None
    calib.wander_baseline = None
    calib.agc_duration_s = None
    calib.started_at = time.time()

    if not monitor.running:
        calib.phase = "error"
        calib.error = "device not connected"
        return

    # Phase 0: leave-room wait -- happens before send_line("train") so no
    # measurement of any kind starts until the installer has had time to leave.
    calib.phase = "leaving"
    calib.phase_started_at = time.time()
    await asyncio.sleep(leave_wait_s)
    if not monitor.running:
        calib.phase = "error"
        calib.error = "device disconnected during calibration"
        return

    if not monitor.send_line("train"):
        calib.phase = "error"
        calib.error = "failed to send train command to device"
        return

    # Phase A: confirm the command landed by waiting for in-flight frames to
    # drain and packet flow to actually go quiet (the firmware sends nothing
    # while training).
    calib.phase = "waiting_ack"
    calib.phase_started_at = time.time()
    phase_start = time.time()
    last_count = monitor.packet_count
    last_change_at = phase_start
    while True:
        if not monitor.running:
            calib.phase = "error"
            calib.error = "device disconnected during calibration"
            return
        await asyncio.sleep(poll_interval_s)
        now = time.time()
        count = monitor.packet_count
        if count != last_count:
            last_count = count
            last_change_at = now
        elif now - last_change_at >= silence_confirm_s:
            break
        if now - phase_start >= silence_timeout_s:
            calib.phase = "error"
            calib.error = "device did not stop streaming after train command (command may not have been received)"
            return

    # Phase B: wait for the firmware to finish its AGC-settle window and
    # resume streaming.
    calib.phase = "waiting_agc"
    calib.phase_started_at = time.time()
    phase_start = time.time()
    baseline_count = monitor.packet_count
    while True:
        if not monitor.running:
            calib.phase = "error"
            calib.error = "device disconnected during calibration"
            return
        await asyncio.sleep(poll_interval_s)
        if monitor.packet_count > baseline_count:
            break
        if time.time() - phase_start >= resume_timeout_s:
            calib.phase = "error"
            calib.error = "streaming did not resume after AGC calibration window (timeout)"
            return

    # How long the AGC-settle window actually took -- an observability signal
    # (does the timing look sane?), not proof the firmware's AGC calibration
    # itself succeeded; the firmware has no protocol to report that.
    calib.agc_duration_s = time.time() - phase_start

    # Phase C: capture the baseline window. Streaming just resumed, so once
    # baseline_window_s elapses, every sample in the trailing window is
    # guaranteed post-resume (pre-training samples are older than that).
    calib.phase = "measuring"
    calib.phase_started_at = time.time()
    await asyncio.sleep(baseline_window_s)
    if not monitor.running:
        calib.phase = "error"
        calib.error = "device disconnected during calibration"
        return

    window = monitor.get_window(baseline_window_s)
    if window is None:
        calib.phase = "error"
        calib.error = "insufficient CSI data captured for baseline"
        return

    loop = asyncio.get_event_loop()
    try:
        mv_threshold, wander_baseline = await loop.run_in_executor(
            executor, _compute_baseline_thresholds,
            window, cfg, k_mv, mv_floor, wander_baseline_floor,
        )
    except Exception as exc:
        calib.phase = "error"
        calib.error = f"baseline computation failed: {exc}"
        return

    cfg.mv_threshold = mv_threshold
    cfg.wander_baseline = wander_baseline
    calib.mv_threshold = mv_threshold
    calib.wander_baseline = wander_baseline
    calib.phase = "done"
    calib.phase_started_at = time.time()
