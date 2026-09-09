"""
Unit tests for the presence detection state machine.

Tests the PRESENT/ABSENT timeout logic (spec 4.1.2.1), including the
wander_current/wander_baseline ratio comparison and the wander_min_duration_s
debounce (rejects transient spikes from the coarse-resolution live PSD
estimate), without hardware.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.presence_state_machine import PresenceDetector, PresenceState


def test_default_state_is_absent():
    """Detector should start ABSENT before any update."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.5, wander_ratio_threshold=2.0, presence_timeout_s=6.0)
    assert detector.state == PresenceState.ABSENT, "Should start ABSENT"
    print("✓ Default state test passed")


def test_mv_above_threshold_triggers_present():
    """A single MV-above-threshold update should flip to PRESENT (MV isn't debounced)."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.5, wander_ratio_threshold=2.0, presence_timeout_s=6.0)
    now = 1000.0
    status = detector.update(now, mv_value=1.5, wander_value=0.1)  # wander ratio=0.2, well under threshold
    assert status.state == PresenceState.PRESENT, "Should be PRESENT after MV above threshold"
    assert status.just_changed == True, "Should report just_changed on first transition"
    print("✓ MV-triggers-PRESENT test passed")


def test_wander_ratio_below_threshold_does_not_trigger():
    """wander_current/wander_baseline below wander_ratio_threshold should NOT trigger presence."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.5, wander_ratio_threshold=2.0, presence_timeout_s=6.0)
    now = 1000.0
    # wander_value=0.9 -> ratio=1.8, below the 2.0 threshold
    status = detector.update(now, mv_value=0.1, wander_value=0.9)
    assert status.wander_ratio < 2.0
    assert status.wander_confirmed == False
    assert status.state == PresenceState.ABSENT, "Ratio below threshold should not trigger presence"
    print(f"✓ Wander-ratio-below-threshold test passed (ratio={status.wander_ratio:.2f})")


def test_wander_ratio_at_threshold_triggers_present_with_zero_debounce():
    """wander_current/wander_baseline exactly at wander_ratio_threshold should trigger (>=),
    with wander_min_duration_s=0 to isolate the ratio comparison from the debounce timing."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.5, wander_ratio_threshold=2.0,
                                 wander_min_duration_s=0.0, presence_timeout_s=6.0)
    now = 1000.0
    # wander_value=1.0 -> ratio=2.0, exactly at threshold
    status = detector.update(now, mv_value=0.1, wander_value=1.0)
    assert status.wander_ratio == 2.0
    assert status.wander_confirmed == True, "Zero debounce should confirm immediately"
    assert status.state == PresenceState.PRESENT, "Ratio at threshold should trigger presence (>=)"
    print("✓ Wander-ratio-at-threshold (zero debounce) test passed")


def test_wander_ratio_above_threshold_triggers_present_with_zero_debounce():
    """A wander energy well above baseline*ratio_threshold should flip to PRESENT,
    with wander_min_duration_s=0 to isolate the ratio comparison from debounce timing."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.5, wander_ratio_threshold=2.0,
                                 wander_min_duration_s=0.0, presence_timeout_s=6.0)
    now = 1000.0
    # wander_value=1.5 -> ratio=3.0, above the 2.0 threshold
    status = detector.update(now, mv_value=0.1, wander_value=1.5)
    assert status.wander_ratio == 3.0
    assert status.state == PresenceState.PRESENT, "Should be PRESENT from wander ratio alone"
    print("✓ Wander-ratio-above-threshold (zero debounce) test passed")


def test_zero_baseline_does_not_crash():
    """A zero/near-zero wander_baseline should not raise a ZeroDivisionError."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.0, wander_ratio_threshold=2.0, presence_timeout_s=6.0)
    status = detector.update(1000.0, mv_value=0.1, wander_value=0.5)
    assert status.wander_ratio == 0.0
    assert status.state == PresenceState.ABSENT
    print("✓ Zero-baseline-safe test passed")


def test_stays_present_within_timeout():
    """Repeated below-threshold updates within presence_timeout_s of the last
    activity should keep the state PRESENT."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.5, wander_ratio_threshold=2.0, presence_timeout_s=6.0)
    now = 1000.0
    detector.update(now, mv_value=1.5, wander_value=0.1)  # trigger PRESENT via MV

    for i in range(1, 6):  # 5 below-threshold ticks, 1s apart, well within 6s timeout
        now += 1.0
        status = detector.update(now, mv_value=0.1, wander_value=0.1)
        assert status.state == PresenceState.PRESENT, f"Should stay PRESENT at t+{i}s"
        assert status.just_changed == False, f"Should not re-trigger just_changed at t+{i}s"
    print("✓ Stays-PRESENT-within-timeout test passed")


def test_flips_to_absent_after_timeout():
    """No activity for presence_timeout_s should flip to ABSENT exactly once."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.5, wander_ratio_threshold=2.0, presence_timeout_s=6.0)
    now = 1000.0
    detector.update(now, mv_value=1.5, wander_value=0.1)  # trigger PRESENT via MV

    # Still within timeout
    status = detector.update(now + 5.9, mv_value=0.1, wander_value=0.1)
    assert status.state == PresenceState.PRESENT, "Should still be PRESENT just under timeout"

    # Timeout elapsed
    status = detector.update(now + 6.1, mv_value=0.1, wander_value=0.1)
    assert status.state == PresenceState.ABSENT, "Should be ABSENT after timeout elapses"
    assert status.just_changed == True, "Should report just_changed exactly on the flip"

    # Subsequent tick should not re-report just_changed
    status = detector.update(now + 6.2, mv_value=0.1, wander_value=0.1)
    assert status.state == PresenceState.ABSENT
    assert status.just_changed == False, "Should not re-trigger just_changed once already ABSENT"
    print("✓ Flips-to-ABSENT-after-timeout test passed")


def test_wander_spike_shorter_than_min_duration_does_not_trigger():
    """A wander ratio spike shorter than wander_min_duration_s should NOT trigger
    presence -- this is the debounce protecting against transient low-frequency
    noise (a door opening, wind) spiking the coarse-resolution live PSD estimate."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.5, wander_ratio_threshold=2.0,
                                 wander_min_duration_s=2.0, presence_timeout_s=6.0)
    now = 1000.0
    status = detector.update(now, mv_value=0.1, wander_value=1.5)  # ratio=3.0, spike starts
    assert status.wander_confirmed == False, "Should not confirm on the very first tick"
    assert status.state == PresenceState.ABSENT

    status = detector.update(now + 0.8, mv_value=0.1, wander_value=0.1)  # drops back before 2.0s
    assert status.wander_confirmed == False
    assert status.state == PresenceState.ABSENT, "Brief spike shorter than wander_min_duration_s should not trigger"

    # Even well after the spike, still absent (activity was never confirmed)
    status = detector.update(now + 7.0, mv_value=0.1, wander_value=0.1)
    assert status.state == PresenceState.ABSENT
    print("✓ Wander-spike-shorter-than-min-duration test passed")


def test_wander_sustained_for_min_duration_triggers():
    """A wander ratio sustained continuously for >= wander_min_duration_s should
    confirm and trigger PRESENT."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.5, wander_ratio_threshold=2.0,
                                 wander_min_duration_s=2.0, presence_timeout_s=6.0)
    now = 1000.0
    status = detector.update(now, mv_value=0.1, wander_value=1.5)  # streak starts
    assert status.wander_confirmed == False
    assert status.state == PresenceState.ABSENT

    status = detector.update(now + 1.0, mv_value=0.1, wander_value=1.5)  # 1.0s into streak
    assert status.wander_confirmed == False, "Still under wander_min_duration_s"
    assert status.state == PresenceState.ABSENT

    status = detector.update(now + 2.1, mv_value=0.1, wander_value=1.5)  # 2.1s into streak
    assert status.wander_confirmed == True, "Should confirm once sustained >= wander_min_duration_s"
    assert status.state == PresenceState.PRESENT
    print("✓ Wander-sustained-triggers-after-min-duration test passed")


def test_wander_interrupted_streak_resets():
    """A single below-threshold tick during a wander streak should reset the
    debounce timer -- the streak must be uninterrupted."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.5, wander_ratio_threshold=2.0,
                                 wander_min_duration_s=2.0, presence_timeout_s=6.0)
    now = 1000.0
    detector.update(now, mv_value=0.1, wander_value=1.5)         # streak starts at t=0
    detector.update(now + 1.5, mv_value=0.1, wander_value=1.5)   # 1.5s in, not yet confirmed

    # One tick drops below threshold -- resets the streak
    status = detector.update(now + 1.6, mv_value=0.1, wander_value=0.1)
    assert status.wander_confirmed == False
    assert status.state == PresenceState.ABSENT

    # Resume high ratio -- streak restarts here (t=1.6), so 1.9s later should NOT yet confirm
    status = detector.update(now + 3.5, mv_value=0.1, wander_value=1.5)
    assert status.wander_confirmed == False, "Streak should have restarted after the interruption, not carried over"

    # 2.1s after the restart point (1.6) should confirm
    status = detector.update(now + 5.6, mv_value=0.1, wander_value=1.5)
    assert status.wander_confirmed == True
    assert status.state == PresenceState.PRESENT
    print("✓ Wander-interrupted-streak-resets test passed")


def test_set_thresholds_updates_live():
    """set_thresholds() should update all four tunables in place."""
    detector = PresenceDetector(mv_threshold=1.0, wander_baseline=0.5, wander_ratio_threshold=2.0,
                                 wander_min_duration_s=2.0, presence_timeout_s=6.0)
    detector.set_thresholds(mv_threshold=2.0, wander_baseline=1.0, wander_ratio_threshold=3.0, wander_min_duration_s=1.0)
    assert detector.mv_threshold == 2.0
    assert detector.wander_baseline == 1.0
    assert detector.wander_ratio_threshold == 3.0
    assert detector.wander_min_duration_s == 1.0
    print("✓ set_thresholds test passed")


if __name__ == "__main__":
    print("Running presence state machine unit tests...\n")

    test_default_state_is_absent()
    test_mv_above_threshold_triggers_present()
    test_wander_ratio_below_threshold_does_not_trigger()
    test_wander_ratio_at_threshold_triggers_present_with_zero_debounce()
    test_wander_ratio_above_threshold_triggers_present_with_zero_debounce()
    test_zero_baseline_does_not_crash()
    test_stays_present_within_timeout()
    test_flips_to_absent_after_timeout()
    test_wander_spike_shorter_than_min_duration_does_not_trigger()
    test_wander_sustained_for_min_duration_triggers()
    test_wander_interrupted_streak_resets()
    test_set_thresholds_updates_live()

    print("\n✅ All tests passed!")
