"""
Real-time fall detection pipeline orchestration.

Scheduled asyncio task that ticks every stride_sec, pulling CSI windows from
the monitor and running the detection pipeline.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from .streaming_features import compute_final_signal, FinalSignalResult
from .fall_state_machine import FallDetector, FallState
from .presence_state_machine import PresenceDetector, PresenceState


@dataclass
class PipelineConfig:
    """All configurable parameters for the detection pipeline."""
    window_sec: float = 3.0
    stride_sec: float = 0.5
    fs_hz: float = 100.0
    mv_window_sec: float = 0.5
    n_streams: int = 10
    bandpass_low: float = 0.5
    bandpass_high: float = 50.0
    bandpass_order: int = 4
    mv_threshold: float = 2.0  # deliberately sensitive interim default; expect more false positives until retuned
    min_duration_s: float = 0.5
    merge_gap_s: float = 0.25
    max_duration_s: float = 2.0
    cooldown_s: float = 3.0

    # Presence detection (spec 4.1.2.1): a second, lower-band signal ("wander")
    # for stationary-but-present activity, computed over its own window.
    #
    # Two distinct bands are used, not one: wander_prefilter_low/high (wide) is
    # the actual bandpass filter applied before subcarrier selection/summing/
    # normalizing, while wander_bandpass_low/high (narrow, 0.1-0.5Hz) is the
    # band the Welch PSD energy is integrated over afterward. If both stages
    # used the same narrow band, the pre-filter alone would already confine
    # ~all of final_signal's power to that band before normalization, making
    # the post-normalization Welch measurement close to input-invariant
    # (near-constant regardless of whether the input had genuine narrowband
    # structure or was just noise that survived the filter) -- empirically
    # confirmed via a flaky test before this was split into two bands.
    wander_window_sec: float = 6.0
    wander_mv_window_sec: float = 1.0
    wander_prefilter_low: float = 0.05
    wander_prefilter_high: float = 5.0
    wander_bandpass_low: float = 0.1
    wander_bandpass_high: float = 0.5
    wander_baseline: float = 0.5  # Welch PSD band energy from onboarding calibration
    wander_ratio_threshold: float = 1.8  # wander_current / wander_baseline trigger multiplier; deliberately sensitive interim default
    # Debounce: wander_ratio must stay >= wander_ratio_threshold, uninterrupted, for this
    # long before it counts as activity. The live 6s/single-segment Welch estimate has
    # coarse frequency resolution and high variance, so a transient burst of low-frequency
    # noise (a door opening, wind) can spike wander_ratio for a tick or two -- this rejects
    # spikes shorter than wander_min_duration_s without touching the window/segment design.
    wander_min_duration_s: float = 2.0
    presence_timeout_s: float = 6.0

    @property
    def omega(self) -> int:
        """Derive omega (moving-variance half-width) from mv_window_sec."""
        return max(1, round((self.mv_window_sec * self.fs_hz - 1) / 2))

    @property
    def wander_omega(self) -> int:
        """Derive omega (moving-variance half-width) from wander_mv_window_sec."""
        return max(1, round((self.wander_mv_window_sec * self.fs_hz - 1) / 2))


@dataclass
class DetectionOutput:
    """Per-tick detection output."""
    state: str
    mv_current: float
    mv_threshold: float
    confidence: float
    cooldown_remaining_s: float
    last_fall_at: Optional[str]
    just_triggered: bool
    selected_indices: list[int]
    q_values: list[float]
    raw_samples: int
    resampled_samples: int
    interp_steps: int
    fallback_steps: int
    irregular_gaps: int
    nonpositive_gaps: int
    actual_pps: float
    window_duration_s: float
    final_signal: list[float]
    pipeline_latency_ms: float
    updated_at: str

    # Presence detection (spec 4.1.2.1)
    presence_state: str
    wander_current: float
    wander_baseline: float
    wander_ratio_threshold: float
    wander_ratio: float
    wander_confirmed: bool
    last_activity_at: Optional[float]
    presence_just_changed: bool


class DetectionPipeline:
    """Detection pipeline instance operating on the single EspMonitor's CSI buffer."""

    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.detector = FallDetector(
            mv_threshold=cfg.mv_threshold,
            min_duration_s=cfg.min_duration_s,
            merge_gap_s=cfg.merge_gap_s,
            max_duration_s=cfg.max_duration_s,
            cooldown_s=cfg.cooldown_s
        )
        self.presence_detector = PresenceDetector(
            mv_threshold=cfg.mv_threshold,
            wander_baseline=cfg.wander_baseline,
            wander_ratio_threshold=cfg.wander_ratio_threshold,
            wander_min_duration_s=cfg.wander_min_duration_s,
            presence_timeout_s=cfg.presence_timeout_s,
        )

    def run_once(self, monitor) -> Optional[DetectionOutput]:
        """
        Run the detection pipeline once for this monitor.

        Args:
            monitor: EspMonitor instance.

        Returns:
            DetectionOutput on success, None if insufficient data.
        """
        t_start = time.time()

        # Get window from monitor's CSI buffer
        window = monitor.get_window(self.cfg.window_sec)
        if window is None or len(window[0]) < 30:
            return None

        timestamps_us, amp_2d = window

        # Run signal processing pipeline
        result = compute_final_signal(
            timestamps_us,
            amp_2d,
            window_sec=self.cfg.window_sec,
            stride_sec=self.cfg.stride_sec,
            fs_hz=self.cfg.fs_hz,
            omega=self.cfg.omega,
            n_streams=self.cfg.n_streams,
            bandpass_low=self.cfg.bandpass_low,
            bandpass_high=self.cfg.bandpass_high,
            bandpass_order=self.cfg.bandpass_order
        )
        if result is None:
            return None

        # Update state machine
        now_s = time.time()
        status = self.detector.update(now_s, result.mv_current)

        # Wander signal: a second, lower-band pass for presence detection (spec
        # 4.1.2.1). Uses its own window pull and a Welch PSD band-energy metric
        # (not moving variance) integrated over [wander_bandpass_low,
        # wander_bandpass_high], with a wider wander_prefilter_low/high band
        # used for the actual bandpass filter/subcarrier selection stage (see
        # PipelineConfig's comment for why these must differ).
        wander_current = 0.0
        wander_window = monitor.get_window(self.cfg.wander_window_sec)
        if wander_window is not None and len(wander_window[0]) >= 30:
            w_ts, w_amp = wander_window
            wander_result = compute_final_signal(
                w_ts, w_amp,
                window_sec=self.cfg.wander_window_sec,
                stride_sec=self.cfg.stride_sec,
                fs_hz=self.cfg.fs_hz,
                omega=self.cfg.wander_omega,
                n_streams=self.cfg.n_streams,
                bandpass_low=self.cfg.wander_prefilter_low,
                bandpass_high=self.cfg.wander_prefilter_high,
                bandpass_order=self.cfg.bandpass_order,
                compute_band_energy=True,
                energy_band_low=self.cfg.wander_bandpass_low,
                energy_band_high=self.cfg.wander_bandpass_high,
            )
            if wander_result is not None and wander_result.band_energy is not None:
                wander_current = wander_result.band_energy

        presence_status = self.presence_detector.update(now_s, result.mv_current, wander_current)

        # Compute actual packet rate
        if result.window_duration_s > 0:
            actual_pps = result.window_sample_count / result.window_duration_s
        else:
            actual_pps = 0.0

        latency_ms = (time.time() - t_start) * 1000

        return DetectionOutput(
            state=status.state.value,
            mv_current=status.mv_current,
            mv_threshold=status.mv_threshold,
            confidence=min(status.confidence, 1.0),  # Cap at 1.0 for display
            cooldown_remaining_s=status.cooldown_remaining_s,
            last_fall_at=status.last_fall_at,
            just_triggered=status.just_triggered,
            selected_indices=result.selected_indices.tolist(),
            q_values=result.q_values.tolist(),
            raw_samples=result.resample_stats.get("raw_samples", 0),
            resampled_samples=result.resample_stats.get("resampled_samples", 0),
            interp_steps=result.resample_stats.get("interp_steps", 0),
            fallback_steps=result.resample_stats.get("fallback_steps", 0),
            irregular_gaps=result.resample_stats.get("irregular_gaps", 0),
            nonpositive_gaps=result.resample_stats.get("nonpositive_gaps", 0),
            actual_pps=actual_pps,
            window_duration_s=result.window_duration_s,
            final_signal=result.final_signal.tolist(),
            pipeline_latency_ms=latency_ms,
            updated_at=time.time(),
            presence_state=presence_status.state.value,
            wander_current=presence_status.wander_current,
            wander_baseline=presence_status.wander_baseline,
            wander_ratio_threshold=presence_status.wander_ratio_threshold,
            wander_ratio=presence_status.wander_ratio,
            wander_confirmed=presence_status.wander_confirmed,
            last_activity_at=presence_status.last_activity_at,
            presence_just_changed=presence_status.just_changed,
        )

    def update_config(self, cfg: PipelineConfig):
        """Live config update (e.g., from /detection/config endpoint)."""
        self.cfg = cfg
        self.detector.mv_threshold = cfg.mv_threshold
        self.detector.min_duration_s = cfg.min_duration_s
        self.detector.merge_gap_s = cfg.merge_gap_s
        self.detector.max_duration_s = cfg.max_duration_s
        self.detector.cooldown_s = cfg.cooldown_s
        self.presence_detector.set_thresholds(
            cfg.mv_threshold, cfg.wander_baseline, cfg.wander_ratio_threshold, cfg.wander_min_duration_s
        )
        self.presence_detector.presence_timeout_s = cfg.presence_timeout_s


async def detection_loop(
    monitor,
    cfg: PipelineConfig,
    executor,
    stop_event: asyncio.Event
):
    """
    Main detection pipeline loop.

    Runs as an asyncio task, ticking every cfg.stride_sec. Dispatches the
    single pipeline run to the executor (thread pool) so numpy/scipy work
    doesn't block the event loop.

    Args:
        monitor: EspMonitor instance (the single CSI source).
        cfg: PipelineConfig.
        executor: ThreadPoolExecutor for offloading numpy work.
        stop_event: asyncio.Event to signal shutdown.
    """
    loop = asyncio.get_event_loop()
    pipeline: DetectionPipeline | None = None
    was_running = False

    while not stop_event.is_set():
        try:
            await asyncio.sleep(cfg.stride_sec)

            if not monitor.running:
                if was_running:
                    pipeline = None
                was_running = False
                continue

            was_running = True
            if pipeline is None:
                pipeline = DetectionPipeline(cfg)
            else:
                pipeline.update_config(cfg)

            task = loop.run_in_executor(executor, pipeline.run_once, monitor)
            try:
                result = await asyncio.wait_for(task, timeout=cfg.stride_sec)
                if result:
                    monitor.set_detection_result(result)
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass

        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.1)
