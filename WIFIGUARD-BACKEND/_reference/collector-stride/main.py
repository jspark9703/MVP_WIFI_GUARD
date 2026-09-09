import asyncio
import csv
import json
import re
import struct
import threading
import time
import queue
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import serial
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

CURRENT_DIR = Path(__file__).resolve().parent
STATIC_DIR = CURRENT_DIR / "static"
DATA_DIR = CURRENT_DIR / "data"

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

ESP_CSV_HEADER = [
    "timestamp",
    "type", "id", "mac", "rssi", "rate", "sig_mode", "mcs",
    "bandwidth", "smoothing", "not_sounding", "aggregation", "stbc",
    "fec_coding", "sgi", "noise_floor", "ampdu_cnt", "channel",
    "secondary_channel", "local_timestamp", "ant", "sig_len",
    "rx_state", "fft_gain", "agc_gain", "len", "first_word", "data",
]


# ──────────────────────────── Models ──────────────────────────────
class StatusResponse(BaseModel):
    running: bool
    esp_ports: dict[str, str | None] = Field(default_factory=dict)
    esp_paths: dict[str, str | None] = Field(default_factory=dict)
    esp_packet_counts: dict[str, int] = Field(default_factory=dict)


class SnapshotResponse(BaseModel):
    running: bool
    esp_packet_counts: dict[str, int] = Field(default_factory=dict)
    csi_amplitudes: dict[str, list[float]] = Field(default_factory=dict)


class StartRequest(BaseModel):
    esp_ports: list[str]
    file_suffix: str | None = None
    prefix_folder: str | None = None


class DataSession(BaseModel):
    session_id: str
    time_tag: str
    suffix: str | None = None
    esp_paths: dict[str, str] = Field(default_factory=dict)


# ──────────────────────────── Helpers ─────────────────────────────
def _sanitize(s: str | None) -> str:
    if not s:
        return ""
    out = re.sub(r'[\\:*?"<>|]+', "_", s).strip()
    out = re.sub(r"\s+", "_", out)
    out = re.sub(r"_+", "_", out).strip("_")
    return "" if out in {"", ".", ".."} else out


def _sanitize_prefix(s: str | None) -> str:
    if not s:
        return ""
    segments = s.replace("\\", "/").split("/")
    clean = [_sanitize(seg) for seg in segments if seg not in ("", ".", "..")]
    return "/".join(clean)


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
        ant, sig_len, rx_state, local_timestamp, fft_gain, agc_gain, csi_len_v,
        first_word_invalid, _reserved,
    ) = hdr
    mac = ":".join(f"{b:02x}" for b in mac_bytes)
    return [
        "CSI_DATA", seq, mac, rssi, rate, sig_mode, mcs, bandwidth, smoothing,
        not_sounding, aggregation, stbc, fec_coding, sgi, noise_floor,
        ampdu_cnt, channel, secondary_channel, local_timestamp, ant, sig_len,
        rx_state, fft_gain, agc_gain, csi_len_v, first_word_invalid, json.dumps(csi_raw),
    ]


# ──────────────────────── EspCollector ────────────────────────────
class EspCollector:
    def __init__(self, label: str = "esp") -> None:
        self._label = label
        self._lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._csv_file: Any = None
        self._csv_writer: Any = None
        self._output_path: Path | None = None
        self._packet_count = 0
        self._port: str | None = None
        self._csi_amplitudes: list[float] = []

    @property
    def running(self) -> bool:
        return self._running

    @property
    def packet_count(self) -> int:
        with self._lock:
            return self._packet_count

    @property
    def csi_amplitudes(self) -> list[float]:
        with self._lock:
            return self._csi_amplitudes[:]

    @property
    def output_path(self) -> str | None:
        with self._lock:
            return str(self._output_path) if self._output_path else None

    def start(self, port: str, csv_path: Path) -> None:
        with self._lock:
            if self._running:
                return
            self._port = port
            self._output_path = csv_path
            self._packet_count = 0
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            self._csv_file = open(csv_path, "w", newline="", encoding="utf-8")
            self._csv_writer = csv.writer(self._csv_file)
            self._csv_writer.writerow(ESP_CSV_HEADER)
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._collect_loop, name="esp-collector", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            if self._csv_file:
                try:
                    self._csv_file.flush()
                    self._csv_file.close()
                except Exception:
                    pass
                self._csv_file = None
                self._csv_writer = None

    def _collect_loop(self) -> None:
        try:
            ser = serial.Serial(
                port=self._port, baudrate=921600,
                bytesize=8, parity="N", stopbits=1, timeout=0.1,
            )
        except Exception as exc:
            print(f"[{self._label}] serial open failed: {exc}")
            with self._lock:
                self._running = False
            return

        bq: queue.Queue[bytes] = queue.Queue(maxsize=SERIAL_QUEUE_MAXSIZE)
        serial_buf = bytearray()
        flush_ts = time.monotonic()

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
                        break
                    continue

                serial_buf.extend(chunk)
                for frame in _extract_binary_frames(serial_buf):
                    row = _parse_csi_frame(frame)
                    if row is None:
                        continue
                    ts = datetime.now().isoformat(timespec="milliseconds")
                    # Compute CSI amplitudes from raw IQ bytes (interleaved imag,real)
                    try:
                        raw: list[int] = json.loads(row[-1])
                        amps = [
                            (raw[i] ** 2 + raw[i + 1] ** 2) ** 0.5
                            for i in range(0, len(raw) - 1, 2)
                        ]
                    except Exception:
                        amps = []
                    with self._lock:
                        if self._csv_writer:
                            self._csv_writer.writerow([ts] + row)
                            self._packet_count += 1
                        self._csi_amplitudes = amps

                now = time.monotonic()
                if now - flush_ts >= 0.5:
                    with self._lock:
                        if self._csv_file:
                            self._csv_file.flush()
                    flush_ts = now
        finally:
            if ser.is_open:
                ser.close()
            reader_t.join(timeout=1.0)
            with self._lock:
                self._running = False


# ──────────────────────── MultiEspCollector ─────────────────────────
class MultiEspCollector:
    def __init__(self) -> None:
        self.esp: dict[str, EspCollector] = {}

    @property
    def running(self) -> bool:
        return any(c.running for c in self.esp.values())

    async def start(self, esp_ports: list[str], file_suffix: str | None = None, prefix_folder: str | None = None) -> StatusResponse:
        if self.running:
            return self.status()

        if len(esp_ports) == 0:
            raise ValueError("At least one ESP port must be provided")

        if len(esp_ports) != len(set(esp_ports)):
            raise ValueError("All ESP ports must be unique")

        now = datetime.now()
        time_tag = now.strftime("%y%m%d_%H%M%S")
        sfx = _sanitize(file_suffix)
        sfx_part = f"_{sfx}" if sfx else ""

        prefix = _sanitize_prefix(prefix_folder)
        base_dir = DATA_DIR / prefix if prefix else DATA_DIR

        self.esp = {}
        for idx, port in enumerate(esp_ports, 1):
            label = f"esp{idx}"
            esp_path = base_dir / f"esp32_{idx}" / f"csi_{time_tag}{sfx_part}.csv"
            collector = EspCollector(label)
            collector.start(port, esp_path)
            self.esp[label] = collector

        return self.status()

    async def stop(self) -> StatusResponse:
        for c in self.esp.values():
            c.stop()
        return self.status()

    def status(self) -> StatusResponse:
        return StatusResponse(
            running=self.running,
            esp_ports={label: c._port if c.running else None for label, c in self.esp.items()},
            esp_paths={label: c.output_path for label, c in self.esp.items()},
            esp_packet_counts={label: c.packet_count for label, c in self.esp.items()},
        )

    def snapshot(self) -> SnapshotResponse:
        return SnapshotResponse(
            running=self.running,
            esp_packet_counts={label: c.packet_count for label, c in self.esp.items()},
            csi_amplitudes={label: c.csi_amplitudes for label, c in self.esp.items()},
        )

    async def close(self) -> None:
        for c in self.esp.values():
            c.stop()


# ──────────────────────────── FastAPI ──────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    collector = MultiEspCollector()
    app.state.collector = collector
    try:
        yield
    finally:
        await collector.close()


app = FastAPI(title="CSI Dual-Port Collector",
              version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/collector/start", response_model=StatusResponse)
async def start_collector(payload: StartRequest) -> StatusResponse:
    collector: MultiEspCollector = app.state.collector
    try:
        return await collector.start(
            esp_ports=payload.esp_ports,
            file_suffix=payload.file_suffix,
            prefix_folder=payload.prefix_folder,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/collector/stop", response_model=StatusResponse)
async def stop_collector() -> StatusResponse:
    collector: MultiEspCollector = app.state.collector
    return await collector.stop()


@app.get("/collector/status", response_model=StatusResponse)
async def collector_status() -> StatusResponse:
    collector: MultiEspCollector = app.state.collector
    return collector.status()


@app.get("/collector/snapshot", response_model=SnapshotResponse)
async def collector_snapshot() -> SnapshotResponse:
    collector: MultiEspCollector = app.state.collector
    return collector.snapshot()


def _list_sessions() -> list[DataSession]:
    sessions: dict[str, DataSession] = {}

    if DATA_DIR.exists():
        for esp_dir in sorted(DATA_DIR.glob("esp32_*")):
            if esp_dir.is_dir():
                esp_label = esp_dir.name.replace("esp32_", "esp")
                for f in sorted(esp_dir.glob("csi_*.csv")):
                    name = f.stem[4:]  # strip "csi_"
                    time_tag = name[:13]
                    suffix_part = name[13:].lstrip("_") or None
                    sid = time_tag + (f"_{suffix_part}" if suffix_part else "")
                    if sid not in sessions:
                        sessions[sid] = DataSession(
                            session_id=sid, time_tag=time_tag, suffix=suffix_part
                        )
                    sessions[sid].esp_paths[esp_label] = str(f)

    return sorted(sessions.values(), key=lambda s: s.time_tag, reverse=True)


@app.get("/data/list", response_model=list[DataSession])
async def data_list() -> list[DataSession]:
    return _list_sessions()


@app.delete("/data/{session_id}")
async def data_delete(session_id: str) -> dict[str, str]:
    sessions = {s.session_id: s for s in _list_sessions()}
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    s = sessions[session_id]
    deleted = []
    for path_str in s.esp_paths.values():
        p = Path(path_str)
        if p.exists():
            p.unlink()
            deleted.append(path_str)
    return {"deleted": str(len(deleted)), "files": ", ".join(deleted)}


@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket) -> None:
    await ws.accept()
    collector: MultiEspCollector = app.state.collector
    try:
        while True:
            await ws.send_json(collector.snapshot().model_dump())
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        return


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(
        description="Dual-port CSI collector for ESP32-C5")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
