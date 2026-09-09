# WIFI-GUARD-esp 레포 개발 명세서

**ESP32-C5 펌웨어 · CSI 수집 및 SPI 슬레이브 전송**

v1.0 · 작성일 2026-08-04

> 4-레포 분리(`esp` / `raspberry` / `backend` / `frontend`) 중 펌웨어 레포의 명세다.
> 전제 문서: `CSI-Guard_구현현황_및_완성목표_v1.0_20260729.md`, `CSI-Guard_완성목표_실행계획_v1.0_20260729.md`, `CSI-Guard_인프라구축.md`
> 인접 레포: [WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md](WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md)

---

## 1. 레포 개요 · 책임 경계

### 1.1 한 줄 정의

ESP32-C5 송수신 2대의 펌웨어. **CSI를 수집해 SPI로 라즈베리파이에 올리는 것까지**가 전부다.

```
[ESP32-C5 TX] --ESP-NOW 5GHz ch48--> [ESP32-C5 RX] --SPI 전이중 50ms--> [Raspberry Pi]
     ↑                                      ↓
     └────── SET_RATE 커맨드 ────────────────┘
```

### 1.2 하는 것

| # | 책임 |
|---|---|
| E1 | ESP-NOW 비콘 송신(TX) · CSI 수신(RX). 밴드 5GHz ch48 |
| E2 | 원시 CSI를 `csi_raw_frame_t`로 패킹해 SPI 슬레이브로 전송 |
| E3 | Pi로부터 SPI 커맨드 수신(`START_TRAIN` / `MODE_OCCUPANCY` / `MODE_FALL` / `PING`) |
| E4 | ESP-NOW 레이트 전환(occupancy ↔ fall). 실제로 바뀌는 건 **TX 비콘 레이트**이고 RX는 따라간다 |
| E5 | AGC 캘리브레이션 — `train` 수신 시 약 1초간 AGC 게인을 측정해 **고정(force gain)** |
| E6 | NVS에 baseline RSSI/진폭 저장 (전원 복구 시 재사용) |

### 1.3 하지 않는 것 — 명시적 비책임

| 항목 | 어디서 하는가 | 근거 |
|---|---|---|
| **네트워크 계층 (Wi-Fi STA, MQTT, TLS)** | 라즈베리파이 | 실행계획 §0.0-1: "MQTT 클라이언트는 라즈베리파이에만 두면 되므로 G2에서 예상했던 펌웨어 팀 의존이 사라진다" |
| **재실 판정 (권위)** | 라즈베리파이 | G7 — 재실감지는 엣지(Pi)로 이관. 온디바이스 `occupancy_fsm.c`는 폴백으로만 유지(§5.4) |
| **낙상 판정** | 클라우드 백엔드 | G4·G6 — 모델 갱신·공간별 적응을 중앙 관리 |
| **신호처리 (리샘플·밴드패스·CWT)** | 라즈베리파이 | ESP는 원시 진폭 I/Q만 올린다 |
| **캘리브레이션 절차 오케스트레이션** | 라즈베리파이 | ESP는 `train` 명령을 받아 AGC만 고정. 61초 4단계 절차는 Pi가 진행 |

> **설계 원칙**: 펌웨어는 "측정기"이지 "판정기"가 아니다. 판정 로직을 ESP에 두면 알고리즘 개선마다 현장 재플래시가 필요해진다.

---

## 2. 핵심 기능 목록

| ID | 기능 | 상태 | 비고 |
|---|---|---|---|
| F-E01 | ESP-NOW 브로드캐스트 송신 (TX) | 기존 | MAC 스푸핑 `1a:00:00:00:00:00` |
| F-E02 | CSI 수신 + MAC 필터 (RX) | 기존 | `CONFIG_CSI_SEND_MAC` |
| F-E03 | SPI 슬레이브 전이중 통신 | 기존 | 50ms 주기, DMA |
| F-E04 | CSI 배치 누적·플러시 | 기존 | `CSI_BATCH_MAX_FRAMES` |
| F-E05 | Pi→ESP 커맨드 수신 | 기존 | 전이중 트랜잭션의 MOSI 측 |
| F-E06 | ESP-NOW 레이트 전환 (50/320pps) | 기존 | `espnow_send_cmd(SET_RATE, pps)` + ACK 대기 |
| F-E07 | AGC 캘리브레이션 (`train`) | 기존(별도 앱) | `csi_recv_calibrate`에서 통합 필요 |
| F-E08 | NVS baseline 저장·복구 | 기존 | `nvs_save_base_rssi()` |
| F-E09 | UART 진단 채널 (`HMS:` 라인) | 기존 | 웹시리얼 모니터용, 운영 시 비활성 가능 |
| **F-E10** | **전 모드 원시 CSI 스트리밍** | **신규 (Phase 0)** | occupancy 모드에서도 CSI 배치 발행 |
| **F-E11** | **`CSI_MAX_LEN` 612 확대** | **신규 (Phase 0)** | 245 subcarrier pair 수용 |
| **F-E12** | **`SPI_FRAME_SIZE_LARGE` 8192 확대** | **신규 (Phase 0)** | 위 확대에 따른 프레임 재산정 |
| F-E13 | 밴드/채널 Kconfig 승격 | 개선 | 현재는 소스 `#define` |
| F-E14 | AGC 리셋 성공 ACK 프레임 | 개선 | 현재는 경과시간으로 추정만 함 |

---

## 3. 디렉토리 구조

```
WIFI-GUARD-esp/
├── README.md
├── CLAUDE.md                          아키텍처 · 와이어 프로토콜 (기존 esp32c5/CLAUDE.md 승계)
├── docs/
│   ├── PROTOCOL.md                    SPI/ESP-NOW 와이어 계약 (§7의 정식판)
│   └── PINOUT.md                      핀맵 · 배선도
├── common/
│   └── protocol.h                     ★ SPI/ESP-NOW 공통 정의 (단일 원본)
├── csi_send/                           송신기 (ESP-IDF 프로젝트)
│   ├── CMakeLists.txt
│   ├── sdkconfig.defaults
│   └── main/
│       ├── app_main.c                 ESP-NOW 비콘 브로드캐스트 + SET_RATE 수신
│       ├── CMakeLists.txt
│       └── idf_component.yml
├── csi_recv_spi/                       수신기 (ESP-IDF 프로젝트) ★ 주력
│   ├── CMakeLists.txt
│   ├── sdkconfig.defaults
│   └── main/
│       ├── app_main.c                 CSI 콜백 · 모드 디스패치 · NVS
│       ├── spi_slave_if.c/.h          SPI 슬레이브 DMA + GPIO 핸드셰이크
│       ├── agc_calibrate.c/.h         ★ 신규: csi_recv_calibrate에서 이식
│       ├── occupancy_fsm.c/.h         폴백 전용 (기본 비활성, §5.4)
│       ├── protocol.h                 → common/protocol.h 심볼릭 참조
│       └── CMakeLists.txt
├── csi_recv_uart/                      레거시 UART 수신기 (참조용, 빌드 제외)
└── tools/
    ├── web_serial_monitor_fall.html   브라우저 시리얼 모니터 (진단)
    └── flash.sh                       TX/RX 일괄 플래시 스크립트
```

**정리 대상** (기존 `esp32c5/` 레포에서 승계하지 않음):

| 제외 | 이유 |
|---|---|
| `wifi_sensing_demo/`, `wifi_sensing_demo_router/` | Espressif `esp_wifi_sensing` 예제. 낙상 파이프라인과 무관 |
| `tools/lagacy/` | 구 CSV 로거 |
| `tools/fall_detect/` | 현 `backend/`의 조상. `WIFI-GUARD-backend`/`-raspberry`가 대체 |
| `tools/stride/` | 다기기 CSI 수집 도구 → 데이터 수집용으로 별도 유지 또는 backend 레포 |
| `managed_components/` (커밋된 상태) | `dependencies.lock` 기반 재현으로 전환 검토 |

---

## 4. 구현 단계

### Phase 0 — 프레임 용량 확대 (선결, 다른 모든 Phase의 전제)

라즈베리파이가 재실감지와 피처 추출을 맡기로 확정되면서, 시제품(`infra/esp32c5/csi_recv_spi/`)의 두 전제가 깨진다.

#### 0-A. 전 모드 원시 CSI 스트리밍

시제품은 occupancy 모드에서 `occupancy_fsm.c`의 판정 결과를 `PKT_STATUS_JSON`으로만 올리고 원시 CSI는 fall 모드에서만 올린다. 재실감지가 Pi로 가므로 **occupancy 모드에서도 CSI 배치를 발행해야 한다.**

- `PKT_CSI_BATCH`를 occupancy 모드에서도 발행
- `PKT_STATUS_JSON`은 폐기하지 않고 **진단·폴백 참고용으로 병행 발행** (§5.4)
- SPI 헤더의 `mode` 필드는 그대로 유지 → Pi가 현재 모드를 알 수 있어야 함

#### 0-B. `CSI_MAX_LEN` · 프레임 크기 확대

낙상 모델은 **245 subcarrier pair에서 상위 30개를 선택**하도록 학습되어 있다(`FeatureConfig.train_raw_pairs=245`, `train_drop_pair_indices=(121,122)`, `target_subcarriers=30`). 시제품의 `CSI_MAX_LEN=128` 바이트는 **64 pair**밖에 담지 못한다.

```c
/* 변경 전 */
#define CSI_MAX_LEN          128
#define SPI_FRAME_SIZE_LARGE 4096
_Static_assert(sizeof(csi_raw_frame_t) == 140, ...);   /* 메타 12B + 128B */

/* 변경 후 */
#define CSI_MAX_LEN          612    /* 306 pair — UART 경로 최대치와 동일 */
#define SPI_FRAME_SIZE_LARGE 8192
_Static_assert(sizeof(csi_raw_frame_t) == 626, ...);   /* 메타 14B + 612B */
```

**메타 영역이 12B → 14B로 늘어나는 이유**: `csi_len`이 `uint8_t`라 최대 255밖에 표현하지 못한다. 490/612를 담으려면 **`uint16_t`로 승격**해야 하고, 정렬을 위해 1B reserved를 덧붙인다(§5.1).

**프레임 크기 산정** — `csi_raw_frame_t` = 메타 **14B** + `CSI_MAX_LEN`, SPI 헤더 8B, 페이로드 예산 = 프레임 − 8B:

| `CSI_MAX_LEN` | frame | 모드 · 레이트 | 50ms당 프레임 | 페이로드 | 4096B (예산 4,088) | 8192B (예산 8,184) |
|---:|---:|---|---:|---:|:---:|:---:|
| 490 (245 pair) | 504B | occupancy 50Hz | 3 | 1,512B | ✓ | ✓ |
| 490 | 504B | fall 166.67Hz | 9 | 4,536B | ✗ | ✓ |
| 490 | 504B | fall 320Hz | 16 | 8,064B | ✗ | ✓ |
| 612 (306 pair) | 626B | occupancy 50Hz | 3 | 1,878B | ✓ | ✓ |
| 612 | 626B | fall 166.67Hz | 9 | 5,634B | ✗ | ✓ |
| 612 | 626B | fall 320Hz | 16 | 10,016B | ✗ | ✗ |

→ **`SPI_FRAME_SIZE_LARGE = 8192`, `CSI_MAX_LEN = 612`를 채택**한다. 표에서 예산을 넘는 조합은 **320Hz × 306 pair 하나뿐**이며, 그 조합을 쓸 경우에만 flush 주기를 25ms로 낮춘다(8프레임 × 626 = 5,008B ✓). §9-1의 **166.67Hz 권고**를 채택하면 8192B/50ms 한 번으로 전부 해결된다.

**대역폭 여유 확인**: 8192B / 50ms = 163.8 KB/s = 1.31 Mbps. `spidev` 10 MHz에서 트랜잭션당 8192×8/10e6 = **6.6ms** — 50ms 예산 대비 충분하다.

**동시 수정 필요 (Pi 측)**: `infra/raspberry/spi_interface.py`의 고정 4096B 전송, `protocol.py`의 `CsiRawFrame.size = 140` 슬라이싱, `spi_interface_windows.py` 목의 배치 생성. → raspberry 명세서 §4 Phase 0 참조.

**완료 기준**
- [ ] `_Static_assert(sizeof(csi_raw_frame_t) == 624)` 통과
- [ ] occupancy 모드에서 `PKT_CSI_BATCH`가 발행되고 Pi가 파싱 성공
- [ ] `csi_len == 490` (245 pair) 실측 확인 — 5GHz ch48 설정에서 실제로 나오는 값 검증
- [ ] 50ms 주기 유지 확인 (오버런 카운터 0)

---

### Phase 1 — AGC 캘리브레이션 통합

현재 AGC 캘리브레이션은 별도 앱(`esp32c5/csi_recv_calibrate/`)에만 있고, SPI 시제품(`csi_recv_spi/`)에는 없다. 두 앱을 하나로 합친다.

이식 대상 (`csi_recv_calibrate/main/app_main.c` 577줄에서):

| 심볼 | 값/역할 |
|---|---|
| `CSI_TRAIN_COMMAND` | `"train"` |
| `CSI_TRAIN_DURATION_US` | `1000000LL` (약 1초 AGC 측정 윈도우) |
| `CSI_PHASE_TRAINING` | 페이즈 게이트 enum |
| `csi_train_cmd_task` | 스택 4096, prio 4, 20ms 폴링, 32B 라인 버퍼, stdin 논블로킹 |
| `csi_train_start()` | AGC 게인 샘플 1초 수집 → `esp_csi_gain_ctrl_set_rx_force_gain` |

**변경점**: 트리거를 stdin 라인(`"train"`)에서 **SPI `CMD_START_TRAIN`**으로 바꾼다. UART 트리거는 진단용으로 병행 유지.

**AGC 고정이 왜 중요한가** (재실감지 알고리즘 보고서 §4.3):
AGC 게인이 한 단계 바뀌면 물리적 사건 없이 CSI 진폭에 계단형 점프가 생긴다. 이동분산과 Welch PSD 모두 이를 "큰 변동"으로 읽는다. 30초 baseline 윈도우 안에 이것이 들어가면 `presence_mv_threshold`·`wander_baseline` 두 값이 모두 오염된다. 캘리브레이션 **이후**에도 같은 게인을 유지해야 하며, 그렇지 않으면 스케일 드리프트가 누적된다.

**완료 기준**
- [ ] `CMD_START_TRAIN` 수신 → 스트리밍 정지 → AGC 1초 측정 → force gain → 스트리밍 재개
- [ ] Pi의 `waiting_ack`(패킷 정지 감지) / `waiting_agc`(재개 감지) 페이즈가 정상 통과
- [ ] `PKT_TRAIN_DONE` 페이로드에 `base_rssi_mean/var`, `base_amp_mean/std` 포함

---

### Phase 2 — SPI 슬레이브 안정화

**필수 버그 수정** — `spi_slave_if.c:139`:

```c
/* 현재: 포인터 크기 아이템 큐를 만들고 */
g_tx_queue = xQueueCreate(TX_BUFFER_QUEUE_SIZE, sizeof(tx_buffer_t *));
/* ...~4KB 구조체를 값으로 밀어 넣는다 */
xQueueSendToBack(g_tx_queue, &buf, ...);
/* ...그리고 포인터로 읽는다 */
xQueuePeek(...);
```

아이템 크기와 실제 전송 크기가 불일치한다. 두 가지 중 하나로 정리:
- (a) 정적 버퍼 풀 + 포인터 큐 (권장 — 8KB 프레임에서는 값 복사 비용이 커진다)
- (b) `xQueueCreate(N, sizeof(tx_buffer_t))` 로 아이템 크기 정정

**기타**
- 프레임 오버런/드롭 카운터를 `csi_raw_frame_t.drop_count`에 정확히 반영
- 링버퍼 오버플로 시 **최신 프레임 우선** 정책 명시 (낙상은 실시간성이 생명)
- keepalive 프레임(페이로드 없이 magic+version만) 유지 — Pi의 `is_connected()` 판정 근거

**완료 기준**
- [ ] 8192B 프레임 24시간 연속 전송 시 큐 관련 크래시·누수 없음
- [ ] `drop_count`가 실제 드롭과 일치 (인위적 부하 시험)

---

### Phase 3 — 프로토콜 계약 통합

C `protocol.h`와 Python `protocol.py`가 **이미 드리프트했다**:

| 항목 | C가 내보냄 | Python이 모델링함 |
|---|---|---|
| `occupancy_fsm_get_status_json()` | `ts_ms, entry, motion, motion_count_3s, rssi, rssi_variance, base_rssi_mean, base_rssi_var, jitter_raw, jitter_smooth, thr_enter, thr_exit, wander, wander_thr, motion_state, occupancy_confirmed, req_mode_change` | `entry, motion, occupancy_confirmed, req_mode_change` 등 일부만 |
| `PKT_TRAIN_DONE` | `base_rssi_mean/var`, **`base_amp_mean`**, **`base_amp_std`**, `entry_threshold` | `base_amp_*`를 버림 (그런데 wander 임계값 계산에 필요) |

**해결**: `common/protocol.h`를 단일 원본으로 두고, Python 측 `protocol.py`를 여기서 **생성**하거나 최소한 CI에서 대조 검증한다. JSON 페이로드는 스키마를 정의해 C의 `snprintf` 포맷과 Pi의 pydantic 모델이 같은 정의에서 나오게 한다.

**완료 기준**
- [ ] C ↔ Python 상수 일치 검증 스크립트가 CI에서 통과
- [ ] JSON 필드 집합이 양측에서 동일

---

### Phase 4 — 운용 개선

| 항목 | 현재 | 목표 |
|---|---|---|
| 밴드/채널 | `app_main.c` 최상단 `#define USE_5G_BAND 1` — 변경 시 **TX·RX 양쪽 재플래시** | Kconfig(`menuconfig`) 승격 |
| AGC 성공 확인 | 프로토콜 없음. Pi가 경과시간으로 "정상으로 보임"을 추정만 함 | `PKT_ACK`에 AGC 결과(측정 게인값·성공 플래그) 실어 회신 |
| OTA | 없음 | G6 — 엣지 배포 경로. 실패 시 자동 복구되는 이중 파티션 검토 |
| 워치독 | 없음 | 태스크 워치독 + 비정상 시 재부팅 |

---

## 5. 핵심 구현 모듈 상세

### 5.1 `common/protocol.h` — 와이어 계약 단일 원본

```c
/* ESP-NOW (RX <-> TX) */
#define ESPNOW_PKT_TYPE_COUNTER  0x00   // TX->RX 트리거
#define ESPNOW_PKT_TYPE_CMD      0x01   // RX->TX 커맨드
#define ESPNOW_PKT_TYPE_ACK      0xFF   // TX->RX 응답
#define ESPNOW_CMD_SET_RATE      0x01

typedef struct {                        // 4 bytes
    uint8_t  pkt_type;  uint8_t cmd;  uint16_t pps;   // 50 또는 320
} __attribute__((packed)) espnow_cmd_t;

/* SPI (RX <-> RPi) */
#define SPI_FRAME_SIZE_SMALL     256    // JSON 프레임
#define SPI_FRAME_SIZE_LARGE     8192   // ★ 4096 → 8192 (Phase 0-B)
static const uint8_t SPI_MAGIC_RX_TO_RPi[] = {0xAB, 0xCD};
static const uint8_t SPI_MAGIC_RPi_TO_RX[] = {0xCD, 0xAB};
#define SPI_VERSION              0x01

#define SPI_MODE_TRAIN 0x00 | SPI_MODE_OCCUPANCY 0x01 | SPI_MODE_FALL_DETECT 0x02
#define PKT_TRAIN_PROGRESS 0x00 | PKT_TRAIN_DONE 0x01 | PKT_STATUS_JSON 0x02
#define PKT_CSI_BATCH      0x03 | PKT_CMD        0x10 | PKT_ACK          0xFF
#define CMD_START_TRAIN    0x00 | CMD_MODE_OCCUPANCY 0x10
#define CMD_MODE_FALL      0x11 | CMD_PING           0x20

typedef struct {                        // 8 bytes — Python: '<2sBBBBH'
    uint8_t  magic[2];  uint8_t version;  uint8_t mode;
    uint8_t  pkt_type;  uint8_t num_frames;  uint16_t payload_len;
} __attribute__((packed)) spi_frame_hdr_t;

typedef struct {                        // 4 bytes — Python: '<BHB'
    uint8_t cmd;  uint16_t param;  uint8_t reserved;   // START_TRAIN: param = 초
} __attribute__((packed)) rpi_cmd_payload_t;

/* CSI 원시 프레임 */
#define CSI_MAX_LEN 612                 // ★ 128 → 612 (Phase 0-B)

typedef struct {                        // ★ 140 → 626 bytes (메타 14B + 612B)
    uint32_t seq;                       //  4
    uint32_t timestamp_ms;              //  4  ESP32-C5 내부 타임스탬프
    int8_t   rssi;                      //  1
    uint8_t  noise_floor;               //  1
    uint16_t csi_len;                   //  2  ★ uint8_t → uint16_t (612 > 255)
    uint8_t  drop_count;                //  1
    uint8_t  reserved;                  //  1  ★ 신규 (정렬)
    int8_t   csi_data[CSI_MAX_LEN];     // 612 게인 보상된 I/Q 인터리브
} __attribute__((packed)) csi_raw_frame_t;

_Static_assert(sizeof(csi_raw_frame_t) == 626, "csi_raw_frame_t size must be 626 bytes");
```

> **`csi_len` 승격이 Phase 0-B의 핵심이다.** 기존 `uint8_t`는 최대 255라 245 pair(490B)조차 표현하지 못한다. Pi 측 `protocol.py`의 언패킹 포맷(`'<IIBBB'` → `'<IIbBHBB'`)과 `CsiRawFrame.size`(140 → 626)도 함께 고쳐야 한다.

### 5.2 `spi_slave_if.c` — SPI 슬레이브

**하드웨어 설정**

| 항목 | 값 |
|---|---|
| Host | `SPI2_HOST` |
| MOSI / MISO / SCLK / CS | GPIO **7 / 2 / 6 / 10** |
| DATA_READY | GPIO **3** (출력) — flush 시 high, RX 콜백에서 clear |
| DMA | `SPI_DMA_CH_AUTO` |
| 모드 | slave mode 0, `queue_size = 3`, `max_transfer_sz = SPI_FRAME_SIZE_LARGE` |

**공개 API** (`spi_slave_if.h`)

```c
void spi_slave_if_init(void);
void spi_slave_if_push_json(uint8_t pkt_type, const char *json);
void spi_slave_if_push_csi(const csi_raw_frame_t *frame);
bool spi_slave_if_recv_cmd(uint8_t *cmd_out, uint16_t *param_out);
void spi_slave_if_set_mode(uint8_t mode);
void spi_slave_if_flush_csi_batch(void);
void spi_slave_if_flush(void);           /* 50ms 주기 호출 */
```

**동작**
- `spi_slave_if_push_csi()`가 `g_csi_batch_buf`에 누적. 바이트 예산 또는 `CSI_BATCH_MAX_FRAMES` 초과 시 자동 flush
- `spi_slave_if_flush()` (50ms마다): 대기 중인 CSI 배치 flush → TX 버퍼 준비(큐가 비면 magic+version만 담은 keepalive) → `DATA_READY` high → 트랜잭션 큐잉
- `spi_slave_rx_callback`: 인바운드 버퍼에서 `magic == {0xCD,0xAB}` 및 `pkt_type == PKT_CMD` 확인 후 `xQueueSendFromISR`로 `{cmd, param}` 전달 (오프셋 8부터)
- **전이중**: Pi→ESP 커맨드와 ESP→Pi 데이터가 **같은 트랜잭션**에서 오간다

### 5.3 `app_main.c` — 모드 디스패치

```
spi_dispatch_task (50ms 루프)
  └─ spi_slave_if_recv_cmd() 드레인
       CMD_START_TRAIN     → agc_calibrate_start()
       CMD_MODE_OCCUPANCY  → spi_slave_if_set_mode(SPI_MODE_OCCUPANCY)
                             espnow_send_cmd(SET_RATE, 50)
       CMD_MODE_FALL       → spi_slave_if_set_mode(SPI_MODE_FALL_DETECT)
                             espnow_send_cmd(SET_RATE, 166)   ← §9-1 권고 반영 시
       CMD_PING            → 로그만
  └─ spi_slave_if_flush()
```

CSI 수신 콜백 (모드별):

| 모드 | 동작 (Phase 0-A 반영 후) |
|---|---|
| `TRAIN` | AGC 측정. `PKT_TRAIN_PROGRESS` 주기 발행, 완료 시 NVS 저장 + `PKT_TRAIN_DONE` |
| `OCCUPANCY` | **`csi_raw_frame_t` 패킹 → `push_csi()`** (신규) + `PKT_STATUS_JSON` 진단 병행 |
| `FALL_DETECT` | `csi_raw_frame_t` 패킹 → `push_csi()` |

`espnow_send_cmd(cmd, pps)`는 TX 노드에 커맨드를 보내고 ACK를 기다린다 — **실제로 바뀌는 것은 TX의 비콘 레이트**이고 RX는 수신되는 대로 받는다.

### 5.4 `occupancy_fsm.c` — 폴백 전용으로 격하

재실 판정 권위는 라즈베리파이에 있다(G7). 그러나 550줄의 검증된 코드를 버릴 이유는 없으므로 **컴파일은 유지하되 기본 비활성**한다.

| 구분 | 정책 |
|---|---|
| 기본 상태 | 비활성 (`CONFIG_WIFIGUARD_ONDEVICE_FSM=n`) |
| 활성 시 용도 | Pi 장애·SPI 단절 구간에서의 로컬 폴백. LED 표시 등 |
| `PKT_STATUS_JSON` | **진단용으로 항상 발행** — Pi가 자신의 판정과 대조해 알고리즘 검증에 활용 |
| 대시보드 노출 | ❌ 절대 사용하지 않음. 재실 상태는 항상 Pi의 `PresenceDetector` 출력 |

**참고로 남길 온디바이스 FSM 파라미터** (Pi 알고리즘과 **다른 계보**임에 주의):

```
JITTER_EWM_ALPHA        0.3     DYNAMIC_THRESHOLD_ENTER  2.5 (mean + 2.5σ)
WANDER_WINDOW_SIZE      100     DYNAMIC_THRESHOLD_EXIT   1.0 (mean + 1.0σ)
WANDER_THRESHOLD_MULT   2.0     MOTION_DEBOUNCE_ENTER    3
MIN_JITTER_FLOOR        0.02    MOTION_DEBOUNCE_EXIT     5
RSSI_WINDOW_SIZE        10      MOTION_CONFIRM_WINDOW    60 (3s @50Hz)
JITTER_HIST_SIZE        20      MOTION_CONFIRM_COUNT     10
ENTRY_THRESHOLD_MULT    3.0     EXIT_NO_MOTION_MS        30000
TRAIN_WARMUP_S          10      EXIT_RSSI_VAR_MULT       2.0
TRAIN_COLLECT_S         20
```

> **혼동 금지**: 위 `wander`(진폭 평균의 baseline 대비 편차)는 Pi 측 `wander`(0.1–0.5Hz Welch band energy 비율)와 **이름만 같고 완전히 다른 신호**다. Pi 명세서 §5.3 참조.

---

## 6. 현재 구현 코드에서 참고·이식할 코드

### 6.1 이식 매핑

| 현재 위치 | 신규 위치 | 변경 |
|---|---|---|
| `csi_fall/infra/esp32c5/csi_recv_spi/main/spi_slave_if.c/.h` | `csi_recv_spi/main/spi_slave_if.c/.h` | Phase 2 큐 버그 수정, 8192B 대응 |
| `csi_fall/infra/esp32c5/csi_recv_spi/main/protocol.h` | `common/protocol.h` | `CSI_MAX_LEN` 612, `SPI_FRAME_SIZE_LARGE` 8192, `csi_len` uint16 승격 |
| `csi_fall/infra/esp32c5/csi_recv_spi/main/occupancy_fsm.c/.h` | `csi_recv_spi/main/occupancy_fsm.c/.h` | Kconfig 게이트 추가, 기본 비활성 |
| `csi_fall/infra/esp32c5/csi_recv_spi/main/app_main.c` | `csi_recv_spi/main/app_main.c` | Phase 0-A: occupancy 모드 CSI 스트리밍 추가 |
| `csi_fall/infra/esp32c5/csi_send/main/app_main.c` | `csi_send/main/app_main.c` | 무변경 (SET_RATE 값만 §9-1에 따라) |
| `csi_fall/esp32c5/csi_recv_calibrate/main/app_main.c` (L85-86, L321, L473) | `csi_recv_spi/main/agc_calibrate.c/.h` | 트리거를 stdin → SPI 커맨드로 |
| `csi_fall/esp32c5/csi_recv/main/app_main.c` (L73-135) | `csi_recv_uart/` (참조용) | UART 바이너리 프레임 정의의 원본. 빌드 제외 |
| `csi_fall/infra/esp32c5/csi_recv_spi/tools/web_serial_monitor_fall.html` | `tools/` | 무변경 |

### 6.2 참조만 하고 이식하지 않는 것

| 위치 | 이유 |
|---|---|
| `csi_fall/esp32c5/CLAUDE.md` | 아키텍처 문서. 내용은 새 `CLAUDE.md`로 재작성 |
| `csi_fall/esp32c5/csi_recv_calibrate/optimization_plan.md` | 최적화 검토 이력 |
| `Guardian Angel Alert/backend/csi/protocol.py` | **UART** 경로 파서(0xA55A, 46B 헤더, 612B). SPI와 다른 계약이지만 **`csi_len=612`가 어디서 왔는지의 근거** |

---

## 7. 인접 레포와의 인터페이스 계약

### 7.1 ESP32-C5 RX → Raspberry Pi (SPI)

```
   [ESP32-C5 RX · SPI Slave]                    [Raspberry Pi · SPI Master]
        GPIO 7  MOSI  ◄───────────────────────────  BCM 10 (SPI0 MOSI)
        GPIO 2  MISO  ───────────────────────────►  BCM  9 (SPI0 MISO)
        GPIO 6  SCLK  ◄───────────────────────────  BCM 11 (SPI0 SCLK)
        GPIO 10 CS    ◄───────────────────────────  BCM  8 (SPI0 CE0)
        GPIO 3  DREADY ──────────────────────────►  BCM 25 (인터럽트, RISING)
                                                     spidev(0,0) 10MHz mode0
```

**트랜잭션**: 50ms 주기, **8192B 전이중 1회**

```
MOSI (Pi → ESP)                          MISO (ESP → Pi)
┌────────────────────────┐               ┌────────────────────────────────┐
│ [0:8]  spi_frame_hdr_t │               │ [0:8]  spi_frame_hdr_t         │
│        magic {CD,AB}   │               │        magic {AB,CD}           │
│        pkt_type=PKT_CMD│               │        mode, pkt_type,         │
│ [8:12] rpi_cmd_payload │               │        num_frames, payload_len │
│        cmd, param      │               │ [8:..] payload                 │
│ [12:]  0 패딩          │               │        · JSON (≤ ~512B) 또는   │
└────────────────────────┘               │        · N × csi_raw_frame_t   │
                                          └────────────────────────────────┘
```

| 방향 | 패킷 | 조건 |
|---|---|---|
| ESP→Pi | `PKT_CSI_BATCH` | **모든 모드**에서 발행 (Phase 0-A). `num_frames`만큼의 `csi_raw_frame_t` |
| ESP→Pi | `PKT_STATUS_JSON` | occupancy 모드, 진단용. **재실 판정 근거로 사용 금지** |
| ESP→Pi | `PKT_TRAIN_PROGRESS` / `PKT_TRAIN_DONE` | 캘리브레이션 중 |
| ESP→Pi | keepalive (payload_len=0) | 보낼 것이 없을 때. Pi의 연결 판정용 |
| Pi→ESP | `PKT_CMD` + `CMD_*` | 이벤트 |

### 7.2 ESP32-C5 TX ↔ RX (ESP-NOW)

| 항목 | 값 |
|---|---|
| 밴드/채널 | 5GHz ch48 (`USE_5G_BAND 1`) |
| TX MAC | 스푸핑 `1a:00:00:00:00:00` |
| RX 필터 | `CONFIG_CSI_SEND_MAC` |
| 레이트 | occupancy 50pps / fall 166 또는 320pps (§9-1) |
| 커맨드 | `espnow_cmd_t{pkt_type=CMD, cmd=SET_RATE, pps}` → ACK 대기 |

### 7.3 밴드 분리 (설계 근거)

감지 링크는 **5GHz**, Pi의 클라우드 업링크는 **2.4GHz**. 업링크 트래픽이 CSI 측정 채널을 오염시키지 않는다. 다만 Pi와 RX를 물리적으로 붙여 두므로 **근접 배치에서 2.4GHz 송신이 5GHz 수신에 주는 영향은 실측 항목**으로 남는다(실행계획 §0.0-3).

---

## 8. 리스크

| # | 리스크 | 영향 | 완화 |
|---|---|---|---|
| R1 | `csi_len`이 `uint8_t`라 490/612 표현 불가 | Phase 0-B가 그대로는 성립 안 함 | `uint16_t` 승격 + 구조체 재산정 (§5.1 주석) |
| R2 | 8192B DMA 트랜잭션이 ESP32-C5에서 제약될 수 있음 | 프레임 확대 불가 | `max_transfer_sz` 상향 후 실측. 안 되면 25ms × 4096B 2회로 분할 |
| R3 | `g_tx_queue` 버그가 8KB에서 더 심각해짐 | 스택 오버플로·크래시 | Phase 2에서 포인터 풀로 전환 |
| R4 | 5GHz ch48에서 실제 `csi_len`이 245 pair가 아닐 수 있음 | 모델 입력 불일치 | Phase 0 완료 기준에 실측 검증 포함 |
| R5 | AGC 성공을 확인할 프로토콜이 없음 | 캘리브레이션 무증상 실패 | Phase 4 — `PKT_ACK`에 결과 실어 회신 |
| R6 | 밴드 변경이 양쪽 재플래시를 요구 | 현장 운용 부담 | Phase 4 — Kconfig 승격 |

---

## 9. 미결정 사항

1. **[최우선] 낙상 모드 CSI 레이트: 320Hz vs 166.67Hz.**
   시제품은 320Hz다. 그러나 **현 체크포인트는 약 166.67Hz 수집 데이터로 학습**되었다(`Window3BestModelInference/PACKAGE_MANIFEST.json`). 피처추출이 측정 fs를 0.25Hz 격자로 양자화해 리샘플하므로 동작 자체는 하지만, S3 스칼로그램의 주파수축이 `linspace(1.0, min(170, fs/2))`라 **fs가 바뀌면 스칼로그램에 담기는 주파수 내용 자체가 달라진다**:

   | fs | 스칼로그램 주파수 범위 |
   |---|---|
   | 166.67Hz | 1 ~ 83.3Hz |
   | 320Hz | 1 ~ 160Hz |

   → **166.67Hz 권고**. 학습 분포와 일치하고 SPI 대역폭도 절반이 된다. 320Hz를 유지하려면 재학습 또는 성능 재검증이 필요하다.

2. **occupancy 모드 CSI 레이트 50Hz의 적정성.**
   Pi의 재실 신호체인은 100Hz 그리드로 리샘플하고 0.5–50Hz 밴드패스를 건다. 원본이 50Hz면 `safe_bandpass`가 상한을 fs/2−1 = 24Hz로 클램프한다. 재실 판정에 실질 영향이 있는지 실측 필요. 후보: 50Hz 유지 + 밴드패스 상한 하향 / 100Hz로 상향.

3. **`occupancy_fsm.c` 폴백을 실제로 유지할 것인가.**
   유지 비용(코드·검증)과 이득(Pi 장애 시 로컬 재실 표시)의 트레이드오프. 초기에는 비활성 컴파일만 유지하고, Pi 안정화 이후 판단.

4. **`managed_components/` 커밋 여부.** 현재 커밋되어 있다. `dependencies.lock` 기반 재현으로 전환할지 결정.

5. **OTA 경로.** 가정 설치 기기라 물리 접근이 어렵다. 이중 파티션 vs 단계적 배포 — G6과 함께 설계.

---

## 참고 문서

- `CSI-Guard_구현현황_및_완성목표_v1.0_20260729.md` §2 (타깃 아키텍처), §4.4 (G7)
- `CSI-Guard_완성목표_실행계획_v1.0_20260729.md` §0.0 (확정 물리 구성), §3 (G2 MQTT)
- `CSI-Guard_재실감지_알고리즘_상태머신_보고서_v2.0_20260720.md` §4.3 (AGC 고정 근거)
- `csi_fall/infra/pipline.md` — SPI 시제품의 시나리오별 데이터 흐름
- `csi_fall/esp32c5/CLAUDE.md` — UART 경로 와이어 프로토콜 원본
- [WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md](WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md) §4 Phase 0 (Pi 측 동시 수정 사항)
