import asyncio
import json
import logging
import struct
import threading
import time
import queue
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
import serial
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.pipeline import PipelineConfig, detection_loop, DetectionOutput
from src.onboarding import OnboardingState, run_calibration

logger = logging.getLogger(__name__)

CURRENT_DIR = Path(__file__).resolve().parent
STATIC_DIR = CURRENT_DIR / "static"

# ESP32 binary‑frame protocol
SERIAL_QUEUE_MAXSIZE = 2048
QUEUE_READ_TIMEOUT_SEC = 0.2
BINARY_FRAME_MAGIC = 0xA55A
BINARY_FRAME_VERSION = 1
BINARY_FRAME_TYPE_CSI = 1
BINARY_FRAME_HEADER_FORMAT = "<HHBBI6sbBBBBBBBBBBbBBBBHHIbBHBB"
BINARY_FRAME_HEADER_SIZE = struct.calcsize(BINARY_FRAME_HEADER_FORMAT)
BINARY_FRAME_CHECKSUM_SIZE = 2
RAW_DATA_COLUMNS = 612
BINARY_FRAME_MAX_SIZE = (
    BINARY_FRAME_HEADER_SIZE + RAW_DATA_COLUMNS + BINARY_FRAME_CHECKSUM_SIZE
)
BINARY_MAGIC_BYTES = struct.pack("<H", BINARY_FRAME_MAGIC)

# Field order returned by _parse_csi_frame(); documentation only, no longer used for CSV output.
ESP_CSV_HEADER = [
    "timestamp",
    "type", "id", "mac", "rssi", "rate", "sig_mode", "mcs",
    "bandwidth", "smoothing", "not_sounding", "aggregation", "stbc",
    "fec_coding", "sgi", "noise_floor", "ampdu_cnt", "channel",
    "secondary_channel", "local_timestamp", "ant", "sig_len",
    "rx_state", "len", "first_word", "data",
]


# ──────────────────────────── Models ──────────────────────────────

# Detection models (must be defined before SnapshotResponse)
class SubcarrierSelection(BaseModel):
    indices: list[int]
    q_values: list[float]


class SignalQuality(BaseModel):
    interp_steps: int
    fallback_steps: int
    irregular_gaps: int
    nonpositive_gaps: int
    actual_pps: float
    window_duration_s: float


class DetectionInfo(BaseModel):
    state: str
    mv_current: float
    mv_threshold: float
    confidence: float
    cooldown_remaining_s: float
    last_fall_at: str | None
    just_triggered: bool
    selection: SubcarrierSelection
    quality: SignalQuality
    final_signal: list[float]
    pipeline_latency_ms: float
    updated_at: float
    presence_state: str
    wander_current: float
    wander_baseline: float
    wander_ratio_threshold: float
    wander_ratio: float
    wander_confirmed: bool
    last_activity_at: float | None
    presence_just_changed: bool


class DetectionConfigUpdate(BaseModel):
    window_sec: float | None = None
    stride_sec: float | None = None
    fs_hz: float | None = None
    mv_window_sec: float | None = None
    n_streams: int | None = None
    bandpass_low: float | None = None
    bandpass_high: float | None = None
    bandpass_order: int | None = None
    mv_threshold: float | None = None
    min_duration_s: float | None = None
    merge_gap_s: float | None = None
    max_duration_s: float | None = None
    cooldown_s: float | None = None
    wander_window_sec: float | None = None
    wander_mv_window_sec: float | None = None
    wander_prefilter_low: float | None = None
    wander_prefilter_high: float | None = None
    wander_bandpass_low: float | None = None
    wander_bandpass_high: float | None = None
    wander_baseline: float | None = None
    wander_ratio_threshold: float | None = None
    wander_min_duration_s: float | None = None
    presence_timeout_s: float | None = None


# Request/response models
class StatusResponse(BaseModel):
    running: bool
    port: str | None = None
    packet_count: int = 0
    error: str | None = None


class SnapshotResponse(BaseModel):
    running: bool
    packet_count: int = 0
    detection: DetectionInfo | None = None


class StartRequest(BaseModel):
    port: str


# Onboarding models
class OnboardingStatus(BaseModel):
    onboarded: bool
    step: int
    service_type: str | None
    device_name: str | None
    room_name: str | None


class ServiceRequest(BaseModel):
    service_type: str


class DeviceRequest(BaseModel):
    device_name: str
    room_name: str


class CalibrationStatusResponse(BaseModel):
    phase: str
    elapsed_s: float
    phase_elapsed_s: float
    agc_duration_s: float | None
    mv_threshold: float | None
    wander_baseline: float | None
    error: str | None


def detection_output_to_info(output: DetectionOutput) -> DetectionInfo:
    """Convert pipeline DetectionOutput to API DetectionInfo."""
    return DetectionInfo(
        state=output.state,
        mv_current=output.mv_current,
        mv_threshold=output.mv_threshold,
        confidence=output.confidence,
        cooldown_remaining_s=output.cooldown_remaining_s,
        last_fall_at=output.last_fall_at,
        just_triggered=output.just_triggered,
        selection=SubcarrierSelection(
            indices=output.selected_indices,
            q_values=output.q_values,
        ),
        quality=SignalQuality(
            interp_steps=output.interp_steps,
            fallback_steps=output.fallback_steps,
            irregular_gaps=output.irregular_gaps,
            nonpositive_gaps=output.nonpositive_gaps,
            actual_pps=output.actual_pps,
            window_duration_s=output.window_duration_s,
        ),
        final_signal=output.final_signal,
        pipeline_latency_ms=output.pipeline_latency_ms,
        updated_at=float(output.updated_at) if isinstance(output.updated_at, (int, float)) else time.time(),
        presence_state=output.presence_state,
        wander_current=output.wander_current,
        wander_baseline=output.wander_baseline,
        wander_ratio_threshold=output.wander_ratio_threshold,
        wander_ratio=output.wander_ratio,
        wander_confirmed=output.wander_confirmed,
        last_activity_at=output.last_activity_at,
        presence_just_changed=output.presence_just_changed,
    )


# ──────────────── ESP32 binary‑frame parsing ──────────────────────
def _extract_binary_frames(buf: bytearray) -> list[bytes]:
    frames: list[bytes] = []
    while True:
        start = buf.find(BINARY_MAGIC_BYTES)
        if start < 0:
            if len(buf) > BINARY_FRAME_HEADER_SIZE:
                del buf[:-BINARY_FRAME_HEADER_SIZE]
            break
        if start > 0:
            del buf[:start]
        if len(buf) < BINARY_FRAME_HEADER_SIZE:
            break
        frame_len = struct.unpack_from("<H", buf, 2)[0]
        if (
            frame_len < BINARY_FRAME_HEADER_SIZE + BINARY_FRAME_CHECKSUM_SIZE
            or frame_len > BINARY_FRAME_MAX_SIZE
        ):
            del buf[:2]
            continue
        if len(buf) < frame_len:
            break
        frames.append(bytes(buf[:frame_len]))
        del buf[:frame_len]
    return frames


def _parse_csi_frame(frame: bytes) -> list | None:
    hdr = struct.unpack_from(BINARY_FRAME_HEADER_FORMAT, frame, 0)
    frame_len, version, frame_type = hdr[1], hdr[2], hdr[3]
    csi_len = hdr[27]

    if version != BINARY_FRAME_VERSION or frame_type != BINARY_FRAME_TYPE_CSI:
        return None
    if csi_len + BINARY_FRAME_HEADER_SIZE + BINARY_FRAME_CHECKSUM_SIZE != frame_len:
        return None
    ck_exp = struct.unpack_from(
        "<H", frame, frame_len - BINARY_FRAME_CHECKSUM_SIZE)[0]
    if (sum(frame[:-BINARY_FRAME_CHECKSUM_SIZE]) & 0xFFFF) != ck_exp:
        return None

    payload = frame[BINARY_FRAME_HEADER_SIZE: BINARY_FRAME_HEADER_SIZE + csi_len]
    csi_raw = list(struct.unpack(f"<{csi_len}b", payload))

    (
        _, _, _, _, seq, mac_bytes, rssi, rate,
        sig_mode, mcs, bandwidth, smoothing, not_sounding, aggregation, stbc,
        fec_coding, sgi, noise_floor, ampdu_cnt, channel, secondary_channel,
        ant, sig_len, rx_state, local_timestamp, _fft, _agc, csi_len_v,
        first_word_invalid, _reserved,
    ) = hdr
    mac = ":".join(f"{b:02x}" for b in mac_bytes)
    return [
        "CSI_DATA", seq, mac, rssi, rate, sig_mode, mcs, bandwidth, smoothing,
        not_sounding, aggregation, stbc, fec_coding, sgi, noise_floor,
        ampdu_cnt, channel, secondary_channel, local_timestamp, ant, sig_len,
        rx_state, csi_len_v, first_word_invalid, json.dumps(csi_raw),
    ]


# ──────────────────────── EspMonitor ────────────────────────────
class EspMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._packet_count = 0
        self._port: str | None = None
        self._last_error: str | None = None
        self._open_confirmed = threading.Event()
        self._serial: serial.Serial | None = None

        # CSI buffer: ring buffer of (unwrapped_ts_us, amp_vector). maxlen=2500
        # (~25s @ 100Hz) gives headroom over the 20s onboarding calibration
        # baseline capture (src/onboarding.py's default baseline_window_s).
        self._csi_buffer = deque(maxlen=2500)

        # Timestamp unwrap state (local_timestamp is uint32, wraps at 2^32 µs)
        self._last_raw_ts: int | None = None
        self._ts_offset: int = 0
        self._ts_wrap_threshold = 2**31

        # Detection result storage (separate lock to avoid blocking ingest)
        self._detection_lock = threading.Lock()
        self._detection_result: DetectionInfo | None = None

        # Diagnostic counters
        self._bytes_received = 0
        self._frames_parsed = 0
        self._frames_rejected = 0
        self._last_summary_log = time.monotonic()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def packet_count(self) -> int:
        with self._lock:
            return self._packet_count

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._last_error

    def get_window(self, window_sec: float) -> tuple[np.ndarray, np.ndarray] | None:
        """
        Get a trailing window of CSI data from the buffer.

        Args:
            window_sec: Window duration in seconds.

        Returns:
            (timestamps_us, amp_2d) or None if insufficient data.
            timestamps_us: (N,) microsecond timestamps
            amp_2d: (N, n_subcarriers) amplitude array
        """
        with self._lock:
            if len(self._csi_buffer) < 10:
                return None
            snapshot = list(self._csi_buffer)

        # Find entries within the trailing window_sec
        window_us = window_sec * 1e6
        if len(snapshot) == 0:
            return None

        latest_ts = snapshot[-1][0]
        cutoff_ts = latest_ts - window_us
        windowed = [(ts, amp) for ts, amp in snapshot if ts >= cutoff_ts]

        if len(windowed) < 10:
            return None

        ts_array = np.array([ts for ts, _ in windowed], dtype=np.int64)
        amp_list = [amp for _, amp in windowed]

        # Stack amplitude vectors; guard against subcarrier-length mismatch
        try:
            amp_2d = np.stack(amp_list, axis=0).astype(np.float32)
        except ValueError:
            # Subcarrier count mismatch; use only the longest matching sequence
            n_sc_counts = {}
            for amp in amp_list:
                n_sc = len(amp)
                n_sc_counts[n_sc] = n_sc_counts.get(n_sc, 0) + 1
            if not n_sc_counts:
                return None
            dominant_n_sc = max(n_sc_counts, key=n_sc_counts.get)
            amp_filtered = [a for a in amp_list if len(a) == dominant_n_sc]
            ts_filtered = ts_array[-len(amp_filtered):]
            amp_2d = np.stack(amp_filtered, axis=0).astype(np.float32)
            ts_array = ts_filtered

        return ts_array, amp_2d

    def set_detection_result(self, result: DetectionInfo | DetectionOutput):
        """Store the latest detection result. Converts DetectionOutput to DetectionInfo if needed."""
        if isinstance(result, DetectionOutput):
            result = detection_output_to_info(result)
        with self._detection_lock:
            self._detection_result = result

    def get_detection_result(self) -> DetectionInfo | None:
        """Retrieve the latest detection result."""
        with self._detection_lock:
            return self._detection_result

    def start(self, port: str) -> None:
        with self._lock:
            if self._running:
                return
            self._port = port
            self._packet_count = 0
            self._last_error = None
            self._stop_event.clear()
            self._open_confirmed.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop, name="esp-monitor", daemon=True
            )
            self._thread.start()
        self._open_confirmed.wait(timeout=2.0)

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def send_line(self, text: str) -> bool:
        """Write a line (with trailing newline) to the open serial port, e.g. the
        firmware's "train" calibration command. Returns False if not connected."""
        with self._lock:
            ser, running = self._serial, self._running
        if not ser or not running:
            return False
        try:
            ser.write((text + "\n").encode("ascii"))
            return True
        except serial.SerialException as exc:
            with self._lock:
                self._last_error = f"serial write failed: {exc}"
            return False

    def _run_loop(self) -> None:
        try:
            ser = serial.Serial(
                port=self._port, baudrate=921600,
                bytesize=8, parity="N", stopbits=1, timeout=0.1,
            )
            logger.info("serial port %s opened successfully (baud=921600)", self._port)
            with self._lock:
                self._serial = ser
            self._open_confirmed.set()
        except Exception as exc:
            msg = f"serial open failed on {self._port}: {exc}"
            logger.error(msg)
            with self._lock:
                self._last_error = msg
                self._running = False
            self._open_confirmed.set()
            return

        bq: queue.Queue[bytes] = queue.Queue(maxsize=SERIAL_QUEUE_MAXSIZE)
        serial_buf = bytearray()

        def _reader() -> None:
            while not self._stop_event.is_set():
                try:
                    w = ser.in_waiting
                    data = ser.read(w if w > 0 else 1)
                except serial.SerialException:
                    break
                if not data:
                    continue
                try:
                    bq.put_nowait(data)
                except queue.Full:
                    try:
                        bq.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        bq.put_nowait(data)
                    except queue.Full:
                        pass

        reader_t = threading.Thread(target=_reader, daemon=True)
        reader_t.start()

        try:
            while not self._stop_event.is_set():
                try:
                    chunk = bq.get(timeout=QUEUE_READ_TIMEOUT_SEC)
                except queue.Empty:
                    if not reader_t.is_alive():
                        with self._lock:
                            self._last_error = "serial reader thread exited unexpectedly (device disconnected?)"
                        break
                    continue

                with self._lock:
                    self._bytes_received += len(chunk)
                serial_buf.extend(chunk)
                for frame in _extract_binary_frames(serial_buf):
                    row = _parse_csi_frame(frame)
                    if row is None:
                        with self._lock:
                            self._frames_rejected += 1
                        continue
                    with self._lock:
                        self._frames_parsed += 1
                    # Compute CSI amplitudes from raw IQ bytes (interleaved imag,real)
                    try:
                        raw: list[int] = json.loads(row[-1])
                        amps = [
                            (raw[i] ** 2 + raw[i + 1] ** 2) ** 0.5
                            for i in range(0, len(raw) - 1, 2)
                        ]
                    except Exception:
                        amps = []

                    # Unwrap the hardware timestamp (row[18] is local_timestamp, uint32 microseconds)
                    raw_ts = int(row[18])
                    if self._last_raw_ts is not None and raw_ts < self._last_raw_ts - self._ts_wrap_threshold:
                        self._ts_offset += 2**32
                    self._last_raw_ts = raw_ts
                    unwrapped_ts = self._ts_offset + raw_ts

                    with self._lock:
                        # Add to CSI buffer
                        self._csi_buffer.append((unwrapped_ts, np.asarray(amps, dtype=np.float32)))
                        self._packet_count += 1

                # Throttled periodic diagnostic summary (~10s)
                now = time.monotonic()
                if now - self._last_summary_log >= 10.0:
                    with self._lock:
                        logger.info(
                            "diagnostics: bytes_received=%d frames_parsed=%d frames_rejected=%d packet_count=%d",
                            self._bytes_received, self._frames_parsed, self._frames_rejected, self._packet_count,
                        )
                        self._bytes_received = 0
                        self._frames_parsed = 0
                        self._frames_rejected = 0
                        self._last_summary_log = now
        finally:
            if ser.is_open:
                ser.close()
            reader_t.join(timeout=1.0)
            with self._lock:
                self._running = False
                self._serial = None


# ──────────────────────────── FastAPI ──────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    monitor = EspMonitor()
    app.state.monitor = monitor

    # Initialize detection pipeline config and executor
    app.state.detection_config = PipelineConfig()
    app.state.executor = ThreadPoolExecutor(max_workers=8)
    app.state.detection_stop_event = asyncio.Event()

    # Onboarding/session state (in-memory only, resets on restart)
    app.state.onboarding = OnboardingState()
    app.state.calibration_task = None

    # Start the detection loop
    app.state.detection_task = asyncio.create_task(
        detection_loop(monitor, app.state.detection_config, app.state.executor, app.state.detection_stop_event)
    )

    try:
        yield
    finally:
        # Shutdown detection loop
        app.state.detection_stop_event.set()
        try:
            await asyncio.wait_for(app.state.detection_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        # Cancel any in-flight onboarding calibration
        if app.state.calibration_task is not None:
            app.state.calibration_task.cancel()

        # Shutdown executor and monitor
        app.state.executor.shutdown(wait=False)
        monitor.stop()


app = FastAPI(title="Real-Time Fall Detection Server",
              version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/monitor/start", response_model=StatusResponse)
async def start_monitor(payload: StartRequest) -> StatusResponse:
    monitor: EspMonitor = app.state.monitor
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, monitor.start, payload.port)
    return StatusResponse(
        running=monitor.running,
        port=monitor._port if monitor.running else None,
        packet_count=monitor.packet_count,
        error=monitor.error,
    )


@app.post("/monitor/stop", response_model=StatusResponse)
async def stop_monitor() -> StatusResponse:
    monitor: EspMonitor = app.state.monitor
    monitor.stop()
    return StatusResponse(
        running=monitor.running,
        port=monitor._port if monitor.running else None,
        packet_count=monitor.packet_count,
        error=monitor.error,
    )


@app.get("/monitor/status", response_model=StatusResponse)
async def monitor_status() -> StatusResponse:
    monitor: EspMonitor = app.state.monitor
    return StatusResponse(
        running=monitor.running,
        port=monitor._port if monitor.running else None,
        packet_count=monitor.packet_count,
        error=monitor.error,
    )


@app.get("/monitor/snapshot", response_model=SnapshotResponse)
async def monitor_snapshot() -> SnapshotResponse:
    monitor: EspMonitor = app.state.monitor
    detection_result = monitor.get_detection_result()
    return SnapshotResponse(
        running=monitor.running,
        packet_count=monitor.packet_count,
        detection=detection_result,
    )


@app.get("/detection/config")
async def get_detection_config() -> dict:
    """Get current detection pipeline configuration."""
    cfg = app.state.detection_config
    return {
        "window_sec": cfg.window_sec,
        "stride_sec": cfg.stride_sec,
        "fs_hz": cfg.fs_hz,
        "mv_window_sec": cfg.mv_window_sec,
        "n_streams": cfg.n_streams,
        "bandpass_low": cfg.bandpass_low,
        "bandpass_high": cfg.bandpass_high,
        "bandpass_order": cfg.bandpass_order,
        "mv_threshold": cfg.mv_threshold,
        "min_duration_s": cfg.min_duration_s,
        "merge_gap_s": cfg.merge_gap_s,
        "max_duration_s": cfg.max_duration_s,
        "cooldown_s": cfg.cooldown_s,
        "wander_window_sec": cfg.wander_window_sec,
        "wander_mv_window_sec": cfg.wander_mv_window_sec,
        "wander_prefilter_low": cfg.wander_prefilter_low,
        "wander_prefilter_high": cfg.wander_prefilter_high,
        "wander_bandpass_low": cfg.wander_bandpass_low,
        "wander_bandpass_high": cfg.wander_bandpass_high,
        "wander_baseline": cfg.wander_baseline,
        "wander_ratio_threshold": cfg.wander_ratio_threshold,
        "wander_min_duration_s": cfg.wander_min_duration_s,
        "presence_timeout_s": cfg.presence_timeout_s,
    }


@app.post("/detection/config")
async def update_detection_config(update: DetectionConfigUpdate) -> dict:
    """Update detection pipeline configuration (partial update)."""
    cfg = app.state.detection_config
    if update.window_sec is not None:
        cfg.window_sec = update.window_sec
    if update.stride_sec is not None:
        cfg.stride_sec = update.stride_sec
    if update.fs_hz is not None:
        cfg.fs_hz = update.fs_hz
    if update.mv_window_sec is not None:
        cfg.mv_window_sec = update.mv_window_sec
    if update.n_streams is not None:
        cfg.n_streams = update.n_streams
    if update.bandpass_low is not None:
        cfg.bandpass_low = update.bandpass_low
    if update.bandpass_high is not None:
        cfg.bandpass_high = update.bandpass_high
    if update.bandpass_order is not None:
        cfg.bandpass_order = update.bandpass_order
    if update.mv_threshold is not None:
        cfg.mv_threshold = update.mv_threshold
    if update.min_duration_s is not None:
        cfg.min_duration_s = update.min_duration_s
    if update.merge_gap_s is not None:
        cfg.merge_gap_s = update.merge_gap_s
    if update.max_duration_s is not None:
        cfg.max_duration_s = update.max_duration_s
    if update.cooldown_s is not None:
        cfg.cooldown_s = update.cooldown_s
    if update.wander_window_sec is not None:
        cfg.wander_window_sec = update.wander_window_sec
    if update.wander_mv_window_sec is not None:
        cfg.wander_mv_window_sec = update.wander_mv_window_sec
    if update.wander_prefilter_low is not None:
        cfg.wander_prefilter_low = update.wander_prefilter_low
    if update.wander_prefilter_high is not None:
        cfg.wander_prefilter_high = update.wander_prefilter_high
    if update.wander_bandpass_low is not None:
        cfg.wander_bandpass_low = update.wander_bandpass_low
    if update.wander_bandpass_high is not None:
        cfg.wander_bandpass_high = update.wander_bandpass_high
    if update.wander_baseline is not None:
        cfg.wander_baseline = update.wander_baseline
    if update.wander_ratio_threshold is not None:
        cfg.wander_ratio_threshold = update.wander_ratio_threshold
    if update.wander_min_duration_s is not None:
        cfg.wander_min_duration_s = update.wander_min_duration_s
    if update.presence_timeout_s is not None:
        cfg.presence_timeout_s = update.presence_timeout_s
    return {"status": "ok", "config": await get_detection_config()}


@app.get("/onboarding/status", response_model=OnboardingStatus)
async def onboarding_status() -> OnboardingStatus:
    ob: OnboardingState = app.state.onboarding
    return OnboardingStatus(
        onboarded=ob.onboarded,
        step=ob.step,
        service_type=ob.service_type,
        device_name=ob.device_name,
        room_name=ob.room_name,
    )


@app.post("/onboarding/service", response_model=OnboardingStatus)
async def onboarding_service(payload: ServiceRequest) -> OnboardingStatus:
    ob: OnboardingState = app.state.onboarding
    ob.service_type = payload.service_type
    ob.step = max(ob.step, 2)
    return await onboarding_status()


@app.post("/onboarding/device", response_model=OnboardingStatus)
async def onboarding_device(payload: DeviceRequest) -> OnboardingStatus:
    monitor: EspMonitor = app.state.monitor
    if not monitor.running:
        raise HTTPException(status_code=400, detail="device is not connected (call /monitor/start first)")
    ob: OnboardingState = app.state.onboarding
    ob.device_name = payload.device_name
    ob.room_name = payload.room_name
    ob.step = max(ob.step, 3)
    return await onboarding_status()


@app.post("/onboarding/calibrate/start")
async def onboarding_calibrate_start() -> dict:
    monitor: EspMonitor = app.state.monitor
    if not monitor.running:
        raise HTTPException(status_code=400, detail="device is not connected")
    ob: OnboardingState = app.state.onboarding
    if ob.calibration.phase in ("waiting_ack", "waiting_agc", "measuring"):
        raise HTTPException(status_code=400, detail="calibration already in progress")
    app.state.calibration_task = asyncio.create_task(
        run_calibration(monitor, app.state.detection_config, ob, app.state.executor)
    )
    return {"status": "started"}


@app.get("/onboarding/calibrate/status", response_model=CalibrationStatusResponse)
async def onboarding_calibrate_status() -> CalibrationStatusResponse:
    ob: OnboardingState = app.state.onboarding
    calib = ob.calibration
    elapsed = (time.time() - calib.started_at) if calib.started_at is not None else 0.0
    phase_elapsed = (time.time() - calib.phase_started_at) if calib.phase_started_at is not None else 0.0
    return CalibrationStatusResponse(
        phase=calib.phase,
        elapsed_s=elapsed,
        phase_elapsed_s=phase_elapsed,
        agc_duration_s=calib.agc_duration_s,
        mv_threshold=calib.mv_threshold,
        wander_baseline=calib.wander_baseline,
        error=calib.error,
    )


@app.post("/onboarding/complete", response_model=OnboardingStatus)
async def onboarding_complete() -> OnboardingStatus:
    ob: OnboardingState = app.state.onboarding
    if ob.calibration.phase != "done":
        raise HTTPException(status_code=400, detail="calibration has not completed successfully yet")
    ob.onboarded = True
    ob.step = 4
    return await onboarding_status()


@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket) -> None:
    await ws.accept()
    monitor: EspMonitor = app.state.monitor
    try:
        while True:
            detection_result = monitor.get_detection_result()
            snapshot = SnapshotResponse(
                running=monitor.running,
                packet_count=monitor.packet_count,
                detection=detection_result,
            )
            await ws.send_json(snapshot.model_dump())
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        return


if __name__ == "__main__":
    import argparse
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Real-time fall detection server for ESP32-C5")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
