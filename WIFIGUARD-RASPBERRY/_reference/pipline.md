

```
┌────────────────────────────────────────────────────────────────────────┐
│                   WiFi 비콘 송수신 (ESP-NOW)                           │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  ┌──────────────────┐                  ┌──────────────────┐           │
│  │  ESP32 TX        │   ESP-NOW        │  ESP32 RX        │           │
│  │  (csi_send)      │ ←────────────→   │  (csi_recv)      │           │
│  │                  │   rate cmd       │                  │           │
│  │ - 50Hz: beacon   │   (50 or 320)    │ - CSI 수신       │           │
│  │ - 320Hz: beacon  │                  │ - RSSI 추출      │           │
│  └──────────────────┘                  └──────────────────┘           │
│                                                │                       │
│                                                ↓                       │
│                                    ┌────────────────────────┐         │
│                                    │ Occupancy FSM          │         │
│                                    │ (occupancy_fsm.c)      │         │
│                                    │                        │         │
│                                    │ 상태:                  │         │
│                                    │ - TRAINING (10+20s)    │         │
│                                    │ - OCCUPANCY (50Hz)     │         │
│                                    │ - FALL (320Hz)         │         │
│                                    └────────────────────────┘         │
│                                                │                       │
│                                ┌───────────────┴──────────────┐       │
│                                ↓                              ↓       │
│                    ┌─────────────────────┐      ┌──────────────────┐ │
│                    │ STATUS_JSON (50Hz)  │      │ CSI_BATCH (320Hz)│ │
│                    │ (occupancy mode)    │      │ (fall mode)      │ │
│                    │                     │      │                  │ │
│                    │ - entry: bool       │      │ - 16 CSI frames  │ │
│                    │ - motion: bool      │      │ - raw amplitude  │ │
│                    │ - jitter_smooth     │      │ - RSSI value     │ │
│                    │ - wander            │      │ - timestamp      │ │
│                    │ - occupancy_confirmed│      │                  │ │
│                    └─────────────────────┘      └──────────────────┘ │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
                                        ↓
                            ┌───────────────────────┐
                            │  USB Serial Interface │
                            │  (uart_if.c)          │
                            │                       │
                            │ TX Buffer Queue       │
                            │ ├─ Header (1 byte)    │
                            │ ├─ Length field       │
                            │ └─ Payload            │
                            │                       │
                            │ Variable Frame Size   │
                            │ (STATUS: ~50B,        │
                            │  CSI_BATCH: ~2300B)   │
                            └───────────────────────┘
                                        ↓
                    ┌───────────────────────────────────┐
                    │  USB Serial (RPi ↔ ESP32)         │
                    │                                   │
                    │  ESP32:                           │
                    │  • UART1 TX: GPIO 1               │
                    │  • UART1 RX: GPIO 3               │
                    │  • USB-to-Serial bridge           │
                    │                                   │
                    │  Raspberry Pi:                    │
                    │  • /dev/ttyUSB0 (or similar)      │
                    │  • Baud: 921600                   │
                    │  • RTS/CTS flow control           │
                    └───────────────────────────────────┘
                                        ↓
                    ┌───────────────────────────────────┐
                    │  Raspberry Pi                     │
                    │  (serial_interface.py)            │
                    │                                   │
                    │  _rx_worker():                    │
                    │  • Serial port open & read        │
                    │  • Frame parsing (header+payload) │
                    │  • on_serial_frame() 호출         │
                    └───────────────────────────────────┘
                                        ↓
                    ┌───────────────────────────────────┐
                    │  web_server.py                    │
                    │  SPI Frame Dispatcher             │
                    │                                   │
                    │  if PKT_TRAIN_PROGRESS:           │
                    │    → update_training_status()     │
                    │                                   │
                    │  if PKT_TRAIN_DONE:               │
                    │    → save NVS (base_rssi, amp)    │
                    │    → broadcast train_done         │
                    │                                   │
                    │  if PKT_STATUS_JSON (50Hz):       │
                    │    → process_occupancy_json()     │
                    │    → broadcast occupancy update   │
                    │    → check mode change request    │
                    │                                   │
                    │  if PKT_CSI_BATCH (320Hz):        │
                    │    → fall_detector.process_csi()  │
                    │    → exit_detector.process_csi()  │
                    │    → check fall_detected()        │
                    │    → broadcast fall_alarm         │
                    └───────────────────────────────────┘
                                        ↓
                    ┌───────────────────────────────────┐
                    │  State Manager (state_manager.py) │
                    │                                   │
                    │  States:                          │
                    │  - IDLE: 대기                     │
                    │  - TRAINING: 기준점 수집           │
                    │  - OCCUPANCY: 50Hz 재실감지       │
                    │  - FALL_DETECTING: 320Hz 낙상감지  │
                    │  - ERROR: 에러 상태               │
                    │                                   │
                    │  User Mode:                       │
                    │  - OCCUPANCY_ONLY                 │
                    │  - OCCUPANCY_AND_FALL             │
                    └───────────────────────────────────┘
                                        ↓
                    ┌───────────────────────────────────┐
                    │  Detection Algorithms             │
                    │                                   │
                    │  fall_detector.py:                │
                    │  • SignalProcessor: CSI→feature   │
                    │  • Extract: mean, std, peak_diff  │
                    │  • Detect: burst + stillness      │
                    │                                   │
                    │  exit_detector.py:                │
                    │  • 10s window RSSI variance       │
                    │  • base_rssi_var * 2.0 이하       │
                    │  → Person exited (fall mode)      │
                    └───────────────────────────────────┘
                                        ↓
                    ┌───────────────────────────────────┐
                    │  WebSocket & HTTP API             │
                    │  (web_server.py FastAPI)          │
                    │                                   │
                    │  POST /api/train/start            │
                    │  POST /api/mode (occupancy|fall)  │
                    │  POST /api/fall/confirm           │
                    │  GET /api/status                  │
                    │  WS /ws (real-time updates)       │
                    └───────────────────────────────────┘
                                        ↓
                    ┌───────────────────────────────────┐
                    │  Web UI (static/index.html)       │
                    │                                   │
                    │  - Login Page                     │
                    │  - Setup (Training)               │
                    │  - Mode Selection                 │
                    │  - Monitoring                     │
                    │    ├─ Entry detection            │
                    │    ├─ Motion detection           │
                    │    ├─ Occupancy confirmed        │
                    │    └─ Fall alarm                 │
                    └───────────────────────────────────┘
```

---

## 📊 시나리오별 데이터 흐름

### 1️⃣ 훈련 단계 (Training Phase - 30초)

```
시간    ESP32-RX          SPI 메시지           Raspberry Pi
─────────────────────────────────────────────────────────────
0s     [CSI 수신 시작]
       g_mode = TRAINING
       training_enabled = true

        ↓ (매 50ms마다)
50ms   train_tick()
       - RSSI 수집: -60 dBm
       - CSI 진폭 평균: 0.45
       - 누적: sum=0.45, sq_sum=0.20
       
        ├─ 5번째 tick (250ms)
        │  [SPI] PKT_TRAIN_PROGRESS
        │  {"elapsed_s": 0, ...} ──────→ update_training_status()
        │                               broadcast("train_progress")
        │
10s    [Warmup 완료]
       g_fsm.training_enabled = true (continue)

       ↓ (매 50ms마다 계속)
20s    train_tick() 누적 계속
       - 1000 samples 수집 (50Hz × 20s)
       - base_rssi_mean = -60.2 dBm
       - base_rssi_var = 2.5

        ├─ 400번째 tick (20s)
        │  training_done() ─→ true
        │  nvs_save_base_rssi(mean, var, amp_mean, amp_std)
        │
30s    [Training 완료]
       g_mode = MODE_OCCUPANCY
       
       [SPI] PKT_TRAIN_DONE
       {"base_rssi_mean": -60.2,
        "base_rssi_var": 2.5,
        "base_amp_mean": 0.48,
        "base_amp_std": 0.15} ──────→ training_complete()
                                    state_manager.set_state(OCCUPANCY)
                                    broadcast("train_done")
                                    fall_detector = FallDetector(base_var=2.5)
```

### 2️⃣ 재실감지 모드 (Occupancy Mode - 50Hz)

```
시간    ESP32-RX          SPI 메시지           Raspberry Pi
─────────────────────────────────────────────────────────────
0s+    [모드 전환]
       g_mode = MODE_OCCUPANCY
       
       CSI 수신 시작

        ↓ (매 frame마다, 50Hz = 20ms)
1      motion_detect_tick()
       - jitter_raw = 0.03
       - jitter_smooth = 0.30 * 0.03 + 0.70 * 0.025 = 0.026
       - motion_state: IDLE → (jitter < thr) → IDLE

2      occupancy_confirm_tick()
       - motion_confirm_count = 0 (아직 motion 없음)

3      exit_detect_tick()
       - exit_no_motion_ms = 0 (motion 확인 안 됨)

        ├─ 5번째 tick (100ms)
        │  [SPI] PKT_STATUS_JSON (50ms 마다)
        │  {"entry": false,
        │   "motion": false,
        │   "jitter_smooth": 0.026,
        │   "wander": 0.002} ──────→ process_occupancy_json()
        │                           broadcast("occupancy", ...)
        │
        │
[누군가 입실]

6      entry_detect_tick()
       - RSSI variance 갑자기 증가: 8.0 (base * 3.0)
       - g_fsm.entry = true

7      motion_detect_tick()
       - jitter_smooth = 0.045 > threshold_enter(0.030)
       - wander = 0.08 > threshold(0.30)
       - motion_state: IDLE → DEBOUNCE_IN

8      motion_detect_tick()
       - jitter_smooth = 0.042 > threshold_enter
       - motion_state: DEBOUNCE_IN → DEBOUNCE_IN (count=2)

9      motion_detect_tick()
       - jitter_smooth = 0.038 > threshold_enter
       - motion_state: DEBOUNCE_IN → ACTIVE (count=3, debounce 완료)
       - g_fsm.motion = true

10     occupancy_confirm_tick()
       - motion_confirm_bits[0] = 1 (1/60 확인됨)

        ├─ 동시에 SPI 전송
        │  [SPI] PKT_STATUS_JSON
        │  {"entry": true,
        │   "motion": true,
        │   "occupancy_confirmed": false,
        │   ...} ──────→ broadcast("occupancy", entry=true, motion=true)
        │
...

60     occupancy_confirm_tick()
       - motion_confirm_count = 10 (10/60 확인)
       - g_fsm.occupancy_confirmed = true
       - g_fsm.req_mode_change = "fall" (낙상 모드 요청)

        │  [SPI] PKT_STATUS_JSON
        │  {"entry": true,
        │   "motion": true,
        │   "occupancy_confirmed": true,
        │   "req_mode_change": "fall"} ──────→ process_occupancy_json() → True
        │                                      send_mode_fall()
        │                                      state_manager.set_state(FALL_DETECTING)
        │                                      broadcast("mode_change", mode="fall")
```

### 3️⃣ 낙상감지 모드 (Fall Detection - 320Hz)

```
시간     ESP32-RX          SPI 메시지           Raspberry Pi
──────────────────────────────────────────────────────────────
0s+     [모드 전환 신호 수신 from RPi]
        spi_slave_if_recv_cmd() → CMD_MODE_FALL
        g_mode = MODE_FALL
        spi_slave_if_set_mode(SPI_MODE_FALL_DETECT)
        espnow_send_cmd(SET_RATE, 320) ──→ TX rate 320pps로 변경
        
        CSI 수신 시작 (320Hz = 3.125ms)

        ↓ (매 frame마다, 320Hz)
1       csi_raw_frame_t 생성
        - rssi: -58 dBm
        - csi_len: 128 bytes
        - timestamp_ms: 1000

2-16    [CSI 프레임 누적]
        g_csi_frame_count: 1 → 16
        g_csi_batch_len: 140 → 2240 bytes
        
        ├─ 16번째 frame (50ms 후)
        │  g_csi_frame_count >= CSI_BATCH_MAX_FRAMES (16)
        │  
        │  spi_slave_if_flush_csi_batch()
        │  TX 큐에 전송:
        │  [SPI] PKT_CSI_BATCH (4096B)
        │  - hdr.num_frames = 16
        │  - hdr.payload_len = 2240
        │  - hdr.mode = SPI_MODE_FALL_DETECT
        │  - payload: 16 × csi_raw_frame_t ──→ _rx_worker() 수신 (LARGE)
        │                                      on_spi_frame(frame, payload_len=2240)
        │
        │                                      for i in range(16):
        │                                         csi = unpack(payload[i*140:(i+1)*140])
        │                                         fall_detector.process_csi_frame(csi)
        │                                         exit_detector.process_csi_frame(csi)
        │
        │                                      if fall_detector.detect_fall():
        │                                         broadcast("fall_alarm", confidence=0.92)
        │                                      
        │                                      if exit_detector.is_person_exited():
        │                                         send_mode_occupancy()
        │                                         set_state(OCCUPANCY)
        │                                         broadcast("mode_change", mode="occupancy")
        │
        │
17-32   [다음 16 프레임 누적]
        g_csi_frame_count: 0 → 16
        
        ├─ 32번째 frame (100ms 후)
        │  [SPI] PKT_CSI_BATCH 다시 전송
        │  ...
        │
        │
[낙상 감지됨]

160     fall_detector.detect_fall() → True
        (burst + stillness 조건 만족)
        
        state_manager.update_fall_status(True, confidence=0.92)
        broadcast("fall_alarm", confidence=0.92)
        
        UI: 빨간 배경 + 알람음 + "낙상 감지됨" 텍스트
        사용자: [확인] 버튼 클릭
        
        ↓
        POST /api/fall/confirm
        
        state_manager.confirm_fall() → True
        send_mode_occupancy() ──→ TX rate 50pps로 복구
        set_state(OCCUPANCY)
        fall_detector.reset()
        exit_detector.reset()
        
        UI: 모니터 페이지로 복귀, 재실감지 모드로 전환