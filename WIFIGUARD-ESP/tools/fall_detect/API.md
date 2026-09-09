# Fall Detection MVP — API Reference

Complete REST API and WebSocket endpoint documentation for the real-time fall detection system.

## Base URL

```
http://localhost:8000
```

## Authentication

No authentication required (local development MVP).

---

## Health & Index

### `GET /`
Serves the browser dashboard (static/index.html).

**Response**: HTML page

---

### `GET /health`
Health check endpoint.

**Response**:
```json
{
  "status": "ok"
}
```

---

## Monitor Control

### `POST /monitor/start`
Start monitoring CSI from an ESP32 receiver device.

**Request Body**:
```json
{
  "port": "COM3"
}
```

**Parameters**:
- `port` (string, required): Serial port name (e.g., `COM3`, `/dev/ttyUSB0`)

**Response**:
```json
{
  "running": true,
  "port": "COM3",
  "packet_count": 0,
  "error": null
}
```

**Status Codes**:
- `200`: Monitoring started successfully
- `400`: Invalid request

---

### `POST /monitor/stop`
Stop monitoring CSI.

**Request Body**: (empty)

**Response**:
```json
{
  "running": false,
  "port": null,
  "packet_count": 0,
  "error": null
}
```

---

### `GET /monitor/status`
Get current monitor status and packet count.

**Response**:
```json
{
  "running": true,
  "port": "COM3",
  "packet_count": 1250,
  "error": null
}
```

---

### `GET /monitor/snapshot`
Get latest CSI snapshot (packet count, CSI amplitudes, and detection results).

**Response**:
```json
{
  "running": true,
  "packet_count": 1250,
  "csi_amplitudes": [12.5, 14.3, 11.2, ..., 13.8],
  "detection": {
    "state": "idle",
    "mv_current": 1.6,
    "mv_threshold": 2.0,
    "confidence": 0.8,
    "cooldown_remaining_s": 0.0,
    "last_fall_at": null,
    "just_triggered": false,
    "selection": { ... },
    "quality": { ... },
    "final_signal": [...],
    "pipeline_latency_ms": 12.5,
    "updated_at": 1688395335.1234567
  }
}
```

---

## Detection Configuration

### `GET /detection/config`
Retrieve the current detection pipeline configuration.

**Response**:
```json
{
  "window_sec": 3.0,
  "stride_sec": 0.5,
  "fs_hz": 100.0,
  "mv_window_sec": 0.5,
  "n_streams": 10,
  "bandpass_low": 0.5,
  "bandpass_high": 50.0,
  "bandpass_order": 4,
  "mv_threshold": 2.0,
  "min_duration_s": 0.5,
  "merge_gap_s": 0.25,
  "max_duration_s": 2.0,
  "cooldown_s": 3.0,
  "wander_window_sec": 6.0,
  "wander_mv_window_sec": 1.0,
  "wander_prefilter_low": 0.05,
  "wander_prefilter_high": 5.0,
  "wander_bandpass_low": 0.1,
  "wander_bandpass_high": 0.5,
  "wander_baseline": 0.5,
  "wander_ratio_threshold": 1.8,
  "wander_min_duration_s": 2.0,
  "presence_timeout_s": 6.0
}
```

---

### `POST /detection/config`
Update detection pipeline configuration (partial update).

**Request Body** (all fields optional):
```json
{
  "mv_threshold": 0.8,
  "min_duration_s": 0.4,
  "cooldown_s": 2.5
}
```

**Parameters**:
- `window_sec`: CSI sliding window duration (seconds)
- `stride_sec`: Pipeline update interval (seconds)
- `fs_hz`: Target resampling frequency (Hz)
- `mv_window_sec`: Moving-variance window duration (seconds)
- `n_streams`: Number of top subcarriers to select
- `bandpass_low`: Bandpass lower cutoff (Hz)
- `bandpass_high`: Bandpass upper cutoff (Hz)
- `bandpass_order`: Butterworth filter order
- `mv_threshold`: Moving-variance threshold for fall trigger
- `min_duration_s`: Minimum time above threshold to confirm fall (seconds)
- `merge_gap_s`: Max gap to merge consecutive detections (seconds)
- `max_duration_s`: Hard cap on fall duration (seconds)
- `cooldown_s`: Lockout duration after fall (seconds)
- `wander_window_sec`: Window duration for the wander (presence) signal (seconds, live detection only)
- `wander_mv_window_sec`: Moving-variance window for the subcarrier-selection stage (seconds), unrelated to the PSD energy calculation
- `wander_prefilter_low`: Wide pre-filter lower cutoff applied before subcarrier selection/summing (Hz)
- `wander_prefilter_high`: Wide pre-filter upper cutoff (Hz)
- `wander_bandpass_low`: Narrow band the Welch PSD energy is integrated over (Hz) — must stay narrower than `wander_prefilter_low`/`wander_prefilter_high`, see `occupation_pipline.md` for why
- `wander_bandpass_high`: Narrow Welch energy integration band upper cutoff (Hz)
- `wander_baseline`: Calibrated baseline Welch PSD band energy (quiet-room reference, not an absolute threshold)
- `wander_ratio_threshold`: Multiplier over `wander_baseline` that triggers WANDER (`wander_current / wander_baseline >= wander_ratio_threshold`)
- `wander_min_duration_s`: Seconds the wander ratio must stay >= `wander_ratio_threshold`, uninterrupted, before it counts as activity (rejects transient spikes from the coarse-resolution live PSD estimate; resets to 0 on any tick that drops below threshold)
- `presence_timeout_s`: Seconds of no MOVE/WANDER activity before presence flips to ABSENT

**Response**:
```json
{
  "status": "ok",
  "config": { ... }
}
```

**Status Codes**:
- `200`: Config updated successfully
- `400`: Invalid parameter values

---

## Onboarding

First-login setup wizard (spec 3.2): service intro -> device registration -> guided
calibration -> complete. State is in-memory only (resets on backend restart), matching
the rest of this backend (no database, no persisted sessions).

### `GET /onboarding/status`
Current onboarding progress.

**Response**:
```json
{
  "onboarded": false,
  "step": 2,
  "service_type": "home",
  "device_name": null,
  "room_name": null
}
```

---

### `POST /onboarding/service`
Step 1: record the selected service type.

**Request Body**:
```json
{ "service_type": "home" }
```

**Response**: `OnboardingStatus` (see above), advances `step` to at least 2.

---

### `POST /onboarding/device`
Step 2: record device/room metadata. Requires the device to already be connected via
`POST /monitor/start` (call that first).

**Request Body**:
```json
{ "device_name": "거실 센서", "room_name": "거실" }
```

**Response**: `OnboardingStatus`, advances `step` to at least 3.

**Status Codes**: `400` if the monitor isn't running.

---

### `POST /onboarding/calibrate/start`
Step 3: kicks off the guided calibration flow in the background (~31s total). First
waits `leave_wait_s` (10s default) for the installer to vacate the room -- no
measurement of any kind starts until this elapses. Then sends the firmware's `"train"`
command, waits out its ~1s AGC-settle window, then captures a 20s baseline CSI window
(room should be empty) and derives `mv_threshold`/`wander_baseline` from the ambient
noise floor (`wander_baseline` is a single Welch PSD band-energy value, not a
mean+k*std threshold like `mv_threshold`). Writes the results directly into the live
detection config.

**Request Body**: (empty)

**Response**:
```json
{ "status": "started" }
```

**Status Codes**: `400` if the monitor isn't running, or calibration is already in progress.

---

### `GET /onboarding/calibrate/status`
Poll calibration progress (recommended: every ~1s while a calibration run is active).

**Response**:
```json
{
  "phase": "measuring",
  "elapsed_s": 12.4,
  "phase_elapsed_s": 2.1,
  "agc_duration_s": 0.98,
  "mv_threshold": null,
  "wander_baseline": null,
  "error": null
}
```

- `elapsed_s`: cumulative seconds since calibration started.
- `phase_elapsed_s`: seconds since the *current* phase began (resets on every phase transition) -- use this for a per-phase countdown/progress display.
- `agc_duration_s`: how long the `waiting_agc` phase actually took, once known (`null` before then). An observability signal only -- it shows whether the timing looks sane (close to ~1s), not proof the firmware's AGC calibration itself succeeded; the firmware has no protocol to report that.

**`phase` values**: `idle` | `leaving` | `waiting_ack` | `waiting_agc` | `measuring` | `done` | `error`

---

### `POST /onboarding/complete`
Step 4: marks onboarding complete.

**Request Body**: (empty)

**Response**: `OnboardingStatus` with `onboarded: true`.

**Status Codes**: `400` if calibration hasn't reached `phase: "done"` yet.

---

## WebSocket

### `WS /ws/live`
Real-time streaming of monitor status, CSI data, and detection results.

**Connection**:
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/live');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(data);
};
```

**Update Frequency**: ~10 Hz (every 100ms)

**Message Format**:
```json
{
  "running": true,
  "packet_count": 1250,
  "csi_amplitudes": [12.5, 14.3, ..., 13.8],
  "detection": {
    "state": "fall",
    "mv_current": 2.9,
    "mv_threshold": 2.0,
    "confidence": 1.45,
    "cooldown_remaining_s": 0.0,
    "last_fall_at": "2026-07-03T14:22:15.123000",
    "just_triggered": false,
    "selection": {
      "indices": [5, 8, 12, 15, 18, 21, 24, 27, 29, 1],
      "q_values": [2.34, 2.15, 1.98, 1.87, 1.76, 1.65, 1.54, 1.43, 1.32, 1.21]
    },
    "quality": {
      "interp_steps": 5,
      "fallback_steps": 0,
      "irregular_gaps": 0,
      "nonpositive_gaps": 0,
      "actual_pps": 100.5,
      "window_duration_s": 3.02
    },
    "final_signal": [-0.12, 0.34, 0.56, ..., -0.21],
    "pipeline_latency_ms": 12.5,
    "updated_at": 1688395335.1234567,
    "presence_state": "present",
    "wander_current": 0.62,
    "wander_baseline": 0.5,
    "wander_ratio_threshold": 1.8,
    "wander_ratio": 1.24,
    "wander_confirmed": false,
    "last_activity_at": 1688395330.1,
    "presence_just_changed": false
  }
}
```

**DetectionInfo Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `state` | string | Current fall state: `idle`, `suspect`, `fall`, `cooldown` |
| `mv_current` | float | Current moving-variance value |
| `mv_threshold` | float | Configured threshold for fall trigger |
| `confidence` | float | Ratio of current MV to threshold (1.0 = at threshold) |
| `cooldown_remaining_s` | float | Seconds remaining in cooldown state |
| `last_fall_at` | string | ISO timestamp of last detected fall (or null) |
| `just_triggered` | bool | `true` if state just transitioned to FALL this tick |
| `selection.indices` | array | Indices of selected subcarriers |
| `selection.q_values` | array | Q-values (activity sensitivity) of selected subcarriers |
| `quality.interp_steps` | int | Number of interpolated samples during resampling |
| `quality.fallback_steps` | int | Number of samples copied (irregular gaps or short windows) |
| `quality.irregular_gaps` | int | Count of timestamp gaps that exceeded tolerance |
| `quality.nonpositive_gaps` | int | Count of non-increasing timestamps (clock anomalies) |
| `quality.actual_pps` | float | Measured packet rate (packets per second) |
| `quality.window_duration_s` | float | Actual duration of the 3-second window processed |
| `final_signal` | array | Normalized combined signal (z-scored) |
| `pipeline_latency_ms` | float | Processing latency for this tick (milliseconds) |
| `updated_at` | float | Timestamp when this result was computed (seconds since epoch) |
| `presence_state` | string | Presence state: `present` or `absent` (spec 4.1.2.1) |
| `wander_current` | float | Current wander Welch PSD band energy (0.1-0.5Hz) |
| `wander_baseline` | float | Calibrated baseline PSD band energy (quiet-room reference) |
| `wander_ratio_threshold` | float | Multiplier over `wander_baseline` that triggers WANDER |
| `wander_ratio` | float | `wander_current / wander_baseline` (the actual computed ratio) |
| `wander_confirmed` | bool | `true` if the wander ratio has been above threshold, uninterrupted, for >= `wander_min_duration_s` (debounced) |
| `last_activity_at` | float | Epoch seconds of the last MOVE/WANDER activity (or null) |
| `presence_just_changed` | bool | `true` if presence_state just transitioned this tick |

---

## Data Models

### StatusResponse
```json
{
  "running": boolean,
  "port": "string | null",
  "packet_count": integer,
  "error": "string | null"
}
```

### SnapshotResponse
```json
{
  "running": boolean,
  "packet_count": integer,
  "csi_amplitudes": [number],
  "detection": "DetectionInfo | null"
}
```

### DetectionInfo
```json
{
  "state": "idle | suspect | fall | cooldown",
  "mv_current": number,
  "mv_threshold": number,
  "confidence": number,
  "cooldown_remaining_s": number,
  "last_fall_at": "string | null",
  "just_triggered": boolean,
  "selection": { "indices": [integer], "q_values": [number] },
  "quality": {
    "interp_steps": integer,
    "fallback_steps": integer,
    "irregular_gaps": integer,
    "nonpositive_gaps": integer,
    "actual_pps": number,
    "window_duration_s": number
  },
  "final_signal": [number],
  "pipeline_latency_ms": number,
  "updated_at": number,
  "presence_state": "present | absent",
  "wander_current": number,
  "wander_baseline": number,
  "wander_ratio_threshold": number,
  "wander_ratio": number,
  "wander_confirmed": boolean,
  "last_activity_at": "number | null",
  "presence_just_changed": boolean
}
```

### DetectionConfigUpdate (request body for POST /detection/config)
```json
{
  "window_sec": number | null,
  "stride_sec": number | null,
  "fs_hz": number | null,
  "mv_window_sec": number | null,
  "n_streams": integer | null,
  "bandpass_low": number | null,
  "bandpass_high": number | null,
  "bandpass_order": integer | null,
  "mv_threshold": number | null,
  "min_duration_s": number | null,
  "merge_gap_s": number | null,
  "max_duration_s": number | null,
  "cooldown_s": number | null,
  "wander_window_sec": number | null,
  "wander_mv_window_sec": number | null,
  "wander_prefilter_low": number | null,
  "wander_prefilter_high": number | null,
  "wander_bandpass_low": number | null,
  "wander_bandpass_high": number | null,
  "wander_baseline": number | null,
  "wander_ratio_threshold": number | null,
  "wander_min_duration_s": number | null,
  "presence_timeout_s": number | null
}
```

---

## Error Responses

### 400 Bad Request
```json
{
  "detail": "At least one ESP port must be provided"
}
```

### 404 Not Found
```json
{
  "detail": "Session not found"
}
```

### 500 Internal Server Error
```json
{
  "detail": "Internal server error message"
}
```

---

## Example Workflows

### Calibrate Threshold

1. Start monitoring:
   ```bash
   curl -X POST http://localhost:8000/monitor/start \
     -H "Content-Type: application/json" \
     -d '{"port": "COM3"}'
   ```

2. Open dashboard and shake device, observe `mv_current` value

3. Update threshold to ~50-70% of observed peak:
   ```bash
   curl -X POST http://localhost:8000/detection/config \
     -H "Content-Type: application/json" \
     -d '{"mv_threshold": 1.2}'
   ```

4. Test again and refine as needed

### Monitor Live Detection

1. Get live updates via WebSocket (automatic in browser dashboard)

2. Or poll via REST:
   ```bash
   while true; do
     curl http://localhost:8000/monitor/snapshot | jq '.detection.state'
     sleep 1
   done
   ```

---

## Performance Notes

- **Pipeline latency**: Typically 8-15ms per 3-second window (measured in `DetectionInfo.pipeline_latency_ms`)
- **WebSocket update rate**: 10 Hz (every 100ms)
- **Detection update rate**: 2 Hz (every 0.5s stride_sec)
- **CSI buffer**: Holds ~15 seconds of data at 100pps (1500 packets)
- **Memory**: ~500 MB for CSI buffer + pipeline state

---

## Live Configuration Best Practices

- **Adjust only one parameter at a time** to understand the effect
- **Lower `mv_threshold`** for more sensitive detection (more false positives)
- **Increase `min_duration_s`** for more stable detection (fewer transients)
- **Reduce `cooldown_s`** for faster re-triggering (useful for multiple-fall scenarios)
- **Monitor `quality` fields** to detect packet loss or clock issues
