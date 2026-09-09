# Real-time Fall Detection MVP

ESP32-C5 CSI-based real-time fall detection system with FastAPI backend and browser dashboard.

## Quick Start

### 1. Install Dependencies

```bash
cd tools/fall_detect
pip install -r requirements.txt
```

### 2. Run the Server

```bash
python main.py --host 0.0.0.0 --port 8000
```

The server will start at `http://localhost:8000`.

### 3. Connect Two ESP32-C5 Boards

This system requires **two** physical ESP32-C5 boards — CSI data will never appear with only one connected:

1. **Receiver** (the one connected to this PC via USB): flash `csi_recv/` onto it. This is the "single port" the dashboard connects to.
2. **Transmitter** (a separate board, battery/USB powered, does NOT need to be connected to this PC): flash `csi_send/` onto it and power it on. It broadcasts ESP-NOW packets at 100Hz that the receiver captures as CSI.

Both boards must be on the **same WiFi channel**: **5GHz channel 48** (`CONFIG_LESS_INTERFERENCE_CHANNEL = 48` in both firmwares' `app_main.c`). The transmitter spoofs its MAC to `1a:00:00:00:00:00`, and the receiver filters incoming CSI to that exact MAC (`csi_recv/main/app_main.c`) — if the transmitter isn't running, isn't in range, or is on a mismatched channel, the receiver's `wifi_csi_rx_cb` callback never fires and **zero UART frames are produced**, independent of whether this Python app is correctly configured.

Once both boards are running:
- Open the dashboard at `http://localhost:8000`.
- Enter the **receiver's** serial port (e.g., `COM3` or `/dev/ttyUSB0`) in the port field.
- Click **시작 (Start)** to begin monitoring.

### 4. Trigger Fall Detection

- **Shake the board** or wave a hand near the antenna to generate CSI disturbance.
- Watch the **CSI Amplitude Heatmap** update in real-time.
- When motion is sustained for ~0.5 seconds, the **Fall Status** will change to orange (SUSPECT) then red (FALL).
- An **alarm popup** will appear when a fall is detected.
- The system auto-resets to idle after ~3 seconds (cooldown).

## Configuration

### Live Parameter Tuning

Adjust detection sensitivity without restarting the server:

```bash
# Get current config
curl http://localhost:8000/detection/config

# Update threshold (example: lower threshold = more sensitive)
curl -X POST http://localhost:8000/detection/config \
  -H "Content-Type: application/json" \
  -d '{"mv_threshold": 0.5}'
```

### Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mv_threshold` | 2.0 | Moving-variance threshold for fall trigger |
| `mv_window_sec` | 0.5 | Moving-variance window duration (seconds) |
| `min_duration_s` | 0.5 | Minimum time above threshold to confirm fall |
| `merge_gap_s` | 0.25 | Max gap to merge consecutive detections |
| `max_duration_s` | 2.0 | Hard cap on fall event duration |
| `cooldown_s` | 3.0 | Lockout duration after fall detection |
| `window_sec` | 3.0 | CSI sliding window duration |
| `stride_sec` | 0.5 | Pipeline update interval |
| `fs_hz` | 100.0 | Resampling target frequency |
| `n_streams` | 10 | Number of top subcarriers to select |
| `bandpass_low` | 0.5 | Bandpass lower cutoff (Hz) |
| `bandpass_high` | 50.0 | Bandpass upper cutoff (Hz) |

## API Endpoints

### Monitor Control
- `POST /monitor/start` — Start monitoring
- `POST /monitor/stop` — Stop monitoring
- `GET /monitor/status` — Get monitor status
- `GET /monitor/snapshot` — Get latest CSI snapshot

### Detection Configuration
- `GET /detection/config` — Get current pipeline config
- `POST /detection/config` — Update pipeline config (partial update)

### Live Streaming
- `WS /ws/live` — WebSocket for real-time updates (packet counts, CSI amplitudes, detection results)

## Dashboard

The browser dashboard displays:

- **Left Column**:
  - Port configuration and start/stop controls
  
- **Right Column**:
  - **CSI Amplitude Heatmap**: Raw subcarrier amplitudes over time (scrolling buffer)
  - **Fall Detection Status**: Color-coded state badge (idle/suspect/fall/cooldown)
  - **Confidence Bar**: Visual indicator of detection confidence
  - **Signal Quality**: Packet rate (PPS) and gap count

- **Alarm Modal**: Popup displayed when fall is detected; auto-dismisses after 5 seconds

## Signal Processing Pipeline

Every 0.5 seconds:

1. **Pull 3-second trailing CSI window** from the ring buffer
2. **Resample** to regular 100Hz grid (linear interpolation, handles dropped packets)
3. **Bandpass filter** 2–50Hz (Butterworth, zero-phase)
4. **Select top-10 subcarriers** by q-value (activity sensitivity metric)
5. **Sum and normalize** selected subcarriers (z-score)
6. **Compute moving variance** with 0.5-second window
7. **Threshold & state machine**: detect sustained above-threshold activity as fall
8. **Push results** to dashboard via WebSocket

## Tuning for Your Environment

### If the system is too sensitive (false positives):
```bash
curl -X POST http://localhost:8000/detection/config \
  -H "Content-Type: application/json" \
  -d '{"mv_threshold": 2.0, "min_duration_s": 1.0}'
```

### If the system is not sensitive enough (missing falls):
```bash
curl -X POST http://localhost:8000/detection/config \
  -H "Content-Type: application/json" \
  -d '{"mv_threshold": 0.5, "min_duration_s": 0.3}'
```

## Troubleshooting

### No CSI data appearing (packet count stuck at 0)

Work through these in order:

1. **Is the transmitter board powered on and in range?** The receiver alone produces zero CSI — you need a second ESP32-C5 flashed with `csi_send/` and powered on nearby. This is the most common cause.
2. **Check `GET /monitor/status` (or the dashboard's status pill) for the `error` field.** If `error` is non-null, the serial port itself failed to open (wrong port name, port in use by another program, permissions) — fix that first; no CSI can flow until the port opens.
3. **Check the server console log.** On successful port open you should see `serial port <PORT> opened successfully (baud=921600)`. Every ~10 seconds while running, a diagnostic line reports `bytes_received`, `frames_parsed`, `frames_rejected`, and `packet_count`:
   - `bytes_received` stuck at 0 → no data arriving over serial at all → check USB cable/receiver firmware/receiver power, independent of the transmitter.
   - `bytes_received` climbing but `frames_parsed` at 0 → bytes are arriving but not recognized as valid CSI frames → check receiver firmware version matches the protocol this app expects (magic `0xA55A`), or check for a wrong baud rate.
   - `bytes_received` and `frames_parsed` both climbing but `packet_count` (in `/monitor/status`) stays at 0 → should not happen (parsed frames always increment packet_count) — file a bug if seen.
   - No diagnostic line appears at all after 10+ seconds → the reader thread may have died silently; check for a "serial reader thread exited unexpectedly" log line, which usually means the device was unplugged.
4. **Confirm the two boards are on the same channel.** Both must be on 5GHz channel 48 (or both edited consistently to 2.4GHz channel 11) — see the hardware setup section above.
5. **Verify baud rate**: should be **921600**, 8N1 — this is hardcoded on both the firmware and Python sides and should not normally need changing.

### Threshold seems wrong
- Calibrate by shaking the device and observing the **moving-variance value** in the dashboard
- Adjust `mv_threshold` to approximately 50–70% of the max observed variance during test motion

### WebSocket connection drops
- Check firewall settings; port 8000 must be accessible
- Refresh the browser page to reconnect

## Files

- `main.py` — FastAPI server, real-time CSI receiver and fall detection engine
- `src/streaming_features.py` — Signal processing (resample, filter, feature extraction)
- `src/fall_state_machine.py` — Threshold-based state machine
- `src/pipeline.py` — Real-time pipeline orchestration
- `static/index.html` — Browser monitoring dashboard
- `requirements.txt` — Python dependencies

## References

- **ESP-IDF CSI Firmware**: `csi_recv/main/app_main.c` (100pps, single antenna)
- **Reference Algorithm**: `src/preprocessing.py` (amfall-based pipeline)
- **Offline Analysis**: `src/true_activity_time.py` (batch segment extraction, algorithm reference only)
