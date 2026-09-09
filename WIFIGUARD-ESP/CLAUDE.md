# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

This repo implements a real-time, WiFi CSI (Channel State Information)-based fall-detection system on ESP32-C5 hardware, plus a separate WiFi-sensing (motion presence) demo. It has two kinds of subprojects:

- **ESP-IDF firmware projects** (`csi_send/`, `csi_recv/`, `csi_recv_calibrate/`, `wifi_sensing_demo_router/`): C code flashed to ESP32-C5 boards.
- **Python tools** (`tools/fall_detect/`, `tools/stride/`, `tools/lagacy/`): host-side servers/scripts that talk to the receiver board over USB serial.

### Hardware data flow (fall detection system)

Two physical ESP32-C5 boards are required — a single board produces zero CSI data:

1. **Transmitter** (`csi_send/`): battery/USB powered, not connected to the PC. Broadcasts ESP-NOW packets at 100Hz with a spoofed MAC `1a:00:00:00:00:00`.
2. **Receiver** (`csi_recv/` or `csi_recv_calibrate/`): connected to the PC via USB. Filters incoming CSI to the transmitter's spoofed MAC (see `CONFIG_CSI_SEND_MAC` in `app_main.c`), packages CSI + radiotap-style metadata into a binary frame (magic `0xA55A`, checksum, 612-byte CSI payload), and streams it out over USB serial at **921600 baud, 8N1**.
3. **Python backend** (`tools/fall_detect/main.py`, FastAPI): reads the serial port, parses binary frames, runs a signal-processing pipeline, and serves a browser dashboard.

Both boards must be on the **same WiFi band/channel** — default is 5GHz channel 48 (`CONFIG_LESS_INTERFERENCE_CHANNEL` / `USE_5G_BAND` at the top of each `app_main.c`). Changing the channel/band requires editing and reflashing *both* `csi_send` and the receiver variant in use.

`csi_recv_calibrate/` is a receiver variant that diverges from `csi_recv/` in `app_main.c`: it boots into an idle phase where CSI packets are dropped and nothing streams. It only starts once it reads the literal line `train` over the USB-serial stdin (a non-blocking read loop, `csi_train_cmd_task`), then runs a ~1s AGC gain-calibration window (`CSI_TRAIN_DURATION_US`) before transitioning to normal binary streaming with the calibrated gain forced via `esp_csi_gain_ctrl_set_rx_force_gain`. Build/CMake/component deps are otherwise identical to `csi_recv/`. This firmware-level "train" command is what `tools/fall_detect`'s onboarding wizard (below) drives via `EspMonitor.send_line("train")` — don't confuse it with the unrelated, still-unimplemented "Train" tab in the dashboard (`F-TRN-001` in `tools/fall_detect/FEATURE_SPEC.md`, a DNN-training placeholder stub with no logic).

`wifi_sensing_demo_router/` is an unrelated, self-contained ESP-IDF example exercising Espressif's `esp_wifi_sensing` component (presence/motion FSM, not the CSI-fall-detect pipeline above). It has its own managed components (`espressif__esp-radar`, `espressif__esp_wifi_sensing`, `espressif__esp_radar_motion_dec`, `espressif__led_strip`, `espressif__ethernet_init`) and a browser-based Web Serial monitor (`wifi_sensing_demo_router/tools/web_serial_monitor.html`) using a distinct `HMS:` / `HMSCMD` line protocol — do not confuse this with the fall-detect binary frame protocol.

## Build / flash (ESP-IDF firmware)

Each firmware directory (`csi_send/`, `csi_recv/`, `csi_recv_calibrate/`, `wifi_sensing_demo_router/`) is an independent ESP-IDF project. From inside one of these directories:

```bash
idf.py set-target esp32c5
idf.py build
idf.py -p <PORT> flash monitor
```

- WiFi band/channel is set at the top of `main/app_main.c` via `USE_5G_BAND` (1 = 5GHz ch48, 0 = 2.4GHz ch11) — not via Kconfig — so band changes require editing source and rebuilding.
- `managed_components/` are ESP-IDF component-manager dependencies declared in `main/idf_component.yml`; don't hand-edit them.

## Python tools

### `tools/fall_detect/` — the live fall-detection MVP

```bash
cd tools/fall_detect
pip install -r requirements.txt
python main.py --host 0.0.0.0 --port 8000
```

Run the unit tests (pure signal-processing/state-machine logic, no hardware needed):

```bash
cd tools/fall_detect
python -m pytest tests/
python -m pytest tests/test_pipeline.py::test_moving_variance   # single test
```

Architecture:
- `main.py` — FastAPI app: serial reader thread → binary frame parser → ring buffer (`EspMonitor`, 1500 frames / ~15s) → REST endpoints (`/monitor/*`, `/detection/config`) → WebSocket push at 10Hz (`/ws/live`).
- `src/pipeline.py` — `PipelineConfig` (all tunable detection parameters) and the `run_once` orchestration that ticks every `stride_sec`.
- `src/streaming_features.py` — the real-time per-tick signal chain: resample to a regular 100Hz grid → Butterworth bandpass (2–50Hz, zero-phase) → select top-N subcarriers by q-value (activity metric) → sum + z-score normalize → moving variance.
- `src/fall_state_machine.py` — threshold-based `IDLE → SUSPECT → FALL → COOLDOWN` state machine with hysteresis (`merge_gap_s`) and a hard cap (`max_duration_s`).
- `src/presence_state_machine.py` — `PresenceDetector`: a second, simpler timeout state machine (`PRESENT`/`ABSENT`, spec 4.1.2.1) driven by two signals computed per tick in `pipeline.py`'s `run_once()` — the existing MV signal, plus a "wander" signal: a second `compute_final_signal` pass with `compute_band_energy=True`, producing a Welch PSD band-energy value (not moving variance) over a narrow 0.1–0.5Hz band, using a *wider* 0.05–5Hz pre-filter band for the upstream subcarrier-selection/summing stage — the two bands must differ, or the pre-filter alone makes the post-normalization PSD measurement nearly input-invariant (see `occupation_pipline.md` §2 for the empirically-confirmed failure mode). Wander triggers when `wander_current / wander_baseline >= wander_ratio_threshold` (a calibrated ratio, not an absolute value). Any MOVE-or-WANDER touch resets an activity clock; presence flips to ABSENT after `presence_timeout_s` (default 6s) of inactivity. Full algorithm writeup: `tools/fall_detect/occupation_pipline.md`.
- `src/onboarding.py` — first-login onboarding orchestration (spec 3.2): in-memory `OnboardingState`/`CalibrationState` (no persistence, resets on restart) and `run_calibration()` (~31s total), which first waits `leave_wait_s` (10s default) for the installer to vacate the room — no measurement starts until this elapses — then drives the firmware's `"train"` command (via `EspMonitor.send_line`), waits out the AGC-settle window, captures a 20s baseline CSI window (room expected empty), and derives `mv_threshold` (`mean + k*std` of MV moving-variance, clamped to a floor) and `wander_baseline` (a single Welch PSD band-energy value from that window, clamped to a floor — no time series to average, unlike `mv_threshold`) — written directly into the shared `PipelineConfig`. `CalibrationState.phase_started_at`/`agc_duration_s` give per-phase progress and how long the AGC phase actually took (observability only — the firmware has no protocol to report AGC success itself). Exposed via `main.py`'s `/onboarding/*` routes (see `API.md`).
- `src/preprocessing.py` / `src/true_activity_time.py` — offline/reference implementations of the same algorithm (batch analysis), not used by the live pipeline.
- `static/index.html` — single-file vanilla-JS dashboard (Korean UI), no build step. A full-screen onboarding wizard overlay gates the dashboard until `/onboarding/status` reports `onboarded: true`.

`mv_threshold` (default `2.0`) and `wander_ratio_threshold` (default `1.8`) were deliberately lowered from their earlier values (`2.5`/`2.0`) on 2026-07-14 to favor sensitivity over precision as an interim measure — expect more false positives until retuned against real usage data.

`SPEC.md` (`tools/fall_detect/SPEC.md`) is a detailed Korean functional spec of the current MVP (architecture, frame format, full signal pipeline math, state machine, API) — read it before making non-trivial changes to the detection pipeline or API, since it documents exact formulas (e.g. moving-variance convolution trick, q-value activity score) that aren't otherwise commented in code.

`FEATURE_SPEC.md` (`tools/fall_detect/FEATURE_SPEC.md`) is a separate Korean document at the screen/feature level (not algorithm-level like `SPEC.md`): it tables each existing UI feature by screen (`F-COM`/`F-MON`/`F-CFG`/`F-HIST`/`F-TRN`/`F-SYS` IDs) and proposes gap features (`EXT-*`) for remote alerting, multi-resident management, and account/permissions that don't exist yet. Read it for product/UX intent, not implementation detail.

### `tools/stride/`

A separate FastAPI tool for **multi-device** CSI capture (status/snapshot models key data by `esp_ports`/`esp_paths` dicts rather than a single port), sharing the same binary frame protocol constants as `fall_detect`. Not the same codebase as `fall_detect` — check `tools/stride/main.py` directly rather than assuming parity with `fall_detect/main.py`.

### `tools/lagacy/`

Older standalone CSI-to-CSV logging script (`main.py -p <PORT>`), Korean README. Superseded by the FastAPI tools above; kept for reference/offline data collection.

## Binary serial frame protocol (shared by firmware + Python tools)

`csi_recv/main/app_main.c` and the Python parsers (`tools/fall_detect/main.py`, `tools/stride/main.py`) must agree on this wire format — if you change one, update the others:

- Magic `0xA55A` (uint16 LE), `version` (must equal 1), `frame_type` (CSI = 1).
- Fixed header (`struct.calcsize("<HHBBI6sbBBBBBBBBBBbBBBBHHIbBHBB")`) with seq, MAC, RSSI, rate/mode/mcs/bandwidth flags, local ESP32 timestamp (μs, wraps at 2^32 — Python side must unwrap it), antenna, etc.
- Fixed 612-byte CSI payload: interleaved signed int8 (imaginary, real) pairs for 306 subcarriers; amplitude = `sqrt(imag² + real²)`.
- Trailing 2-byte checksum = sum of all preceding bytes & 0xFFFF.
