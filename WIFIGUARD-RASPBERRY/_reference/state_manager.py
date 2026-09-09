"""
System State Manager
Singleton managing overall system state: mode, training, occupancy, fall detection
"""

import threading
import logging
from enum import IntEnum
from dataclasses import dataclass
from typing import Optional
import json
import time
from pathlib import Path

import protocol

logger = logging.getLogger(__name__)

TRAINING_DATA_FILE = Path(__file__).parent / "training_data.json"

class SystemState(IntEnum):
    IDLE = 0
    TRAINING = 1
    OCCUPANCY = 2
    FALL_DETECTING = 3
    ERROR = 4

class UserMode(IntEnum):
    OCCUPANCY_ONLY = 0
    OCCUPANCY_AND_FALL = 1

@dataclass
class OccupancyStatus:
    """Real-time occupancy detection status."""
    entry: bool = False
    motion: bool = False
    motion_count_3s: int = 0
    occupancy_confirmed: bool = False
    req_mode_change: Optional[str] = None
    rssi: float = 0.0
    rssi_variance: float = 0.0
    timestamp_ms: int = 0

@dataclass
class TrainingStatus:
    """Training progress status."""
    elapsed_s: int = 0
    total_s: int = protocol.TRAIN_TOTAL_S
    sample_count: int = 0
    current_rssi_mean: float = 0.0

@dataclass
class FallDetectionStatus:
    """Fall detection status."""
    fall_detected: bool = False
    confidence: float = 0.0
    timestamp_ms: int = 0
    fall_confirmed: bool = False

class StateManager:
    """Singleton managing system state."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._lock_state = threading.RLock()

        # System state
        self.state = SystemState.IDLE
        self.user_mode = UserMode.OCCUPANCY_ONLY
        self.last_error = None

        # Training - load from file if available
        self.base_rssi_mean = 0.0
        self.base_rssi_var = 1.0
        self._load_training_data()
        self.training_status = TrainingStatus()

        # Occupancy
        self.occupancy_status = OccupancyStatus()

        # Fall detection
        self.fall_status = FallDetectionStatus()

        # Exit detection (motion idle timer)
        self.last_motion_time = time.time()
        self.exit_threshold_s = 30  # seconds of no motion to exit

        # Cached status JSON
        self._last_occupancy_json = ""

        self._initialized = True
        logger.info("StateManager initialized")

    def set_state(self, new_state: SystemState, error_msg: Optional[str] = None):
        """Change system state."""
        with self._lock_state:
            if self.state != new_state:
                logger.info(f"State transition: {self.state.name} -> {new_state.name}")
                self.state = new_state
                if error_msg:
                    self.last_error = error_msg
                    logger.error(f"State error: {error_msg}")

    def set_user_mode(self, mode: UserMode):
        """Set user mode preference (occupancy only vs occupancy+fall)."""
        with self._lock_state:
            if self.user_mode != mode:
                logger.info(f"User mode: {mode.name}")
                self.user_mode = mode

    def update_training_status(self, status: TrainingStatus):
        """Update training progress."""
        with self._lock_state:
            self.training_status = status

    def training_complete(self, base_mean: float, base_var: float):
        """Mark training complete and store base parameters."""
        with self._lock_state:
            self.base_rssi_mean = base_mean
            self.base_rssi_var = base_var
            self.state = SystemState.OCCUPANCY
            self._save_training_data()
            logger.info(f"Training complete: mean={base_mean:.2f}, var={base_var:.2f}")

    def update_occupancy_status(self, status: OccupancyStatus):
        """Update occupancy detection status."""
        with self._lock_state:
            self.occupancy_status = status
            self._last_occupancy_json = self._serialize_occupancy_status(status)

    def process_occupancy_json(self, json_payload: dict):
        """Process JSON occupancy status from ESP32C5."""
        try:
            status = OccupancyStatus(
                entry=json_payload.get("entry", False),
                motion=json_payload.get("motion", False),
                motion_count_3s=json_payload.get("motion_count_3s", 0),
                occupancy_confirmed=json_payload.get("occupancy_confirmed", False),
                req_mode_change=json_payload.get("req_mode_change"),
                rssi=json_payload.get("rssi", 0.0),
                rssi_variance=json_payload.get("rssi_variance", 0.0),
                timestamp_ms=json_payload.get("ts_ms", 0)
            )
            self.update_occupancy_status(status)

            # Check for mode change request
            if status.req_mode_change == "fall" and self.user_mode == UserMode.OCCUPANCY_AND_FALL:
                logger.info("Occupancy confirmed, requesting fall detection mode")
                return True  # Signal to switch to fall detection

        except Exception as e:
            logger.error(f"Failed to process occupancy JSON: {e}")

        return False

    def update_fall_status(self, fall_detected: bool, confidence: float = 0.0):
        """Update fall detection status."""
        with self._lock_state:
            self.fall_status.fall_detected = fall_detected
            self.fall_status.confidence = confidence
            self.fall_status.timestamp_ms = int(time.time() * 1000)
            self.fall_status.fall_confirmed = False

            if fall_detected:
                logger.warning(f"FALL DETECTED! Confidence: {confidence:.2f}")

    def confirm_fall(self):
        """User confirms fall and acknowledges."""
        with self._lock_state:
            self.fall_status.fall_confirmed = True
            logger.info("Fall confirmed by user")

    def get_status(self) -> dict:
        """Get current system status as dict."""
        with self._lock_state:
            return {
                "state": self.state.name,
                "user_mode": self.user_mode.name,
                "base_rssi_mean": round(self.base_rssi_mean, 2),
                "base_rssi_var": round(self.base_rssi_var, 2),
                "training": {
                    "elapsed_s": self.training_status.elapsed_s,
                    "total_s": self.training_status.total_s,
                } if self.state == SystemState.TRAINING else None,
                "occupancy": {
                    "entry": self.occupancy_status.entry,
                    "motion": self.occupancy_status.motion,
                    "confirmed": self.occupancy_status.occupancy_confirmed,
                } if self.state in (SystemState.OCCUPANCY, SystemState.FALL_DETECTING) else None,
                "fall": {
                    "detected": self.fall_status.fall_detected,
                    "confidence": round(self.fall_status.confidence, 2),
                } if self.state == SystemState.FALL_DETECTING else None,
                "error": self.last_error,
            }

    def _serialize_occupancy_status(self, status: OccupancyStatus) -> str:
        """Serialize occupancy status to JSON string."""
        return json.dumps({
            "entry": status.entry,
            "motion": status.motion,
            "motion_count_3s": status.motion_count_3s,
            "confirmed": status.occupancy_confirmed,
            "timestamp_ms": status.timestamp_ms,
        }, separators=(',', ':'))

    @property
    def occupancy_json(self) -> str:
        """Get last occupancy status as JSON."""
        with self._lock_state:
            return self._last_occupancy_json

    def _load_training_data(self):
        """Load training data from file if available."""
        try:
            if TRAINING_DATA_FILE.exists():
                with open(TRAINING_DATA_FILE, 'r') as f:
                    data = json.load(f)
                    self.base_rssi_mean = data.get('base_rssi_mean', 0.0)
                    self.base_rssi_var = data.get('base_rssi_var', 1.0)
                    logger.info(f"Loaded training data: mean={self.base_rssi_mean:.2f}, var={self.base_rssi_var:.2f}")
            else:
                logger.info("No saved training data found")
        except Exception as e:
            logger.warning(f"Failed to load training data: {e}")
            self.base_rssi_mean = 0.0
            self.base_rssi_var = 1.0

    def _save_training_data(self):
        """Save training data to file."""
        try:
            data = {
                'base_rssi_mean': self.base_rssi_mean,
                'base_rssi_var': self.base_rssi_var,
                'timestamp': time.time()
            }
            with open(TRAINING_DATA_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved training data to {TRAINING_DATA_FILE}")
        except Exception as e:
            logger.error(f"Failed to save training data: {e}")


# Global singleton instance
_state_manager = None

def get_state_manager() -> StateManager:
    """Get or create the state manager singleton."""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    sm = get_state_manager()
    print(f"Initial state: {sm.state}")

    sm.set_state(SystemState.TRAINING)
    sm.update_training_status(TrainingStatus(elapsed_s=5, sample_count=250))

    print(f"Status: {json.dumps(sm.get_status(), indent=2)}")
