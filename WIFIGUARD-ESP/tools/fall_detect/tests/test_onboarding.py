"""
Unit tests for onboarding calibration orchestration (derive_threshold and
run_calibration), without hardware. run_calibration is exercised against a
fake EspMonitor double so the full silence -> resume -> baseline-capture flow
can run in milliseconds instead of ~20+ real seconds.
"""

import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.onboarding import derive_threshold, run_calibration, OnboardingState
from src.pipeline import PipelineConfig


def test_derive_threshold_mean_std():
    """threshold = mean + k*std for a noisy-but-nonzero baseline."""
    values = np.array([1.0, 1.2, 0.8, 1.1, 0.9], dtype=np.float32)
    k = 4.0
    floor = 0.05
    expected = float(np.mean(values) + k * np.std(values))
    got = derive_threshold(values, k, floor)
    assert abs(got - expected) < 1e-6, f"Expected {expected}, got {got}"
    assert got > floor, "Non-degenerate baseline should exceed the floor"
    print(f"✓ derive_threshold mean+k*std test passed (threshold={got:.4f})")


def test_derive_threshold_floor_clamp():
    """A perfectly quiet (zero-variance) baseline should clamp to the floor."""
    values = np.zeros(50, dtype=np.float32)
    floor = 0.3
    got = derive_threshold(values, k=4.0, floor=floor)
    assert got == floor, f"Zero-variance baseline should clamp to floor, got {got}"
    print(f"✓ derive_threshold floor-clamp test passed (threshold={got})")


class FakeEspMonitor:
    """Duck-typed double for EspMonitor, keyed off actual elapsed wall-clock
    time (not read count, which would be fragile under scheduling jitter):
    packet_count stays flat for `silent_duration_s` after send_line("train")
    is called, then increments on every subsequent read -- matching the real
    firmware's silent-during-training / continuous-once-streaming behavior.
    Satisfies run_calibration()'s expected contract (`.running`,
    `.packet_count`, `.send_line()`, `.get_window()`)."""

    def __init__(self, silent_duration_s: float, fs_hz: float = 100.0):
        self.running = True
        self._packet_count = 0
        self.sent_lines: list[str] = []
        self._silent_duration_s = silent_duration_s
        self._train_sent_at: float | None = None
        self._fs_hz = fs_hz

    def send_line(self, text: str) -> bool:
        self.sent_lines.append(text)
        self._train_sent_at = time.monotonic()
        return True

    @property
    def packet_count(self) -> int:
        if self._train_sent_at is None or (time.monotonic() - self._train_sent_at) > self._silent_duration_s:
            self._packet_count += 1
        return self._packet_count

    def get_window(self, window_sec: float):
        n = max(30, int(window_sec * self._fs_hz))
        ts = np.arange(n, dtype=np.int64) * int(1e6 / self._fs_hz)
        amp = np.ones((n, 20), dtype=np.float32) + np.random.randn(n, 20).astype(np.float32) * 0.05
        return ts, amp


def test_run_calibration_success():
    """Full happy-path: leaving -> train -> silence -> resume -> baseline capture -> done."""

    async def _run():
        # Silent window (0.15s) is comfortably longer than silence_confirm_s
        # (0.05s), so phase A observes a genuinely stable quiet period before
        # phase B's resume-detection is expected to fire.
        monitor = FakeEspMonitor(silent_duration_s=0.15)
        cfg = PipelineConfig()
        state = OnboardingState()
        executor = ThreadPoolExecutor(max_workers=2)
        try:
            await run_calibration(
                monitor, cfg, state, executor,
                leave_wait_s=0.05,
                silence_confirm_s=0.05, silence_timeout_s=1.0,
                resume_timeout_s=2.0, baseline_window_s=0.1,
                poll_interval_s=0.01,
            )
        finally:
            executor.shutdown(wait=False)
        return monitor, cfg, state

    monitor, cfg, state = asyncio.run(_run())
    calib = state.calibration
    assert monitor.sent_lines == ["train"], "Should have sent exactly one 'train' command"
    assert calib.phase == "done", f"Expected phase=done, got {calib.phase} (error={calib.error})"
    assert calib.mv_threshold is not None and calib.mv_threshold > 0
    assert calib.wander_baseline is not None and calib.wander_baseline > 0
    assert cfg.mv_threshold == calib.mv_threshold, "Should write threshold into shared PipelineConfig"
    assert cfg.wander_baseline == calib.wander_baseline
    assert calib.agc_duration_s is not None and calib.agc_duration_s > 0, \
        "Should record how long the waiting_agc phase took"
    print(f"✓ run_calibration success test passed (mv_threshold={calib.mv_threshold:.4f}, "
          f"wander_baseline={calib.wander_baseline:.4f}, agc_duration_s={calib.agc_duration_s:.4f})")


def test_leaving_phase_runs_before_train_command():
    """The leaving phase should run to completion -- with monitor.sent_lines
    staying empty -- before "train" is ever sent."""

    async def _run():
        monitor = FakeEspMonitor(silent_duration_s=0.05)
        cfg = PipelineConfig()
        state = OnboardingState()
        executor = ThreadPoolExecutor(max_workers=2)
        task = asyncio.create_task(run_calibration(
            monitor, cfg, state, executor,
            leave_wait_s=0.3,
            silence_confirm_s=0.02, silence_timeout_s=1.0,
            resume_timeout_s=2.0, baseline_window_s=0.05,
            poll_interval_s=0.01,
        ))
        await asyncio.sleep(0.1)  # partway through the 0.3s leaving phase
        phase_during_leaving = state.calibration.phase
        sent_during_leaving = list(monitor.sent_lines)
        await task
        executor.shutdown(wait=False)
        return phase_during_leaving, sent_during_leaving, state

    phase_during_leaving, sent_during_leaving, state = asyncio.run(_run())
    assert phase_during_leaving == "leaving", f"Expected 'leaving' phase, got {phase_during_leaving}"
    assert sent_during_leaving == [], "train should not be sent while still in the leaving phase"
    assert state.calibration.phase == "done", \
        f"Expected phase=done, got {state.calibration.phase} (error={state.calibration.error})"
    print("✓ leaving-phase-runs-before-train-command test passed")


def test_run_calibration_not_connected():
    """Should fail fast if the device isn't connected."""

    async def _run():
        monitor = FakeEspMonitor(silent_duration_s=0.15)
        monitor.running = False
        cfg = PipelineConfig()
        state = OnboardingState()
        executor = ThreadPoolExecutor(max_workers=2)
        try:
            await run_calibration(monitor, cfg, state, executor)
        finally:
            executor.shutdown(wait=False)
        return state

    state = asyncio.run(_run())
    assert state.calibration.phase == "error"
    assert "not connected" in state.calibration.error
    print("✓ run_calibration not-connected test passed")


def test_run_calibration_resume_timeout():
    """Should report an error if streaming never resumes after training."""

    async def _run():
        # silent_duration_s far exceeds resume_timeout_s => never resumes in time.
        monitor = FakeEspMonitor(silent_duration_s=10.0)
        cfg = PipelineConfig()
        state = OnboardingState()
        executor = ThreadPoolExecutor(max_workers=2)
        try:
            await run_calibration(
                monitor, cfg, state, executor,
                leave_wait_s=0.05,
                silence_confirm_s=0.02, silence_timeout_s=1.0,
                resume_timeout_s=0.1, baseline_window_s=0.1,
                poll_interval_s=0.01,
            )
        finally:
            executor.shutdown(wait=False)
        return state

    state = asyncio.run(_run())
    assert state.calibration.phase == "error"
    assert "did not resume" in state.calibration.error
    print("✓ run_calibration resume-timeout test passed")


if __name__ == "__main__":
    print("Running onboarding calibration unit tests...\n")

    test_derive_threshold_mean_std()
    test_derive_threshold_floor_clamp()
    test_run_calibration_success()
    test_leaving_phase_runs_before_train_command()
    test_run_calibration_not_connected()
    test_run_calibration_resume_timeout()

    print("\n✅ All tests passed!")
