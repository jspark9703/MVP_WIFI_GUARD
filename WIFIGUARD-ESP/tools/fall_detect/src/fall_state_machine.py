"""
Fall detection state machine for real-time detection.

Implements the hysteresis/threshold logic from true_activity_time.py's
extract_fall_segments as an incremental online state machine:
IDLE → SUSPECT → FALL → COOLDOWN → IDLE
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class FallState(Enum):
    """Fall detection state."""
    IDLE = "idle"
    SUSPECT = "suspect"
    FALL = "fall"
    COOLDOWN = "cooldown"


@dataclass
class FallDetectorStatus:
    """Status output from a detector update."""
    state: FallState
    mv_current: float
    mv_threshold: float
    confidence: float
    state_entered_at_s: float  # Time (seconds, absolute) when state was entered
    cooldown_remaining_s: float = 0.0
    last_fall_at: str | None = None  # ISO timestamp of last fall
    just_triggered: bool = False  # True if just entered FALL state


class FallDetector:
    """
    Online fall detection state machine.

    Adapted from true_activity_time.py's extract_fall_segments logic.
    """

    def __init__(
        self,
        mv_threshold: float = 1.0,
        min_duration_s: float = 0.5,
        merge_gap_s: float = 0.25,
        max_duration_s: float = 2.0,
        cooldown_s: float = 3.0
    ):
        """
        Args:
            mv_threshold: Moving-variance threshold for fall trigger.
            min_duration_s: Minimum duration above threshold to confirm FALL.
            merge_gap_s: Max gap to merge consecutive above-threshold intervals.
            max_duration_s: Hard cap on fall duration (force to COOLDOWN if exceeded).
            cooldown_s: Duration to remain in COOLDOWN before returning to IDLE.
        """
        self.mv_threshold = mv_threshold
        self.min_duration_s = min_duration_s
        self.merge_gap_s = merge_gap_s
        self.max_duration_s = max_duration_s
        self.cooldown_s = cooldown_s

        self.state = FallState.IDLE
        self.state_entered_at_s = 0.0
        self.last_fall_at: str | None = None

        # For SUSPECT → FALL hysteresis
        self._suspect_start_s: float | None = None
        self._last_above_threshold_s: float | None = None

    def update(self, now_s: float, mv_value: float) -> FallDetectorStatus:
        """
        Update the state machine with the current moving-variance value.

        Args:
            now_s: Current time in seconds (absolute, e.g. from time.time()).
            mv_value: Current moving-variance scalar.

        Returns:
            FallDetectorStatus reflecting the new state.
        """
        above_threshold = mv_value >= self.mv_threshold
        just_triggered = False

        if self.state == FallState.IDLE:
            if above_threshold:
                self.state = FallState.SUSPECT
                self.state_entered_at_s = now_s
                self._suspect_start_s = now_s
                self._last_above_threshold_s = now_s
        elif self.state == FallState.SUSPECT:
            if above_threshold:
                self._last_above_threshold_s = now_s
                # Check if enough time has passed to confirm FALL
                if now_s - self._suspect_start_s >= self.min_duration_s:
                    self.state = FallState.FALL
                    self.state_entered_at_s = now_s
                    just_triggered = True
                    self.last_fall_at = datetime.now().isoformat()
            else:
                # Below threshold; check if gap is within merge tolerance
                if now_s - self._last_above_threshold_s <= self.merge_gap_s:
                    # Still within merge gap; stay in SUSPECT
                    pass
                else:
                    # Gap too large; revert to IDLE
                    self.state = FallState.IDLE
                    self.state_entered_at_s = now_s

        elif self.state == FallState.FALL:
            fall_duration = now_s - self.state_entered_at_s
            if fall_duration >= self.max_duration_s:
                # Force to COOLDOWN after max_duration_s
                self.state = FallState.COOLDOWN
                self.state_entered_at_s = now_s
            elif not above_threshold:
                # Below threshold; transition to COOLDOWN
                self.state = FallState.COOLDOWN
                self.state_entered_at_s = now_s

        elif self.state == FallState.COOLDOWN:
            cooldown_elapsed = now_s - self.state_entered_at_s
            if cooldown_elapsed >= self.cooldown_s:
                # Cooldown finished; return to IDLE
                self.state = FallState.IDLE
                self.state_entered_at_s = now_s

        # Compute cooldown remaining
        cooldown_remaining = 0.0
        if self.state == FallState.COOLDOWN:
            cooldown_remaining = max(
                0.0,
                self.cooldown_s - (now_s - self.state_entered_at_s)
            )

        # Compute confidence
        confidence = mv_value / self.mv_threshold if self.mv_threshold > 0 else 0.0

        return FallDetectorStatus(
            state=self.state,
            mv_current=mv_value,
            mv_threshold=self.mv_threshold,
            confidence=confidence,
            state_entered_at_s=self.state_entered_at_s,
            cooldown_remaining_s=cooldown_remaining,
            last_fall_at=self.last_fall_at,
            just_triggered=just_triggered
        )

    def set_threshold(self, mv_threshold: float):
        """Update the threshold (e.g., from a live config endpoint)."""
        self.mv_threshold = mv_threshold
