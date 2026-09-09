# WIFI-GUARD-raspberry 레포 개발 명세서

**엣지 게이트웨이 · 재실감지 및 피처 추출 · MQTT 퍼블리셔**

v1.0 · 작성일 2026-08-04

> 4-레포 분리 중 **엣지** 레포의 명세다. 이 레포가 4개 중 신규 구현 비중이 가장 크다.
> 전제 문서: `CSI-Guard_구현현황_및_완성목표_v1.0_20260729.md`(G7), `CSI-Guard_완성목표_실행계획_v1.0_20260729.md`(§8), `CSI-Guard_재실감지_알고리즘_상태머신_보고서_v2.0_20260720.md`
> 인접 레포: [esp](WIFI-GUARD_레포명세_esp_v1.0_20260804.md) · [backend](WIFI-GUARD_레포명세_backend_v1.0_20260804.md)

---

## 1. 레포 개요 · 책임 경계

### 1.1 한 줄 정의

라즈베리파이에서 상시 동작하는 엣지 서비스. **CSI를 SPI로 받아 재실을 판정하고, 활동 구간의 피처를 추출해 클라우드로 올린다.**

```
[ESP32-C5 RX] ──SPI 8192B/50ms──► [Raspberry Pi]
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
              PresenceLoop        FeatureLoop        Calibration
              (상시, 0.25s)      (게이팅 시, 0.25s)   (61초 4단계)
                    │                  │                  │
                    └──────────────────┴──────────────────┘
                                       ▼
                          MQTT over TLS 8883 (아웃바운드 전용)
                                       ▼
                      [AWS IoT Core] ──IoT Rule──► [Kafka]
```

### 1.2 하는 것

| # | 책임 | 근거 |
|---|---|---|
| P1 | SPI 마스터로 ESP32-C5에서 CSI 수신 · 링버퍼 적재 | 현행 `SerialReader`+`RingBuffer` 이관 |
| P2 | **재실/움직임 감지 (이 레포가 유일한 권위)** | G7 — 상시 동작·저지연이 필요하고 네트워크와 무관해야 함 |
| P3 | **게이팅** — 활동 구간에만 클라우드 업로드 | G7 부수효과. G2·G3의 대역폭 전제 |
| P4 | **S3 스칼로그램 + PCA-ACF 피처 추출** | 인프라구축 §1: "피처 추출(PCA/S3 등) → MQTT over TLS" |
| P5 | MQTT/TLS 퍼블리시 (mTLS, 아웃바운드 8883) | G2 |
| P6 | 4단계 캘리브레이션 오케스트레이션 (61초) | ESP에 `train` 명령을 보내는 주체 |
| P7 | 네트워크 단절 시 로컬 버퍼링 후 복구 시 업로드 | 실행계획 §2.1-7 |
| P8 | 24시간 상시 운용 (systemd, 워치독, 정전 복구) | 실행계획 §8.2 |

### 1.3 하지 않는 것

| 항목 | 어디서 | 근거 |
|---|---|---|
| **낙상 판정** | 클라우드 백엔드 | 확정 결정 — Pi는 피처 추출까지만. 모델 갱신·공간별 적응을 중앙 관리(G4·G6) |
| 신호 수집 하드웨어 제어 | ESP32-C5 펌웨어 | AGC 고정은 ESP가, 절차 진행은 Pi가 |
| 영속 저장 (이력·이벤트) | 클라우드 DB | Pi의 SQLite는 **단절 구간 임시 버퍼 전용** |
| 사용자 인증·대시보드 | 백엔드 / 프론트엔드 | 시제품 `web_server.py`의 로컬 UI는 승계하지 않음 |

> **네트워크가 끊기면**: 재실감지는 계속 동작한다(P2). 낙상 감지는 **중단된다**. 안전 기능이므로 이 상태를 무증상으로 넘기면 안 되고, `telemetry`로 명시 보고 + 복구 시 버퍼 업로드해야 한다(§9-5).

---

## 2. 핵심 기능 목록

| ID | 기능 | 출처 | 비고 |
|---|---|---|---|
| F-P01 | SPI 마스터 수신 + DATA_READY 인터럽트 | 이식 | `infra/raspberry/spi_interface.py` |
| F-P02 | 개발 PC용 SPI 목 | 이식 | `spi_interface_windows.py` — 유지 가치 큼 |
| F-P03 | 링버퍼 (30초, 타임스탬프 언랩, 리부팅 감지) | 이식 | `backend/csi/buffer.py` |
| F-P04 | 재실 MV 감지 (3초 윈도우, 0.25초 stride) | 이식 | `backend/presence/` |
| F-P05 | Wander 감지 (10초 윈도우, Welch PSD) | 이식 | 동상 |
| F-P06 | 재실 상태머신 (PRESENT/ABSENT) | 이식 | `presence/state_machine.py` |
| F-P07 | 4단계 캘리브레이션 (61초) | 이식 | `backend/onboarding.py` |
| F-P08 | S3 스칼로그램 추출 (224×224) | 이식 | `backend/features/common.py` |
| F-P09 | PCA-ACF 추출 (1×128×64) | 이식 | `backend/features/acf.py` |
| F-P10 | 피처 파이프라인 오케스트레이션 | 이식 | `backend/features/realtime.py` |
| **F-P11** | **게이팅 — 활동 구간만 업로드** | **신규** | 대역폭의 핵심 |
| **F-P12** | **MQTT/TLS 퍼블리셔 (mTLS)** | **신규** | paho-mqtt |
| **F-P13** | **MQTT 커맨드 구독 (cmd/ack)** | **신규** | 원격 캘리브레이션·설정 |
| **F-P14** | **페이로드 양자화 + zstd 압축** | **신규** | 232KB/윈도우 완화 |
| **F-P15** | **SQLite 스풀 (단절 버퍼)** | **신규** | |
| **F-P16** | **systemd 서비스 + 워치독** | **신규** | 24h 상시 운용 |
| F-P17 | Pi CPU 벤치마크 | 확장 | `bench_pipeline.py` |

---

## 3. 디렉토리 구조

```
WIFI-GUARD-raspberry/
├── README.md
├── pyproject.toml                  (또는 requirements.txt + requirements-pi.txt)
├── deploy/
│   ├── wifiguard-edge.service      systemd unit
│   ├── watchdog.conf               하드웨어 워치독
│   ├── install.sh                  SPI 활성화, gpio 그룹, tmpfs 로그
│   └── certs/                      mTLS 클라이언트 인증서 (gitignore)
├── config/
│   ├── default.toml                기본 설정
│   └── device.toml.example         기기별 (facility_id, device_id, 브로커)
├── src/wifiguard_edge/
│   ├── __main__.py                 엔트리포인트 · 스레드 기동/정지
│   ├── config.py                   설정 로딩 · 런타임 갱신
│   │
│   ├── transport/                  ── 하드웨어 전송 계층
│   │   ├── protocol.py             SPI 와이어 계약 (ESP common/protocol.h 미러)
│   │   ├── spi_reader.py           ★ SPI 마스터. SerialReader 덕타이핑 계약 구현
│   │   ├── spi_mock.py             개발 PC용 목
│   │   └── base.py                 Transport 프로토콜 정의 (Protocol/ABC)
│   │
│   ├── csi/
│   │   └── buffer.py               RingBuffer (30초)
│   │
│   ├── presence/                   ── 재실감지 (무변경 이식)
│   │   ├── config.py               PresenceConfig
│   │   ├── preprocessing.py        리샘플 · 밴드패스 · 이동분산 · q-value
│   │   ├── streaming_features.py   compute_final_signal()
│   │   └── state_machine.py        PresenceDetector (PRESENT/ABSENT)
│   ├── presence_loop.py            PresenceLoop 스레드 (0.25s)
│   │
│   ├── features/                   ── 낙상 피처 추출 (무변경 이식 + 최적화)
│   │   ├── realtime.py             FeatureConfig, extract_window_features()
│   │   ├── common.py               S3 스칼로그램 (CWT + 3단 디노이즈)
│   │   └── acf.py                  PCA-ACF
│   ├── feature_loop.py             ★ 신규: FeatureLoop 스레드 (게이팅 연동)
│   │
│   ├── gating.py                   ★ 신규: 업로드 게이트 정책
│   │
│   ├── calibration/
│   │   └── onboarding.py           4단계 61초 절차
│   │
│   ├── mqtt/                       ── 클라우드 업링크
│   │   ├── publisher.py            ★ 신규: paho-mqtt + mTLS
│   │   ├── command.py              ★ 신규: cmd 구독 · ack 발행
│   │   └── codec.py                ★ 신규: MessagePack + 양자화 + zstd
│   │
│   ├── spool/
│   │   └── sqlite_spool.py         ★ 신규: 단절 구간 버퍼
│   │
│   └── health.py                   ★ 신규: 헬스 메트릭 수집
├── tools/
│   ├── bench_pipeline.py           Pi CPU 지연·메모리·발열 실측
│   └── replay.py                   저장된 CSI로 파이프라인 재현
└── tests/
```

---

## 4. 구현 단계

### Phase 0 — SPI 전송 계층 확대 (esp 레포와 동시 진행)

[esp 명세서 §4 Phase 0](WIFI-GUARD_레포명세_esp_v1.0_20260804.md)의 펌웨어 변경에 맞춰 Pi 측을 수정한다.

| 항목 | 변경 전 (시제품) | 변경 후 |
|---|---|---|
| `SPI_FRAME_SIZE_LARGE` | 4096 | **8192** |
| `CSI_MAX_LEN` | 128 (64 pair) | **612** (306 pair, 245 pair 수용) |
| `csi_len` 필드 | `uint8_t` (최대 255) | **`uint16_t`** — 490/612 표현 필요 |
| `csi_raw_frame_t` | 140B | **626B** (메타 14B + 612B) |
| `_spi_transfer()` | 항상 4096B 전송 | 8192B |
| `CsiRawFrame.unpack()` | `payload[i*140:(i+1)*140]` | `payload[i*626:(i+1)*626]` |
| occupancy 모드 수신 | `PKT_STATUS_JSON`만 | **`PKT_CSI_BATCH` 추가** |

**50ms당 프레임 수 재검산** (626B 기준, 페이로드 예산 8192−8 = 8,184B):

| 모드 · 레이트 | 프레임 | 페이로드 | 판정 |
|---|---:|---:|:---:|
| occupancy 50Hz | 3 | 1,878B | ✓ |
| fall 166.67Hz | 9 | 5,634B | ✓ |
| fall 320Hz | 16 | 10,016B | ✗ → 25ms flush 시 8프레임 5,008B ✓ |

→ §9-1의 **166.67Hz 권고**를 채택하면 8192B/50ms 한 번으로 전부 해결된다.

**완료 기준**
- [ ] `tests/test_protocol.py`가 626B 레이아웃으로 통과 (기존 `infra/raspberry/test_protocol.py` 확장)
- [ ] 목(`spi_mock.py`)이 새 크기로 배치 생성
- [ ] 실기에서 occupancy·fall 양 모드 CSI 파싱 성공, 체크섬/시퀀스 갭 0

---

### Phase 1 — 전송 계층 어댑터화 + 재실 파이프라인 이식

**핵심 설계**: 현행 `SerialReader`의 **덕타이핑 계약을 SPI 리더가 그대로 구현**하면, 상위 파이프라인(`RingBuffer` 소비자 전부, `run_calibration`)을 **한 줄도 고치지 않고** 재사용할 수 있다. 실행계획 §3.1.5가 지정한 이음매다.

```python
class Transport(Protocol):
    running: bool          # 연결 여부
    packet_count: int      # 파싱 성공 프레임 누적 — 캘리브레이션 침묵/재개 감지용
    def send_line(self, text: str) -> bool: ...      # SPI에서는 CMD로 매핑
    def get_window(self, seconds: float) -> tuple | None: ...
```

`send_line("train")` → `SpiReader`에서는 `send_cmd(CMD_START_TRAIN, TRAIN_TOTAL_S)`로 매핑한다. 이렇게 하면 `onboarding.run_calibration()`이 무변경으로 동작한다.

**이식**: `backend/presence/` 4개 모듈 + `presence_loop.py` + `csi/buffer.py` → **무변경**.

**주의할 계약**:
- `RingBuffer`는 시각을 **초**로 반환하고, 이식된 신호체인은 **µs**를 기대한다 → 호출부에서 `times * 1e6`. 현행 `presence_loop.py`가 이미 그렇게 한다. 유지할 것.
- `compute_final_signal()`은 `window_sec`/`stride_sec` 인자를 받지만 **쓰지 않는다**. 실제 윈도우 길이는 `ring.get_window(seconds)`가 반환한 것으로 결정된다. 리팩터 시 오해 금지.
- `PresenceConfig`는 세 곳에서 쓰기가 일어난다(캘리브레이션 executor 스레드 / 설정 갱신 / presence 스레드가 읽기). **현재 락이 없다.** 이관 시 락 또는 불변 스냅샷 교체 방식으로 정리 권장.
- `PresenceDetector.__init__`의 기본값(`mv_threshold=2.5`, `wander_ratio_threshold=2.0`, `presence_timeout_s=6.0`)과 `PresenceConfig` 기본값(2.0 / 1.8 / 10.0)이 **다르다**. 런타임에는 매 틱 `set_thresholds()`가 덮어쓰므로 `PresenceConfig` 값이 적용된다. 혼동 방지를 위해 이관 시 일치시킬 것.

**완료 기준**
- [ ] Pi에서 `PresenceLoop`가 0.25초 stride를 지키며 상시 동작 (드리프트 < 10ms)
- [ ] `spi_mock.py`로 개발 PC에서도 동일 코드 경로 실행
- [ ] 사람 입·퇴실 시나리오에서 PRESENT/ABSENT 전이가 현행 백엔드와 동일

---

### Phase 2 — 캘리브레이션 이관

`backend/onboarding.py`를 그대로 옮긴다. 시리얼/SPI write 권한이 필요하므로 **반드시 엣지에 남아야 한다**.

```
① leaving      30s   설치자 퇴실 대기. 이 단계 전에는 어떤 명령도 보내지 않는다
② waiting_ack  ~0.2s send_line("train") 후 packet_count 정지 확인 (침묵 = 명령 수신)
③ waiting_agc  ~1s   packet_count 재증가 확인 (AGC 안정화 후 스트리밍 재개)
④ measuring    30s   조용한 공간에서 baseline 윈도우 수집
                     ─────────────────────────────────────  총 약 61초
```

`silence_confirm_s = 0.2`의 근거: 921600 baud에서 ~650B 프레임의 UART 드레인 시간 ~7ms를 넘기면서, 펌웨어의 ~1초 AGC 윈도우 아래로 ~0.8초 여유를 남기는 값. **SPI로 전환하면 드레인 특성이 달라지므로 이 값을 재산정해야 한다** (§9-3).

산출:
- `presence_mv_threshold = max(mean + 2.0·std, 0.3)` — IQR(Tukey k=1.5) 이상치 제거 후. 50% 초과가 제거될 상황이면 원본 배열로 복귀
- `wander_baseline = max(band_energy, 0.05)` — Welch PSD 적분은 스칼라 하나라 평균낼 시계열이 없다

두 값 모두 살아있는 `PresenceConfig`에 즉시 반영되어 재시작 없이 적용된다. 실패 시 무조건 `phase="error"`로 귀결시켜 재시도가 영구히 막히지 않게 하는 처리도 그대로 유지.

**신규**: MQTT `cmd` 토픽으로도 캘리브레이션을 시작할 수 있어야 한다(원격 재설정). 진행 상황은 `ack` 토픽으로 발행.

**완료 기준**
- [ ] 로컬(HTTP) · 원격(MQTT cmd) 양쪽에서 캘리브레이션 시작 가능
- [ ] 4단계 페이즈가 `ack` 토픽으로 실시간 보고
- [ ] 실패 시 항상 `error` 페이즈로 종료 (영구 잠금 없음)

---

### Phase 3 — 피처 추출 이식 및 Pi 최적화 ★ 최대 난관

`backend/features/`를 이식한다. **이 Phase가 이 레포에서 가장 위험하다.** 현행 42ms/window는 개발 PC(MPS 우선) 기준이며 **Pi CPU 실측이 없다**.

#### 3-1. 먼저 벤치마크

`bench_pipeline.py`를 Pi에서 돌려 기준선을 확보한다. 이 수치가 나오기 전에는 최적화 목표를 세울 수 없다(실행계획 §8.1-4와 동일한 논리).

```bash
python tools/bench_pipeline.py --fs 166.67 --subcarriers 245 --iterations 100
# 목표: total_ms p90 < 250ms (0.25초 stride 예산)
```

#### 3-2. 알려진 병목 4곳

| 병목 | 현재 | 대응 |
|---|---|---|
| **`freq_to_scale`** | 호출당 **약 0.5초**. fs·윈도우 길이에 결정적이라 캐시한다. 측정 fs를 **0.25Hz 격자로 양자화**해 캐시(최대 64엔트리)를 적중시킴 | **최우선 이식 항목.** 캐시가 빠지면 즉시 예산 초과 |
| **`vertical_denoise` / `horizontal_denoise`** | 224×224 격자 위 중첩 파이썬 루프. 초당 4회 | **벡터화 또는 numba 선행 필요.** Pi에서 가장 유력한 예산 초과 지점 |
| **`resample_signal`** (presence) | 프레임마다 도는 순수 파이썬 루프 (`preprocessing.py`) | numpy `interp` 벡터화 검토 |
| **`csi_to_amplitude`류 I/Q 루프** | 시제품 `fall_detector.py`가 파이썬 루프로 `sqrt(I²+Q²)` | 승계하지 않음(§6.2). 새 코드는 `np.frombuffer` + 벡터 연산 |

#### 3-3. `ssqueezepy` ARM64 리스크

S3 스칼로그램의 CWT는 `ssqueezepy==0.6.6`(내부적으로 `numba` 의존)을 쓴다. **ARM64 휠 가용성이 불확실하다.** `features/common.py`에 이미 있는 **NumPy 전용 `fallback_cwt`(6-cycle complex Morlet 컨볼루션) 경로를 반드시 유지**하고, 두 경로의 출력 동등성을 검증한다.

#### 3-4. 피처 계약 (변경 금지 — 모델이 이 형태로 학습됨)

```python
@dataclass(frozen=True)
class FeatureConfig:
    window_seconds = 3.0            target_subcarriers = 30
    train_raw_pairs = 245           train_drop_pair_indices = (121, 122)
    omega = 30                      moving_variance_radius_seconds = 0.4
    freq_min_hz = 1.0               freq_max_hz = 170.0
    image_size = 224                th_scmax = 1.0      kappa = 1.0
    carrier_hz = 2.4e9
    acf_lag_seconds = 0.4           acf_lag_output_bins = 128
    acf_time_bins = 64              acf_clip_percentile = 99.5
    fs_quantize_hz = 0.25
```

`extract_window_features(times, amplitude, config) -> WindowFeatures`:

```
① times.size < 8 또는 span < 2.7s(= 3.0 × 0.9) → ValueError
② 비증가 타임스탬프 제거 (np.diff(times) > 0)
③ fs_hz = 1 / median(양수 diff), 0.25Hz 격자로 양자화   ← CWT 캐시 적중 핵심
④ select_subcarrier_indices: n_available == 245면 pair 121,122 드롭 후
   linspace 균등 선택 30개
⑤ resample_uniform: 스트림별 np.interp → (grid_count, 30) float32
⑥ window = resampled[-round(3.0 * fs_hz):]        (166.67Hz → 500 샘플)
⑦ select_streams(omega=30, w_radius=round(0.4*fs)): q = max(mv)/mean(mv) 상위 30,
   정규화 q ≥ 1/len 유지
⑧ select_pc_signal: 중심화 → SVD, 고유비 > 1/n_cols 후보 → q-metric 재선택
   → 선택된 PC들의 **합**이 1-D 모션 신호
⑨ compute_s3_scalogram → (224, 224) float32
     freqs = linspace(1.0, min(170, fs/2 - 1e-6), 224) 내림차순
     ssqueezepy.cwt(gmw, l1_norm=True)  [또는 fallback_cwt]
     → |W| / max → general_denoise → vertical_denoise → horizontal_denoise
     → resize_time_axis(224)
⑩ compute_pca_acf → (1, 128, 64) float32
     z-score → lag_steps = round(0.4 * fs) → lag-product map
     → 시간축 64로 리사이즈 → 99.5 퍼센타일 클립·정규화 [-1,1] → lag축 128
```

**완료 기준**
- [ ] Pi CPU에서 `total_ms` p90 < 250ms
- [ ] `ssqueezepy` 경로와 `fallback_cwt` 경로의 출력이 수치적으로 일치 (허용오차 명시)
- [ ] 개발 PC 백엔드의 피처와 Pi 피처가 동일 입력에서 bit-identical 또는 명시된 오차 이내
- [ ] 24시간 연속 운용 시 메모리 누수 없음, 발열 스로틀링 없음

---

### Phase 4 — 게이팅 + MQTT 퍼블리셔

#### 4-1. 게이팅 (`gating.py`)

**이것이 클라우드 비용과 대역폭을 결정한다.**

| 재실 상태 | 발행 토픽 | 주기 |
|---|---|---|
| ABSENT | `telemetry`만 (헬스 메트릭 경량) | 저빈도 (예: 5초) |
| ABSENT + 활동 감지 (MV ≥ 임계값) | `presence` + `window` | 4Hz |
| PRESENT | `presence` + `window` | 4Hz |
| PRESENT + 무활동 지속 | `presence`만, `window` 중단 | 4Hz / — |

정확한 게이트 조건은 튜닝 대상이지만 **원칙은 "사람이 없는 구간의 원시/피처 데이터는 올리지 않는다"**이다(인프라구축 §시나리오A-2).

#### 4-2. 대역폭 산정 — 이것이 리스크다

확정 결정에 따라 **Pi가 피처를 만들어 올린다.** 그 비용:

| 페이로드 | 크기/윈도우 | 4Hz 시 |
|---|---:|---:|
| S3 `(224,224)` float32 | 200.7 KB | 802.8 KB/s |
| PCA-ACF `(1,128,64)` float32 | 32.0 KB | 128.0 KB/s |
| **합계 (무압축)** | **232.7 KB** | **930.8 KB/s ≈ 7.4 Mbps** |
| float16 변환 | 116.4 KB | 3.7 Mbps |
| + int8 양자화 | 58.2 KB | 1.86 Mbps |
| + zstd (S3는 디노이즈로 0이 많아 압축률 높음, 추정 3~5×) | 12~20 KB | **0.4~0.6 Mbps** |
| **× 게이팅 20%** | — | **0.08~0.12 Mbps** |

`FACILITY 구상도` §5는 **원시 CSI 윈도우(~50KB)를 올리고 클라우드에서 피처를 뽑는 편이 대역폭상 유리**하다고 지적한다(피처는 그 4~5배). 이번 아키텍처는 그 비용을 감수하는 선택이므로, **게이팅 + 양자화 + 압축이 선택이 아니라 필수 전제**다.

→ 위 표의 추정치를 **실측으로 대체하는 것이 Phase 4의 완료 기준**이다.

#### 4-3. MQTT 퍼블리셔 (`mqtt/publisher.py`)

| 항목 | 값 |
|---|---|
| 클라이언트 | `paho-mqtt` (엣지). 백엔드 측은 `aiomqtt` |
| 브로커 | AWS IoT Core (운영) / Mosquitto (로컬 개발) |
| 보안 | **mTLS 기기별 인증서**, 아웃바운드 **8883**만. 포트포워딩·고정 IP 불필요 |
| 직렬화 | **MessagePack** (JSON은 고빈도에 오버헤드가 큼) |
| 압축 | **zstd** + float16/int8 양자화 |
| QoS | `presence`/`telemetry` = 0 또는 1, `window` = 0 (최신성 우선), `cmd`/`ack` = 1 |

#### 4-4. 커맨드 채널 (`mqtt/command.py`)

`cmd` 구독 → 처리 → `ack` 발행:

| 커맨드 | 동작 |
|---|---|
| `calibrate` | `run_calibration()` 시작. 페이즈를 `ack`로 스트리밍 |
| `set_config` | `PresenceConfig` 필드 갱신 (재시작 없이) |
| `set_mode` | ESP에 `CMD_MODE_OCCUPANCY`/`CMD_MODE_FALL` 전달 |
| `ping` | 헬스 응답 |

**완료 기준**
- [ ] mTLS 연결 성립, 토픽 ACL로 다른 기기 토픽 접근 차단 확인
- [ ] 게이팅 on/off 시 실측 대역폭 비교표 산출
- [ ] 브로커 다운 시 재연결 백오프 동작, `presence` 로컬 판정은 무중단

---

### Phase 5 — 단절 대응 + 상시 운용

#### 5-1. SQLite 스풀 (`spool/sqlite_spool.py`)

- 네트워크 단절 시 `presence`/`telemetry` 이력을 로컬 저장, 복구 시 순서대로 업로드
- `window`(피처)는 **저장하지 않는 것을 기본**으로 한다 — 232KB×4Hz는 SD카드를 빠르게 채우고 마모시킨다. 낙상 판정은 실시간성이 본질이라 뒤늦게 올려도 가치가 낮다. 단, 단절 중 활동 구간이 있었다는 **사실**은 기록한다.
- 보존 상한(용량/기간) 초과 시 오래된 것부터 폐기, 폐기량을 `telemetry`로 보고

#### 5-2. 상시 운용 (실행계획 §8.2)

| 항목 | 대응 |
|---|---|
| 상시 구동 | systemd 서비스 등록, `Restart=always`, 비정상 종료 시 자동 재시작 |
| 하드웨어 워치독 | `/dev/watchdog` 등록 |
| 정전 복구 | 부팅 시 자동 기동 + SPI 재연결(현행 자동 재연결 로직 재사용) |
| SD카드 수명 | 로그를 **tmpfs**로, 필요 시 SSD 부팅. 스풀 쓰기 빈도 제한 |
| 발열 | 밀폐 설치 시 스로틀링 → 추론 지연 증가. 방열 설계 확인, 온도를 `telemetry`에 포함 |
| 원격 운영 | MQTT 커맨드 채널 (Phase 4-4) + OTA(G6) |
| 무선 간섭 | **2.4GHz 업링크 송신이 근접 5GHz CSI 수신에 주는 영향 실측** |

**완료 기준**
- [ ] 전원 차단 → 재인가 시 60초 이내 완전 복구
- [ ] 네트워크 3시간 단절 후 복구 시 스풀 전량 업로드, 재실감지 무중단
- [ ] 72시간 연속 운용에서 메모리·디스크 증가 없음

---

## 5. 핵심 구현 모듈 상세

### 5.1 `transport/spi_reader.py` — SPI 마스터

```python
class SpiReader:
    """SerialReader 덕타이핑 계약을 그대로 구현해 상위 파이프라인을 무수정 재사용한다."""

    # 하드웨어
    GPIO_DATA_READY = 25          # BCM, PUD_DOWN, add_event_detect(RISING)
    SPI_BUS, SPI_DEVICE = 0, 0    # spidev(0, 0)
    MAX_SPEED_HZ = 10_000_000
    MODE, LSB_FIRST = 0, False

    # 덕타이핑 계약 (SerialReader와 동일)
    running: bool                 # 마지막 프레임 < 3.0s 이내
    packet_count: int             # 파싱 성공 프레임 누적
    def send_line(self, text: str) -> bool: ...   # "train" → CMD_START_TRAIN 매핑
    def get_window(self, seconds) -> tuple | None: ...

    # SPI 고유
    def send_cmd(self, cmd: int, param: int = 0) -> None: ...
    def send_mode_occupancy(self) -> None: ...
    def send_mode_fall(self) -> None: ...
```

**RX 워커 스레드**: `DATA_READY` 이벤트를 1초 타임아웃으로 대기 → `_spi_transfer(8192)` → magic 검증 → 프레임 파싱 → 링버퍼 적재. 타임아웃 경로에서도 대기 중인 커맨드를 플러시한다. 3회 연속 실패 시 에러 콜백.

**전이중 커맨드**: 커맨드가 대기 중이면 TX 버퍼 `[0:8]`에 `SpiFrameHdr(magic=0xCDAB, pkt_type=PKT_CMD, payload_len=4)`, `[8:12]`에 `RpiCmdPayload`를 채우고 `spi.xfer2()` — **커맨드 송신과 데이터 수신이 같은 트랜잭션**이다.

> **시제품에서 반드시 고칠 것**: `infra/raspberry/web_server.py`의 `on_spi_frame`은 **SPI 워커 스레드**에서 `asyncio.create_task(...)`를 호출한다. 호출 스레드에 러닝 루프가 없으면 실패한다. 새 구현은 `loop.call_soon_threadsafe()` 또는 `asyncio.Queue`로 넘길 것.

### 5.2 `presence/` — 재실감지 (무변경 이식)

#### 움직임 감지 (MV)

0.25초 stride마다 최근 3초를 처음부터 다시 계산한다:

```
① resample_signal(timestamps_us, amp) → 100Hz 균일 그리드 (선형보간)
② bandpass_filter 0.5–50Hz, 4차 Butterworth, sosfiltfilt(영위상)
     ※ 위상 지연이 있으면 "지금" 판정이 어긋난다
③ select_top_subcarriers_2d: q = max(이동분산)/mean(이동분산), 상위 10개
④ sum_and_normalize: 10개 합산 후 z-score
⑤ _moving_variance(omega): υ(n;W) = (1/2W)·Σ_{k=n-W}^{n+W}(|h[k]| − μ_h(n))²
   → mv_current = mv[-1]
⑥ mv_current ≥ presence_mv_threshold → 활동
```

`omega`는 설정에서 직접 주지 않고 **파생 프로퍼티**다:

```python
omega        = max(1, round((mv_window_sec        * fs_hz - 1) / 2))  # (0.5×100−1)/2 = 24.5 → 24
wander_omega = max(1, round((wander_mv_window_sec * fs_hz - 1) / 2))  # (1.0×100−1)/2 = 49.5 → 50
```

> 파이썬 `round()`는 banker's rounding이라 24.5 → **24**, 49.5 → **50**이 된다. 이식 시 언어를 바꾸거나 `math.floor`/`np.round`로 갈아끼우면 값이 달라지므로 주의할 것.

| 파라미터 | 기본값 |
|---|---:|
| 관측 윈도우 | 3.0초 |
| 이동분산 창 (`mv_window_sec`) | 0.5초 → `omega = 24` (창 2ω+1 = 49 샘플 ≈ 0.49초) |
| 리샘플 주파수 | 100Hz |
| 밴드패스 | 0.5–50Hz, 4차 |
| 선택 서브캐리어 | 10개 |
| stride | 0.25초 |
| `presence_mv_threshold` | 2.0 (캘리브레이션 산출) |

#### Wander 감지 (미세움직임)

MV는 **큰 몸통 동작**에 선택적으로 민감해서, 앉거나 누운 사람이 손발만 움직이는 상황을 놓친다. 호흡(성인 안정 시 12–20 bpm = 0.2–0.33Hz)과 사지 미세동작을 잡는 보조 신호다.

```
① 사전필터 0.05–5Hz (광대역) → z-score 정규화
② 측정 대역 0.1–0.5Hz (협대역)만 Welch PSD 적분
③ wander_ratio = wander_current / wander_baseline
④ ratio ≥ wander_ratio_threshold 를 wander_min_duration_s 연속 유지 → wander_confirmed
```

> **두 대역을 같게 만들면 안 된다.** 사전필터가 이미 모든 에너지를 측정 대역 안으로 가둔 뒤 정규화하면, 이어지는 PSD 측정이 입력과 거의 무관해져 변별력을 잃는다. 실측으로 확인된 사항이다.

Welch는 **단일 세그먼트**(다중 세그먼트 평균 없음)를 쓴다 — 분산 감소를 포기하고 0.25초 응답성을 택한 것.

| 파라미터 | 기본값 |
|---|---:|
| 관측 윈도우 | 10.0초 |
| 사전필터 대역 | 0.05–5Hz |
| 측정(적분) 대역 | 0.1–0.5Hz |
| `wander_ratio_threshold` | 1.8배 |
| 디바운스 | 2.0초 |
| `wander_baseline` | 0.5 (캘리브레이션 산출) |
| 이동분산 창 (`wander_mv_window_sec`) | 1.0초 → `wander_omega = 50` |

#### 상태머신 — 비대칭 융합

두 상태(PRESENT/ABSENT)뿐이고, 부팅 기본은 **ABSENT**.

| 신호 | ABSENT → PRESENT | PRESENT 유지 |
|---|:---:|:---:|
| MV ≥ 임계값 | ✅ (입실에는 반드시 몸통 동작이 따른다) | ✅ |
| wander 확인 | ❌ **불가** (입실 사건을 구분할 수 없다) | ✅ (이미 PRESENT일 때만) |

이 비대칭은 문이 열리거나 바람이 스치는 순간적 노이즈가 재실로 오탐되는 것을 막는다.

**틱당 6단계**:
```
1. MV 3-tap 가중평활: 0.4·x[t] + 0.3·x[t-1] + 0.3·x[t-2]   (2샘플: 0.6/0.4, 1샘플: 그대로)
2. wander_ratio = wander_current / wander_baseline           (baseline ≤ 1e-8이면 0.0)
3. 디바운스 2초 → wander_confirmed
4. last_activity_at 갱신 (비대칭 규칙):
     smoothed_mv ≥ mv_threshold                        → 갱신 (상태 무관)
     else if 상태 == PRESENT and wander_confirmed      → 갱신
     else                                              → 갱신하지 않음
5. (now − last_activity_at) < presence_timeout_s(10s) → PRESENT, 아니면 ABSENT
6. 직전 틱과 다르면 just_changed 플래그
```

### 5.3 이름이 같지만 다른 신호 — 혼동 방지표

이 프로젝트에는 **`wander`가 세 개, 임계값이 세 축** 있다. 문서·UI·코드에서 절대 섞지 말 것.

| 이름 | 정의 | 척도 | 어디 |
|---|---|---|---|
| `wander` (Pi, 정식) | 0.1–0.5Hz Welch band energy의 baseline 대비 **비율** | 배수 (1~5) | `presence/streaming_features.py` |
| `wander` (ESP FSM) | 진폭 평균의 `base_amp_mean` 대비 **절대 편차** | 절대값 | `occupancy_fsm.c` — **폴백 전용, 무관** |
| `wander_threshold` (mock) | 프론트 목업의 절대값 임계값 | 절대값 (0~1) | `src/lib/mock-store.ts` |
| `presence_mv_threshold` | 재실 움직임 임계값 (캘리브레이션 산출) | MV 스케일 | UI: **"움직임 임계값"** |
| `wander_baseline` | 재실 baseline (캘리브레이션 산출) | PSD 스케일 | UI: **"재실 baseline"** |
| `threshold` = 0.468 | **낙상 DL 확률** 임계값 | 확률 (0~1) | UI: **"판정 임계값"** / **"낙상 확률 임계값"** |

### 5.4 `gating.py` (신규)

```python
class UploadGate:
    """재실/활동 상태로 window 토픽 발행 여부를 결정한다."""
    def should_upload_window(self, status: PresenceStatus, now: float) -> bool: ...
    def stats(self) -> dict:   # 게이팅률, 차단 윈도우 수 — telemetry로 보고
        ...
```

**정책 원칙**: 사람이 없는 구간의 피처는 올리지 않는다. 단 게이트가 **닫혀 있었다는 사실 자체**는 `telemetry`로 항상 보고해야 한다 — 무증상 침묵과 "정상적으로 조용함"을 구별할 수 있어야 한다.

### 5.5 `mqtt/codec.py` (신규)

```python
def encode_window(s3: np.ndarray, acf: np.ndarray, meta: dict) -> bytes:
    """(224,224) + (1,128,64) float32 → 양자화 → MessagePack → zstd"""

def quantize_int8(x: np.ndarray) -> tuple[np.ndarray, float, float]:
    """스케일·오프셋을 함께 반환. 클라우드에서 역양자화 후 모델 정규화 적용"""
```

> **주의**: 모델의 `normalization{feature_a, feature_b}` 통계는 **체크포인트 안에** 있고 클라우드에서 적용된다. 엣지 양자화는 그 **이전 단계**이므로, 양자화 오차가 정규화 후 모델 입력에 미치는 영향을 반드시 검증해야 한다(양자화 전후 `proba_fall` 비교).

---

## 6. 현재 구현 코드에서 참고·이식할 코드

### 6.1 이식 매핑

| 현재 위치 | 신규 위치 | 변경 |
|---|---|---|
| `csi_fall/infra/raspberry/spi_interface.py` | `transport/spi_reader.py` | 8192B, 덕타이핑 계약 구현, asyncio 스레드 안전성 수정 |
| `csi_fall/infra/raspberry/spi_interface_windows.py` | `transport/spi_mock.py` | 626B 배치 생성으로 갱신 |
| `csi_fall/infra/raspberry/protocol.py` | `transport/protocol.py` | ESP `common/protocol.h`와 단일 계약으로 통합 |
| `csi_fall/infra/raspberry/test_protocol.py` | `tests/test_protocol.py` | 626B 레이아웃 |
| [backend/csi/buffer.py](../backend/csi/buffer.py) | `csi/buffer.py` | 무변경 |
| [backend/presence/config.py](../backend/presence/config.py) | `presence/config.py` | 락 또는 스냅샷 교체 추가 |
| [backend/presence/preprocessing.py](../backend/presence/preprocessing.py) | `presence/preprocessing.py` | `resample_signal` 벡터화 검토 |
| [backend/presence/streaming_features.py](../backend/presence/streaming_features.py) | `presence/streaming_features.py` | 무변경 |
| [backend/presence/state_machine.py](../backend/presence/state_machine.py) | `presence/state_machine.py` | 기본값을 `PresenceConfig`와 일치시킴 |
| [backend/presence_loop.py](../backend/presence_loop.py) | `presence_loop.py` | 무변경 |
| [backend/onboarding.py](../backend/onboarding.py) | `calibration/onboarding.py` | `silence_confirm_s` SPI 기준 재산정 |
| [backend/features/realtime.py](../backend/features/realtime.py) | `features/realtime.py` | 무변경 (계약 고정) |
| [backend/features/common.py](../backend/features/common.py) | `features/common.py` | 디노이즈 벡터화/numba, `fallback_cwt` 유지 |
| [backend/features/acf.py](../backend/features/acf.py) | `features/acf.py` | 무변경 |
| [backend/bench_pipeline.py](../backend/bench_pipeline.py) | `tools/bench_pipeline.py` | Pi 지연·메모리·발열 측정 확장 |
| `csi_fall/infra/raspberry/state_manager.py` | 개념만 참조 | IDLE/TRAINING/OCCUPANCY/FALL_DETECTING 모드 관리 |
| `csi_fall/infra/raspberry/SETUP_BY_PLATFORM.md` | `deploy/install.sh`의 근거 | `usermod -a -G gpio`, `raspi-config` SPI 활성화, 핀맵 |

### 6.2 승계하지 않는 것

| 위치 | 이유 |
|---|---|
| `csi_fall/infra/raspberry/fall_detector.py` | LPF + mean/std/peak_diff + burst∧stillness 규칙. **S3+PCA-ACF DL 모델과 무관한 별개 계보.** 낙상 판정은 클라우드가 한다 |
| `csi_fall/infra/raspberry/web_server.py` | 프로토타입 인증(평문 비밀번호 `USERS`, 프로세스마다 재생성되는 `SECRET_KEY`, 어디에도 걸리지 않은 `verify_token`), deprecated `@app.on_event`, 스레드-asyncio 혼용 버그. 로컬 UI는 프론트엔드 레포가 대체 |
| `csi_fall/infra/raspberry/static/` | 위와 동일 |
| [backend/detector.py](../backend/detector.py) | 낙상 DL 루프 → 클라우드 |
| [backend/inference/](../backend/inference/) | 모델 서빙 → 클라우드 |
| [backend/notifier.py](../backend/notifier.py) | 알림 → 클라우드 |

### 6.3 진단 목적으로만 참조

- `csi_fall/infra/pipline.md` — SPI 시제품의 시나리오별 데이터 흐름(훈련 30초 / occupancy 50Hz / fall 320Hz)
- `csi_fall/csi_fall_monorepo/DATA_PIPELINE.md` — 같은 흐름의 상세 ASCII 다이어그램

---

## 7. 인접 레포와의 인터페이스 계약

### 7.1 ESP32-C5 ← SPI

[esp 명세서 §7.1](WIFI-GUARD_레포명세_esp_v1.0_20260804.md) 참조. Pi 측 요약:

| 항목 | 값 |
|---|---|
| 디바이스 | `spidev(0, 0)`, 10MHz, mode 0, MSB first |
| 핀 (BCM) | MOSI 10 / MISO 9 / SCLK 11 / CE0 8 / **DATA_READY 25** (RISING) |
| 트랜잭션 | 8192B 전이중, 50ms 주기 |
| 수신 | `PKT_CSI_BATCH`(전 모드) · `PKT_STATUS_JSON`(진단) · `PKT_TRAIN_*` · keepalive |
| 송신 | `PKT_CMD` + `CMD_START_TRAIN`/`MODE_OCCUPANCY`/`MODE_FALL`/`PING` |

### 7.2 클라우드 → MQTT (SSOT는 [backend 명세서 §7](WIFI-GUARD_레포명세_backend_v1.0_20260804.md))

```
[Pi] ──MQTT over TLS 8883, mTLS, 아웃바운드 전용──► [AWS IoT Core] ──IoT Rule──► [Kafka]
```

| 토픽 | 방향 | 주기 | 페이로드 |
|---|---|---|---|
| `wifiguard/{facility}/{device}/presence` | Pi→ | 4Hz | `PresenceStatus` 11개 스칼라 |
| `wifiguard/{facility}/{device}/telemetry` | Pi→ | 저빈도 | RSSI, noise_floor, AGC, hz, 링버퍼, 재접속 카운터, 게이팅률, 온도, 스풀 적체 |
| `wifiguard/{facility}/{device}/window` | Pi→ | **게이팅 시에만** 4Hz | S3 + ACF (양자화 + zstd + MessagePack) |
| `wifiguard/{facility}/{device}/cmd` | →Pi | 이벤트 | `calibrate` / `set_config` / `set_mode` / `ping` |
| `wifiguard/{facility}/{device}/ack` | Pi→ | 이벤트 | 커맨드 결과, 캘리브레이션 페이즈 |

**`presence` 페이로드** (`PresenceStatus`):
```
state (present|absent), mv_current (평활 후), wander_current,
mv_threshold, wander_baseline, wander_ratio_threshold, wander_ratio,
wander_confirmed, last_activity_at, seconds_since_activity, just_changed
```

**`window` 페이로드**: `s3 (224,224)` + `acf (1,128,64)` + 메타(`fs_hz`, `window_samples`, `t_start`, `t_end`, 양자화 스케일/오프셋, `feature_ms`).

---

## 8. 리스크

| # | 리스크 | 영향 | 완화 |
|---|---|---|---|
| **R1** | **Pi CPU에서 0.25초 예산 초과** | 낙상 파이프라인 성립 불가 | Phase 3-1 벤치 선행. 디노이즈 벡터화/numba. 안 되면 stride 완화 또는 원시 CSI 업로드로 회귀 |
| **R2** | `ssqueezepy` ARM64 휠 부재 | CWT 불가 | `fallback_cwt` 유지 + 동등성 검증. 최악의 경우 소스 빌드 |
| R3 | `freq_to_scale` 캐시 미스 | 호출당 0.5초 → 즉시 예산 초과 | fs 0.25Hz 양자화를 반드시 이식. 캐시 적중률을 `telemetry`로 노출 |
| R4 | 피처 업로드 대역폭 (7.4Mbps 무압축) | 가정 업링크 포화 | 게이팅 + 양자화 + zstd. 실측 후 필요 시 원시 CSI 전환 재검토 |
| R5 | 2.4GHz 업링크가 근접 5GHz CSI 수신을 간섭 | 감지 품질 저하 | Phase 5 실측 항목. 안테나 이격 또는 유선 업링크 |
| R6 | SD카드 마모 (로그·스풀 쓰기) | 현장 고장 | tmpfs 로그, 스풀 쓰기 제한, SSD 부팅 옵션 |
| R7 | 발열 스로틀링 | 추론 지연 증가 | 방열 설계, 온도 telemetry, 스로틀 발생 시 경보 |
| R8 | `PresenceConfig` 무락 동시 접근 | 드문 불일치 | 이관 시 락 또는 불변 스냅샷 교체 |
| R9 | 양자화 오차가 `proba_fall`을 흔듦 | 오탐/미탐 | Phase 4에서 양자화 전후 확률 비교 검증 필수 |

---

## 9. 미결정 사항

1. **[최우선] 낙상 모드 CSI 레이트: 320Hz vs 166.67Hz.**
   현 체크포인트는 약 166.67Hz 수집 데이터로 학습되었고, S3 스칼로그램 주파수축이 `linspace(1.0, min(170, fs/2))`라 fs가 바뀌면 담기는 주파수 내용이 달라진다(166.67Hz → 1~83.3Hz, 320Hz → 1~160Hz). → **166.67Hz 권고.** SPI 대역폭도 8192B/50ms 한 번으로 해결된다. [esp 명세서 §9-1](WIFI-GUARD_레포명세_esp_v1.0_20260804.md)과 동일 항목.

2. **occupancy 모드 CSI 레이트 50Hz의 적정성.**
   재실 신호체인은 100Hz 그리드로 리샘플하고 0.5–50Hz 밴드패스를 건다. 원본이 50Hz면 `safe_bandpass`가 상한을 fs/2−1 = 24Hz로 클램프한다. 재실 판정에 실질 영향이 있는지 실측 필요.

3. **`silence_confirm_s = 0.2` 재산정.**
   UART 921600 baud의 드레인 시간(~7ms)에서 나온 값이다. SPI는 50ms 주기 트랜잭션이라 침묵 감지 특성이 다르다. `packet_count` 정지 판정에 최소 2~3 트랜잭션(100~150ms)이 필요할 수 있다.

4. **Pi 폴백 추론 (양자화 ONNX).**
   Phase 3-1 벤치 결과에 종속. 피처 추출만으로 예산의 몇 %를 쓰는지 나와야 추론까지 얹을 수 있는지 판단 가능하다. 이번 명세는 범위 밖으로 두되, `features/` → ONNX Runtime 인터페이스는 예약해 둔다.

5. **네트워크 단절 시 낙상 감지 중단의 사용자 고지 방식.**
   재실감지는 유지되지만 낙상 감지는 멈춘다. **안전 기능이므로 무증상 중단은 허용되지 않는다.** `telemetry`로 보고 → 백엔드 → 프론트 배너/푸시 경로를 확정해야 한다.

6. **`window` 페이로드를 피처로 할 것인가 원시 CSI로 할 것인가 (재검토 트리거).**
   현재 결정은 **피처**다. 다만 `FACILITY 구상도` §5의 대역폭 계산은 원시가 유리하다고 보며, `FACILITY` §9.4는 이를 명시적 미결정으로 남겼다. R1(Pi 연산 예산)이나 R4(대역폭)가 실측에서 무너지면 이 결정을 되돌려야 한다 — 그때 Pi는 PCA 후 1-D 모션 신호(3초 × fs ≈ 500 float)만 올리고 CWT는 클라우드가 맡는 절충안도 가능하다.

7. **FACILITY 다채널 게이트웨이.**
   `FACILITY 구상도` §3.2는 "라즈베리파이 1대 = 수신기 N대"를 전제한다(Pi 4에서 2~4채널 추정, 실측 필요). 이번 명세는 **1:1 HOME 구성**을 기준으로 쓰였다. 채널별 독립 링버퍼 + presence 인스턴스, 한 채널의 장애가 다른 채널을 죽이지 않는 격리가 추가로 필요하다.

---

## 참고 문서

- `CSI-Guard_재실감지_알고리즘_상태머신_보고서_v2.0_20260720.md` — §1 MV, §2 Wander, §3 상태머신, §4 캘리브레이션 (이 문서 §5.2의 원본)
- `CSI-Guard_완성목표_실행계획_v1.0_20260729.md` §3 (G2 MQTT), §8 (G7 엣지 이관·Pi 운용 항목)
- `CSI-Guard_구현현황_및_완성목표_v1.0_20260729.md` §2 (타깃 아키텍처), §4.4 (G7)
- `FACILITY 구상도.md` §3.2 (존 게이트웨이), §5 (대역폭 산정), §9.4 (페이로드 미결정)
- [backend/README.md](../backend/README.md) — 현행 파이프라인 실행법·계약
- [esp 명세서](WIFI-GUARD_레포명세_esp_v1.0_20260804.md) §4 Phase 0 · §7 (SPI 계약)
- [backend 명세서](WIFI-GUARD_레포명세_backend_v1.0_20260804.md) §7 (MQTT 토픽 SSOT)
