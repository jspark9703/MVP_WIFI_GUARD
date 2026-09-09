# 실시간 낙상 감지 시스템 기능 명세서

> **최종 수정**: 2026-07-03  
> **대상 버전**: MVP v1 (commit 9bba36c 이후)

## 1. 개요

### 1.1 시스템 목표

**ESP32-C5 기반 CSI(Channel State Information) 실시간 낙상 감지 시스템**

- **목적**: 거주자의 낙상(Fall) 이벤트를 WiFi 신호(5GHz CSI)로 감지하고, 즉시 화면/알람으로 알림
- **사용 장비**: ESP32-C5 개발보드 2대 (Transmitter + Receiver)
- **입력**: WiFi CSI 신호 (신호 진폭의 시간 변화)
- **출력**: 낙상 여부/신뢰도/타임스탬프, 브라우저 대시보드에 실시간 표시

### 1.2 핵심 기능

| 기능 | 설명 |
|------|------|
| **실시간 CSI 수집** [구현됨] | ESP32 Receiver에서 시리얼(921600 bps)로 CSI 바이너리 프레임 수신 |
| **신호 처리 파이프라인** [구현됨] | 리샘플링 → 대역통과 필터(2-50Hz) → 부반송파 선택 → 이동분산(moving variance) 계산 |
| **낙상 감지 상태 머신** [구현됨] | IDLE→SUSPECT→FALL→COOLDOWN 상태 전이 로직, 임계값 기반 트리거 |
| **이동분산 라인 차트** [구현됨] | 마지막 240틱의 이동분산값 실시간 시각화 |
| **낙상 알람** [구현됨] | 낙상 감지 시 5초 자동 dismiss 팝업 알람 표시 |
| **실시간 대시보드** [구현됨] | WebSocket 10Hz 푸시 방식, 4개 페이지(모니터링/탐지설정/낙상기록/Train) |
| **동적 파라미터 튜닝** [구현됨] | 재시작 없이 13개 설정값 실시간 변경 가능 (`GET/POST /detection/config`) |
| **CSI 진폭 히트맵** [문서상 계획, 미구현] | README/API.md에 기술되었으나 실제 프론트엔드에 미구현 |
| **신뢰도 바 위젯** [문서상 계획, 미구현] | README에 기술되었으나 UI 미구현 (스탯 카드에만 신뢰도% 표시) |
| **신호 품질 위젯** [문서상 계획, 미구현] | README에 기술된 PPS/gap count 시각화 미구현 |
| **낙상 기록 영속화** [미구현] | 현재는 클라이언트 메모리만 사용, 서버 저장 없음 |

---

## 2. 시스템 아키텍처

### 2.1 하드웨어 구성도

```
┌─────────────────────────────────────────────────────────────────┐
│                        WiFi 5GHz Channel 48                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  [Transmitter]              [Receiver]                           │
│  ESP32-C5                   ESP32-C5 (USB)                      │
│  - Battery/USB powered       - Connected to PC                  │
│  - csi_send/ firmware        - csi_recv/ firmware               │
│  - ESP-NOW broadcast 100Hz   - USB Serial 921600 bps            │
│  - MAC: 1a:00:00:00:00:00    - Filters CSI by TX MAC           │
│    (spoofed)                 - Binary frame parser              │
│                              - Ring buffer (1500 frames, ~15s)  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                                 │
                    Serial (USB) over 921600 bps
                                 │
                    ┌────────────▼───────────┐
                    │   Python FastAPI       │
                    │   Backend (main.py)    │
                    │ - CSI 파싱 & 버퍼링    │
                    │ - 신호처리 파이프라인  │
                    │ - 낙상 상태 머신      │
                    │ - REST/WS API         │
                    └────────────┬───────────┘
                                 │
                    ┌────────────▼──────────────┐
                    │  WebSocket (10Hz)         │
                    │  & REST HTTP              │
                    └────────────┬──────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  Browser Dashboard       │
                    │ - static/index.html      │
                    │ - 이동분산 차트          │
                    │ - 낙상 상태 배지        │
                    │ - 알람 모달             │
                    │ - 설정 폼               │
                    └──────────────────────────┘
```

### 2.2 데이터 흐름

1. **Transmitter** (ESP32-C5, `csi_send/`)
   - ESP-NOW로 100Hz 브로드캐스트 (spoofed MAC `1a:00:00:00:00:00`)
   - 신호 주파수/power 일정: 프로토콜 프레임 크기, 반송파 등 고정

2. **Receiver** (ESP32-C5, `csi_recv/`)
   - TX의 MAC으로 필터링된 CSI 수신 (IQ 샘플)
   - 로컬 타임스탬프(μs, 32비트 wrap) 첨가
   - 바이너리 프레임 포장 (magic 0xA55A, 체크섬)
   - USB 시리얼 송신 (921600 bps, 8N1)

3. **FastAPI Backend** (main.py, `src/pipeline.py`)
   - 시리얼 스레드에서 바이너리 프레임 수신 & 파싱
   - CSI 진폭 계산 (IQ → √(I² + Q²))
   - 타임스탬프 unwrap (32비트 overflow 처리)
   - Ring buffer에 저장 (1500 프레임, ~15초)
   - 0.5초마다 신호처리 파이프라인 실행
   - 결과를 WebSocket으로 10Hz 푸시, REST API로 조회

4. **Browser Dashboard** (static/index.html)
   - WebSocket으로 매 100ms마다 DetectionInfo 수신
   - 이동분산 차트 업데이트 (5-샘플 평활)
   - 상태/신뢰도/임계값 스탯 카드 업데이트
   - 낙상 감지 시 알람 모달 표시

---

## 3. 하드웨어 요구사항 및 초기 설정

### 3.1 필수 하드웨어

- **ESP32-C5 개발보드** (2개)
  - Transmitter: 배터리 또는 USB 전원
  - Receiver: PC에 USB 케이블 연결
  
### 3.2 펌웨어 설정

| 항목 | 값 | 위치 |
|------|-----|------|
| **WiFi 대역** | 5GHz | `csi_recv/main/app_main.c` |
| **채널** | 48 | `CONFIG_LESS_INTERFERENCE_CHANNEL=48` |
| **TX MAC (스푸프)** | `1a:00:00:00:00:00` | `csi_send/` |
| **RX 필터 MAC** | `1a:00:00:00:00:00` | `csi_recv/main/app_main.c:73` |
| **브로드캐스트 주기** | 100Hz (10ms) | `csi_send/` |
| **시리얼 속도** | 921600 bps, 8 data, no parity, 1 stop | `csi_recv/main/app_main.c`, `main.py:342-345` |

### 3.3 펌웨어 플래시

1. Transmitter 펌웨어: `csi_send/` → ESP32-C5 보드에 플래시
2. Receiver 펌웨어: `csi_recv/` → 다른 ESP32-C5 보드에 플래시
3. 두 보드 모두 5GHz WiFi 활성화 확인 (`iw list` 등)

### 3.4 백엔드 시작

```bash
cd tools/fall_detect
pip install -r requirements.txt
python main.py --host 0.0.0.0 --port 8000
```

### 3.5 브라우저 접근

```
http://localhost:8000
```

---

## 4. CSI 수집 및 바이너리 프레임 파싱

### 4.1 프레임 구조 [구현됨]

**Source**: `main.py:29-50`, `main.py:175-440`

ESP32 Receiver에서 전송되는 바이너리 프레임:

```
[ MAGIC ] [ HEADER ] [ CSI DATA ] [ CHECKSUM ]
  0xA55A   ~44 bytes   612 bytes    2 bytes
```

#### Magic & Version
- **Magic**: `0xA55A` (리틀엔디언, `<H` 포맷)
- **Version**: `BINARY_FRAME_VERSION=1` (필수, 다른 값 시 거부)
- **Frame Type**: `BINARY_FRAME_TYPE_CSI=1` (CSI 데이터 구분용)

#### 헤더 포맷

```c
struct {
    uint16 seq;                      // 프레임 시퀀스 번호
    uint16 mac[3];                   // 송신자 MAC 주소
    int8   rssi;                     // RSSI (수신 신호 강도)
    uint8  rate;                     // 데이터율
    uint8  sig_mode;                 // 신호 모드
    uint8  mcs;                      // Modulation & Coding Scheme
    uint8  bandwidth;                // 대역폭
    uint8  smoothing;                // 평활화 플래그
    uint8  not_sounding;             // Not sounding 플래그
    uint8  aggregation;              // A-MPDU aggregation 플래그
    uint8  stbc;                     // Space-Time Block Coding
    uint8  fec_coding;               // FEC 인코딩
    uint8  sgi;                      // Short Guard Interval
    int8   noise_floor;              // 노이즈 플로어
    uint8  ampdu_cnt;                // A-MPDU 개수
    uint16 channel;                  // 채널 번호
    uint16 secondary_channel;        // 2차 채널
    uint8  local_timestamp[4];       // ESP32 로컬 타임스탬프 (μs, 32비트)
    uint8  ant;                      // 안테나 인덱스
    uint16 sig_len;                  // 신호 길이
    uint8  rx_state;                 // RX 상태
    uint16 len;                      // 다음 데이터 블록 길이
    uint8  first_word;               // 첫 단어 플래그
    uint8  data[612];                // CSI 원본 바이트 (IQ 샘플 쌍)
} BinaryFrame;
```

**포맷 문자열**: `"<HHBBI6sbBBBBBBBBBBbBBBBHHIbBHBB"` (struct.calcsize = ~50 바이트)

#### CSI 데이터

- **크기**: 612 바이트 (고정)
- **인코딩**: signed int8 IQ 샘플 쌍 (interleaved)
  - 바이트 0, 1: 첫 부반송파의 Imaginary, Real
  - 바이트 2, 3: 다음 부반송파의 Imaginary, Real
  - ... (총 306개 부반송파)
- **진폭 계산**: `amp[i] = sqrt(imag[i]² + real[i]²)` (main.py:409-411)

#### 체크섬

- **유형**: 모든 선행 바이트의 합 (&0xFFFF)
- **위치**: 프레임 끝 2바이트 (리틀엔디언 uint16)
- **검증**: `main.py:_parse_csi_frame()` 수행 (불일치 시 프레임 거부)

### 4.2 프레임 파싱 로직 [구현됨]

**Source**: `main.py:149-230`

#### 프레임 추출 (`_extract_binary_frames()`)

```python
def _extract_binary_frames(self, chunk: bytes) -> list[dict]:
    """
    Raw 바이트 스트림에서 완전한 바이너리 프레임 추출.
    - magic 0xA55A 검색
    - 프레임 길이 유효성 확인
    - 완전한 프레임만 반환 (부분 프레임 제외)
    - magic 전 쓰레기 데이터는 삭제
    """
    # 내부 bytearray 버퍼에 chunk 누적
    # 0xA55A magic 스캔
    # frame_len 범위 검증 (너무 크거나 작으면 버림)
    # 완전한 프레임만 queue에 push
```

#### 프레임 파싱 (`_parse_csi_frame()`)

```python
def _parse_csi_frame(self, frame_bytes: bytes) -> dict:
    """
    완전한 바이너리 프레임을 파싱.
    - Magic & Version 확인
    - 헤더 unpacking
    - 체크섬 검증
    - CSI 진폭 추출
    - 결과 딕셔너리 반환
    """
    # 1. Magic & Version 확인
    #    - frame_bytes[0:2] == 0xA55A
    #    - version field == 1
    # 2. 헤더 unpacking (struct.unpack)
    # 3. 체크섬 검증 (모든 바이트 합 & 0xFFFF)
    # 4. CSI 원본 바이트에서 (imaginary, real) 쌍 추출
    # 5. 진폭 = sqrt(imag² + real²) per subcarrier
    # 6. 반환 딕셔너리:
    #    {
    #      'timestamp_us': <로컬 타임스탬프>,
    #      'mac': '<HH:HH:HH>',
    #      'rssi': <dBm>,
    #      'rate': <Mbps>,
    #      ...,
    #      'csi_amplitude': [amp0, amp1, ..., amp305]  # 306개 부반송파
    #    }
```

### 4.3 타임스탬프 Unwrap (시계 오버플로우 처리) [구현됨]

**Source**: `main.py:416-421`

ESP32의 로컬 타임스탬프는 32비트 uint32로 약 4296초(~71.6분)마다 0으로 리셋된다.

```python
# 원본 타임스탐프: 32비트 unsigned int (최대 4,294,967,295 μs = 4296.9초)

# Unwrap 알고리즘:
# - 새 raw_ts가 이전 raw_ts보다 2^31 이상 작으면 overflow 발생
# - _ts_offset += 2^32 (다음 프레임부터 더함)
# - unwrapped_ts = raw_ts + _ts_offset

# 예:
# raw_ts[n-1] = 4_294_000_000 μs (nearly overflow)
# raw_ts[n]   = 500_000 μs        (wrapped after 1s elapsed)
# → 증가분 = 500_000 - 4_294_000_000 < -2^31
# → _ts_offset += 2^32
# → unwrapped_ts[n] = 500_000 + (2^32 - 0) = 4_294_967_796 μs ✓
```

### 4.4 Ring Buffer 관리 [구현�음]

**Source**: `main.py:340-430`, `EspMonitor._csi_buffer`

```python
# Ring buffer: deque(maxlen=1500)
# - 최대 1500 CSI 프레임 저장
# - 100pps × 15s = 1500 프레임
# - 메모리: ~1500 × 306 floats = ~1.8MB

# 스레드 안전: 별도 reader 스레드에서 큐를 통해 push
# - reader 스레드: 시리얼 포트에서 0.2초 타임아웃으로 청크 수신
# - main 루프: 큐에서 청크 꺼내 프레임 파싱, 버퍼에 저장
```

### 4.5 진단 및 로깅 [구현됨]

**Source**: `main.py:430-440`, `EspMonitor._run_loop()`

```python
# 카운터 유지:
# - bytes_received: 시리얼에서 수신한 총 바이트
# - frames_parsed: 성공적으로 파싱된 프레임
# - frames_rejected: 체크섬/헤더 오류로 거부된 프레임
# - packet_count: 현재까지 누적 패킷 수

# 주기: ~10초마다 진단 로그 출력
# 로그 내용:
#   "timestamp | bytes_received | frames_parsed | frames_rejected | packet_count | diagnostic"
#   예: "2026-07-03 08:30:45 | 54321 | 100 | 0 | 100 | OK"
#       "2026-07-03 08:30:55 |  5432 |   5 | 95 |   5 | ERROR: checksum_mismatch"

# packet_count = 0 진단 트리 (README 참고):
# 1. Transmitter 전원 확인? (가장 흔한 원인)
# 2. /monitor/status 에러 필드 확인? (시리얼 포트 열기 실패)
# 3. "serial port <PORT> opened successfully (baud=921600)" 로그 확인?
# 4. 두 보드 동일 WiFi 채널 확인?
# 5. 보드율 921600 8N1 맞는지 확인?
```

---

## 5. 신호처리 파이프라인

### 5.1 개요 [구현됨]

**Source**: `src/streaming_features.py`, `src/pipeline.py:78-146`

각 `stride_sec` (기본 0.5초)마다 실행되는 파이프라인:

```
Input: 3초 trailing CSI window (진폭 306차원 시계열)
  ↓
[1] Resample (불규칙 시간간격 → 규칙적 100Hz 그리드)
  ↓
[2] Bandpass Filter (2-50Hz, Butterworth, zero-phase)
  ↓
[3] Subcarrier Selection (상위 10개 by q-value)
  ↓
[4] Sum & Normalize (선택된 부반송파 합산, z-score 정규화)
  ↓
[5] Moving Variance (0.5초 윈도우)
  ↓
Output: mv_current, confidence, selected_indices, signal_quality_metrics
```

**Configuration** (src/pipeline.py:20-28):

| 파라미터 | 기본값 | 문서상 기본값 | 설명 |
|---------|--------|-------------|------|
| `window_sec` | 3.0 | 3.0 | CSI 슬라이딩 윈도우 길이 (초) |
| `stride_sec` | 0.5 | 0.5 | 파이프라인 업데이트 간격 (초) |
| `fs_hz` | 100.0 | 100.0 | 리샘플링 목표 주파수 (Hz) |
| `mv_window_sec` | 0.5 | 0.5 | 이동분산 계산 윈도우 (초) |
| `n_streams` | 10 | 10 | 선택할 상위 부반송파 개수 |
| `bandpass_low` | 0.5 | 0.5 | 대역통과 필터 하한 (Hz) |
| `bandpass_high` | 50.0 | 50.0 | 대역통과 필터 상한 (Hz) |
| `bandpass_order` | 4 | 4 | Butterworth 필터 차수 |
| `mv_threshold` | 2.0 | 2.0 | 이동분산 임계값 (2026-07-14부터 잠정적으로 민감하게 설정, 이전 2.5) |
| `min_duration_s` | 0.5 | 0.5 | 낙상 확정에 필요한 최소 지속시간 |
| `merge_gap_s` | 0.25 | 0.25 | 인접 감지 병합 최대 갭 |
| `max_duration_s` | 2.0 | 2.0 | 낙상 이벤트 최대 지속시간 |
| `cooldown_s` | 3.0 | 3.0 | 낙상 감지 후 lockout 시간 |

### 5.2 Step 1: Resample (시간 정렬)

**Source**: `src/streaming_features.py:50-100`, `src/preprocessing.py:113-180`

목표: 불규칙한 수신 시간을 정규 100Hz 그리드에 정렬

```python
def resample_signal(
    timestamps_us: np.ndarray,      # 원본 비정규 타임스탬프 (μs)
    signal: np.ndarray,             # 309차원 CSI 진폭
    fs_hz: float = 100.0,           # 목표 주파수
    tolerance_ms: float = 2.0       # 보간 허용 갭 (ms)
) -> tuple[np.ndarray, SignalQuality]:
    """
    Linear interpolation을 사용한 리샘플링.
    - 정규 100Hz 시간 그리드 생성: t_new = [0, 10, 20, 30, ...] ms
    - scipy.interp1d로 각 부반송파 선형 보간
    - 큰 갭(> tolerance_ms): 'irregular_gap' 카운트, fallback (원본값 복사)
    - 음수/0 갭: 'nonpositive_gap' 카운트 (시계 역행 감지)
    
    반환:
      - resampled_signal: (n_time, n_subcarriers) ndarray
      - quality: SignalQuality 객체
        - interp_steps: 보간된 샘플 수
        - fallback_steps: fallback (큰 갭) 샘플 수
        - irregular_gaps: 큰 갭 개수
        - nonpositive_gaps: 음수/0 갭 개수
        - actual_pps: 실제 샘플링 레이트 (Hz)
        - window_duration_s: 윈도우 실제 지속시간 (초)
    """
```

**세부 로직**:

1. 원본 timestamps_us를 초 단위로 변환
2. 첫 타임스탐프를 0으로 정규화: `t_normalized = t - t[0]`
3. 정규 그리드 생성: `t_regular = np.arange(0, duration_s, 1/fs_hz)`
4. 시간 격차 검사: `gaps = np.diff(t_normalized)`
   - `gap > tolerance_ms / 1000` → irregular_gap (보간 스킵)
   - `gap <= 0` → nonpositive_gap (에러, 원본값 사용)
5. 선형 보간: `signal_interp = interp1d(t_normalized, signal)(t_regular)`
6. 결과: (len(t_regular), 306) 배열

### 5.3 Step 2: Bandpass Filter (2-50Hz 통대역 추출)

**Source**: `src/streaming_features.py:102-135`

목표: 낙상 운동의 특성 주파수 대역(2-50Hz)만 추출, 고주파 노이즈 제거

```python
def safe_bandpass(
    signal: np.ndarray,          # (n_time, n_subcarriers)
    fs_hz: float = 100.0,        # 샘플링 주파수
    low_hz: float = 2.0,         # 하한
    high_hz: float = 50.0,       # 상한
    order: int = 4               # 필터 차수
) -> tuple[np.ndarray, dict]:
    """
    Butterworth zero-phase bandpass 필터 (scipy.signal.sosfiltfilt).
    
    안전장치:
    - Nyquist 주파수 (fs_hz/2) 초과 방지
    - high_hz > Nyquist 이면 high_hz = Nyquist - 1.0으로 자동 낮춤
    - 신호 길이 < 2*order+5 이면 필터링 스킵 (불안정성 방지), 원본 반환
    
    반환:
      - filtered_signal: (n_time, n_subcarriers)
      - diagnostics: {'filter_skipped': bool, 'reason': str}
    """
    
    # Nyquist 주파수
    nyquist_hz = fs_hz / 2.0  # 100Hz 샘플링 → 50Hz Nyquist
    
    # 안전 체크
    if high_hz >= nyquist_hz:
        high_hz = nyquist_hz - 1.0  # 49Hz로 자동 강등
    
    if signal.shape[0] < 2 * max(order, 2) + 1:
        # 신호가 너무 짧음, 필터링 스킵
        return signal, {'filter_skipped': True}
    
    # Butterworth SOS (second-order sections) 설계
    sos = scipy.signal.butter(
        order, [low_hz, high_hz], btype='band',
        analog=False, output='sos', fs=fs_hz
    )
    
    # 각 부반송파에 zero-phase 적용
    filtered = np.zeros_like(signal)
    for i in range(signal.shape[1]):
        filtered[:, i] = scipy.signal.sosfiltfilt(sos, signal[:, i])
    
    return filtered, {'filter_skipped': False}
```

### 5.4 Step 3: Subcarrier Selection (상위 10개 선택)

**Source**: `src/streaming_features.py:137-165`, `src/preprocessing.py:46-104`

목표: 활동 신호가 강한 부반송파 10개만 선택 (노이즈 감소, 계산 효율)

#### q-value 메트릭 (활동 민감도)

```python
def _compute_q(signal_1d: np.ndarray) -> float:
    """
    Subcarrier 신호의 '활동성' 점수.
    
    q = max(moving_variance) / mean(moving_variance)
    
    해석:
    - q가 높음: 신호에 날카로운 변화/펄스 포함 (낙상 같은 활동)
    - q가 낮음: 신호가 평탄/잡음 (활동 없음)
    
    예:
    - 조용한 신호: MV = [1, 1.1, 0.9, 1.0, ...] → max/mean ≈ 1.1 (q ≈ 1)
    - 낙상 신호: MV = [1, 1, 50, 2, 1, ...] → max/mean ≈ 25 (q ≈ 25)
    """
    mv = _moving_variance(signal_1d)  # moving variance 계산
    mean_mv = np.mean(mv)
    
    if mean_mv < 1e-10:  # 거의 0 신호
        return 0.0
    
    return np.max(mv) / mean_mv
```

#### Subcarrier 선택

```python
def select_top_subcarriers_2d(
    signal: np.ndarray,     # (n_time, n_subcarriers)
    n_streams: int = 10     # 선택할 개수
) -> tuple[np.ndarray, SubcarrierSelection]:
    """
    상위 n_streams 부반송파 선택 (q-value 기준 내림차순).
    
    반환:
      - selected_signal: (n_time, n_streams)
      - metadata: SubcarrierSelection
        - indices: 선택된 부반송파 인덱스 리스트 (원본 인덱스)
        - q_values: 각 선택 부반송파의 q-value
    
    예:
    input: (100, 306)  # 100 타임스탭, 306 부반송파
    output: (100, 10), indices=[145, 234, 89, ...], q_values=[25.3, 23.1, ...]
    """
    
    q_scores = np.array([_compute_q(signal[:, i]) for i in range(signal.shape[1])])
    top_indices = np.argsort(-q_scores)[:n_streams]  # 내림차순 정렬 후 상위 n개
    
    selected = signal[:, top_indices]
    q_top = q_scores[top_indices]
    
    return selected, SubcarrierSelection(indices=top_indices, q_values=q_top)
```

### 5.5 Step 4: Sum & Z-score Normalize

**Source**: `src/streaming_features.py:167-180`

목표: 10개 부반송파 합산 후 표준화 (절댓값 비교 가능하게)

```python
def sum_and_normalize(signal: np.ndarray) -> np.ndarray:
    """
    선택된 부반송파들의 합을 z-score 정규화.
    
    step 1: summed = sum(signal, axis=1)  # (n_time, 10) → (n_time,)
    step 2: mean = np.mean(summed)
    step 3: std = np.std(summed)
    
    if std < 1e-8:  # 거의 0 std (flat 신호)
        return summed  # 정규화 스킵, 원본 반환
    else:
        return (summed - mean) / std
    
    반환: (n_time,) 1차원 배열 (z-score normalized)
    """
```

### 5.6 Step 5: Moving Variance (이동분산)

**Source**: `src/preprocessing.py:160-210`, `src/streaming_features.py:182-214`

목표: 신호 변동성의 시간 윈도우 평균 제곱편차 (움직임 강도 지표)

#### 수학식

```
υ(n; W) = (1/(2W)) * Σ_{k=n-W}^{n+W} (|h[k]| - μ)²

여기서:
- n: 현재 인덱스
- W: 반-윈도우 크기 (샘플)
- h[k]: 정규화된 신호 (z-score)
- μ: 윈도우 내 신호의 평균

기본값: W = 25 (0.5초 × 100Hz / 2)
```

#### 구현 (효율적 convolution 트릭)

```python
def _moving_variance(signal: np.ndarray, window_half_width: int = 25) -> np.ndarray:
    """
    Moving variance를 효율적으로 계산 (uniform_filter1d 사용).
    
    원리:
      - σ² = E[X²] - E[X]²
      - E[X²] = mean(signal²)를 윈도우로 convolution
      - E[X]  = mean(signal)을 윈도우로 convolution
      - mv = E[X²] - E[X]²
    
    scipy.ndimage.uniform_filter1d로 한 번에 처리.
    """
    W = window_half_width
    
    # 평균의 제곱
    mean_signal = scipy.ndimage.uniform_filter1d(
        signal, size=2*W+1, mode='nearest'
    )
    mean_sq = mean_signal ** 2
    
    # 제곱의 평균
    signal_sq = signal ** 2
    mean_sq_signal = scipy.ndimage.uniform_filter1d(
        signal_sq, size=2*W+1, mode='nearest'
    )
    
    # 분산 = E[X²] - E[X]²
    variance = mean_sq_signal - mean_sq
    
    # 스케일 조정 (정의상 분모가 2W+1, 우리는 2W를 원함)
    variance *= (2*W + 1) / (2*W)
    
    return variance
```

#### 임계값 설정 가이드

- **초보자**: `mv_threshold = 최대 관찰값 × 0.5`
  - 예: 최대 MV = 5.0 → threshold = 2.5 (현재 기본값)
- **미세 조정**: 실시간 대시보드에서 차트를 보며 상한선 조정
  - 오감지 많음 → threshold 올림
  - 감지 안 됨 → threshold 내림

### 5.7 Pipeline 실행 루프 [구현됨]

**Source**: `src/pipeline.py:146-160`, `main.py:lifespan`

```python
async def detection_loop(
    monitor: EspMonitor,
    cfg: PipelineConfig,
    executor: ThreadPoolExecutor,
    stop_event: asyncio.Event
):
    """
    0.5초마다 파이프라인 실행 (비동기 루프).
    
    1. stop_event 확인, 종료 시 break
    2. monitor.running이 False면 sleep만 하고 계속
    3. 그 외:
       a. time_start = time.time()
       b. pipeline.run_once(monitor) → executor에서 스레드 풀 실행
       c. 결과 수집 (timeout = cfg.stride_sec)
       d. monitor.set_detection_result(result) 저장
       e. 예외/타임아웃은 조용히 무시 (fail-safe)
    4. asyncio.sleep(cfg.stride_sec)
    """
```

**특징**:

- **비동기 실행**: FastAPI 메인 루프 블로킹 방지
- **타임아웃**: 0.5초 이상 걸리는 파이프라인은 강제 취소
- **예외 무시**: 파이프라인 에러로 인한 전체 크래시 방지 (resilience-first)

---

## 6. 낙상 감지 상태 머신

### 6.1 상태 다이어그램 [구현됨]

**Source**: `src/fall_state_machine.py:25-180`

```
                 ┌─────────────────────────────┐
                 │         IDLE                │
                 │  (대기 상태)                 │
                 └────────────┬────────────────┘
                              │
                    MV ≥ threshold
                              │
                              ▼
                 ┌─────────────────────────────┐
                 │       SUSPECT               │
                 │  (의심 상태, 지속 관찰)      │
                 └─┬──────────────────────┬────┘
                   │                      │
         지속시간 ≥  │    gap > merge_gap_s │
        min_duration_s│                      │
                   │                      │
                   │          MV < threshold
                   │                      │
                   ▼                      ▼
         ┌──────────────────┐   ┌──────────────────┐
         │      FALL        │   │       IDLE       │
         │   (낙상 감지!)     │   │  (의심 해제)     │
         └──┬─────────┬─────┘   └──────────────────┘
            │         │
            │         └─ MV < threshold
            │
     지속시간 ≥ │
    max_duration_s│
            │
            ▼
    ┌──────────────────┐
    │    COOLDOWN      │
    │   (재감지 방지)    │
    └────────┬─────────┘
             │
      경과시간 ≥
      cooldown_s
             │
             ▼
    ┌──────────────────┐
    │       IDLE       │
    │  (재시작)        │
    └──────────────────┘
```

### 6.2 상태 전이 조건

#### IDLE → SUSPECT

```python
if mv_current >= mv_threshold:
    state = SUSPECT
    _suspect_start_s = now_s
    _last_above_threshold_s = now_s
```

#### SUSPECT → FALL

```python
# SUSPECT 상태에서
if mv_current >= mv_threshold:
    _last_above_threshold_s = now_s
    
if (now_s - _suspect_start_s) >= min_duration_s:
    # min_duration_s 이상 임계값 초과 지속
    state = FALL
    just_triggered = True
    last_fall_at = datetime.now().isoformat()
    confidence = mv_current / mv_threshold
```

#### SUSPECT → IDLE (의심 해제)

```python
# SUSPECT 상태에서
if mv_current < mv_threshold:
    gap_since_last_above = now_s - _last_above_threshold_s
    
    if gap_since_last_above > merge_gap_s:
        # 0.25초 이상 임계값 이하 유지 → 해제
        state = IDLE
    else:
        # gap ≤ 0.25초 → 일시적 딥, SUSPECT 유지 (hysteresis/merge)
        state = SUSPECT
```

#### FALL → COOLDOWN (자동/강제)

```python
# FALL 상태에서
fall_duration = now_s - _fall_start_s

if mv_current < mv_threshold:
    # 신호 수렴 → COOLDOWN
    state = COOLDOWN
    
elif fall_duration >= max_duration_s:
    # 최대 2초 유지 후 강제 COOLDOWN (연쇄 감지 방지)
    state = COOLDOWN
```

#### COOLDOWN → IDLE

```python
# COOLDOWN 상태에서
cooldown_elapsed = now_s - _fall_end_s

if cooldown_elapsed >= cooldown_s:  # 기본 3.0초
    state = IDLE
    cooldown_remaining_s = 0
else:
    cooldown_remaining_s = cooldown_s - cooldown_elapsed
```

### 6.3 Confidence 계산

```python
confidence = mv_current / mv_threshold

# 예:
# mv_current = 3.5, mv_threshold = 2.5
# → confidence = 1.4 (임계값의 140%)

# UI 표시 시 1.0으로 cap:
# confidence_display = min(confidence, 1.0)  # 0.0~1.0 범위, 백분율 = ×100
```

**해석**:

- `confidence < 1.0`: 낙상 아직 의심 단계
- `confidence = 1.0`: 임계값 정확히 도달
- `confidence > 1.0`: 신뢰도 높음 (오감지 위험 낮음)

### 6.4 파라미터 튜닝 (민감도 조정)

| 목표 | 조정 |
|------|------|
| **너무 많은 오감지** (가짜 경보) | `mv_threshold` 올림 또는 `min_duration_s` 올림 |
| **감지 안 됨** (미감지) | `mv_threshold` 내림 또는 `min_duration_s` 내림 |
| **오래 지속되는 false fall** | `max_duration_s` 내림 또는 `cooldown_s` 올림 |
| **빠른 재감지** | `cooldown_s` 내림 (단, 경보 폭주 위험) |

---

## 7. 설정 파라미터 상세

### 7.1 파라미터표

전체 22개 설정값 (src/pipeline.py의 `PipelineConfig` 클래스; 아래 마지막 9개는 재실감지용 wander 신호/presence 파라미터. 상세 알고리즘/왜 두 개의 서로 다른 대역을 쓰는지/왜 wander에 디바운스가 있는지는 [`occupation_pipline.md`](./occupation_pipline.md) 참고):

| 항목 | 타입 | 기본값 | 문서값 | 범위 | 설명 |
|------|------|--------|--------|------|------|
| `window_sec` | float | 3.0 | 3.0 | 1.0~10.0 | CSI 슬라이딩 윈도우 (초) |
| `stride_sec` | float | 0.5 | 0.5 | 0.1~2.0 | 파이프라인 업데이트 주기 (초) |
| `fs_hz` | float | 100.0 | 100.0 | 50.0~200.0 | 리샘플링 목표 주파수 (Hz) |
| `mv_window_sec` | float | 0.5 | 0.5 | 0.1~2.0 | 이동분산 윈도우 (초) |
| `n_streams` | int | 10 | 10 | 1~50 | 선택할 부반송파 개수 |
| `bandpass_low` | float | 0.5 | 0.5 | 0.1~10.0 | 대역통과 필터 하한 (Hz) |
| `bandpass_high` | float | 50.0 | 50.0 | 10.0~100.0 | 대역통과 필터 상한 (Hz) |
| `bandpass_order` | int | 4 | 4 | 2~6 | Butterworth 필터 차수 |
| `mv_threshold` | float | 2.0 | 2.0 | 0.5~10.0 | 낙상 임계값. 2026-07-14부터 잠정적으로 민감하게 설정(이전 2.5) — 오탐 증가 감수, 추후 재조정 필요 (온보딩 캘리브레이션으로 자동 설정 가능) |
| `min_duration_s` | float | 0.5 | 0.5 | 0.1~2.0 | 낙상 확정 최소 지속 (초) |
| `merge_gap_s` | float | 0.25 | 0.25 | 0.05~1.0 | SUSPECT 병합 갭 (초) |
| `max_duration_s` | float | 2.0 | 2.0 | 0.5~10.0 | 낙상 최대 지속 (초) |
| `cooldown_s` | float | 3.0 | 3.0 | 0.5~10.0 | 재감지 lockout (초) |
| `wander_window_sec` | float | 6.0 | 6.0 | 2.0~15.0 | wander 신호용 슬라이딩 윈도우 (초, 라이브 감지 전용) |
| `wander_mv_window_sec` | float | 1.0 | 1.0 | 0.2~5.0 | 부반송파 선택 단계의 이동분산 윈도우 (초), PSD 계산과 무관 |
| `wander_prefilter_low` | float | 0.05 | 0.05 | 0.01~1.0 | wander **사전 필터** 하한 (Hz, 부반송파 선택/합산 이전 단계) |
| `wander_prefilter_high` | float | 5.0 | 5.0 | 1.0~10.0 | wander 사전 필터 상한 (Hz) |
| `wander_bandpass_low` | float | 0.1 | 0.1 | 0.05~1.0 | wander **Welch 에너지 측정** 대역 하한 (Hz, 호흡 대역) |
| `wander_bandpass_high` | float | 0.5 | 0.5 | 0.1~2.0 | wander Welch 에너지 측정 대역 상한 (Hz) |
| `wander_baseline` | float | 0.5 | 0.5 | 0.01~10.0 | 온보딩 캘리브레이션으로 측정한 baseline PSD 에너지 (비율 비교 기준값) |
| `wander_ratio_threshold` | float | 1.8 | 1.8 | 1.2~10.0 | `wander_current / wander_baseline` 트리거 배수. 2026-07-14부터 잠정적으로 민감하게 설정(이전 2.0) |
| `wander_min_duration_s` | float | 2.0 | 2.0 | 0.0~10.0 | wander 비율이 임계값 이상으로 끊김 없이 지속돼야 하는 최소 시간 (초) — 순간적 노이즈 스파이크 방지용 디바운스 |
| `presence_timeout_s` | float | 6.0 | 6.0 | 1.0~60.0 | MOVE/WANDER 미감지 후 ABSENT 전환까지 대기 시간 (초) |

⚠️ **주의사항**:

1. **`stride_sec`**와 **`window_sec`의 관계**: stride < window 권장 (겹치는 윈도우로 더 부드러운 감지)
2. **`fs_hz`**: 수신 패킷율과 무관하게 리샘플링 목표 (보간/decimation으로 조절)
3. **`bandpass_high`**: Nyquist 주파수(fs_hz/2) 미만이어야 함. 초과 시 자동으로 `fs_hz/2 - 1.0`으로 강등됨.
4. **`mv_threshold`/`wander_ratio_threshold`**: 2026-07-14부터 잠정적으로 민감하게 설정된 값(오탐 증가를 감수한 임시치). 실사용 데이터로 재조정 필요.

### 7.2 실시간 파라미터 변경

**Endpoint**: `POST /detection/config`

```bash
curl -X POST http://localhost:8000/detection/config \
  -H "Content-Type: application/json" \
  -d '{
    "mv_threshold": 3.0,
    "min_duration_s": 0.7
  }'
```

**응답**:

```json
{
  "status": "ok",
  "config": {
    "window_sec": 3.0,
    "stride_sec": 0.5,
    ...
    "mv_threshold": 3.0,
    ...
  }
}
```

**특징**:

- 재시작 불필요 (다음 파이프라인 실행부터 적용)
- 부분 업데이트 지원 (지정한 필드만 변경, 나머지는 유지)
- WS 클라이언트는 업데이트 후 다음 메시지에서 새로운 값 수신

---

## 8. API 명세

### 8.1 REST Endpoints

**Base URL**: `http://localhost:8000`

#### 1. GET `/`

정적 HTML 대시보드 제공.

```
GET / HTTP/1.1
Host: localhost:8000

→ 200 OK
Content-Type: text/html
<html>...</html>  (static/index.html)
```

#### 2. GET `/health`

헬스 체크.

```
GET /health HTTP/1.1

→ 200 OK
{"status": "ok"}
```

#### 3. POST `/monitor/start`

CSI 수집 시작 (시리얼 포트 열기).

```
POST /monitor/start HTTP/1.1
Content-Type: application/json

{
  "port": "COM3"        # Windows: COMx, Linux: /dev/ttyUSB0, macOS: /dev/tty.usbserial-*
}

→ 200 OK
{
  "running": true,
  "port": "COM3",
  "packet_count": 0,
  "error": null
}

→ 400 Bad Request (포트 열기 실패)
{
  "running": false,
  "port": "COM3",
  "packet_count": 0,
  "error": "Port COM3 not found"
}
```

#### 4. POST `/monitor/stop`

CSI 수집 중지 (시리얼 포트 닫기).

```
POST /monitor/stop HTTP/1.1

(body 비어있음)

→ 200 OK
{
  "running": false,
  "port": null,
  "packet_count": 125,
  "error": null
}
```

#### 5. GET `/monitor/status`

현재 모니터 상태 조회.

```
GET /monitor/status HTTP/1.1

→ 200 OK
{
  "running": true,
  "port": "COM3",
  "packet_count": 456,
  "error": null
}
```

#### 6. GET `/monitor/snapshot`

현재 감지 결과 스냅샷 조회 (WS 대신 HTTP 폴링용).

```
GET /monitor/snapshot HTTP/1.1

→ 200 OK
{
  "running": true,
  "packet_count": 500,
  "detection": {
    "state": "IDLE",
    "mv_current": 1.2,
    "mv_threshold": 2.5,
    "confidence": 0.48,
    "cooldown_remaining_s": 0.0,
    "last_fall_at": "2026-07-03T08:35:42.123456",
    "just_triggered": false,
    "selection": {
      "indices": [145, 234, 89, 267, ...],
      "q_values": [25.3, 23.1, 21.5, ...]
    },
    "quality": {
      "interp_steps": 98,
      "fallback_steps": 2,
      "irregular_gaps": 0,
      "nonpositive_gaps": 0,
      "actual_pps": 99.8,
      "window_duration_s": 3.001
    },
    "final_signal": [0.1, -0.3, 1.2, ...],  # (n_time,) z-score normalized
    "pipeline_latency_ms": 12.34,
    "updated_at": "2026-07-03T08:35:44.567890"
  }
}
```

#### 7. GET `/detection/config`

현재 파이프라인 설정 조회.

```
GET /detection/config HTTP/1.1

→ 200 OK
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
  "cooldown_s": 3.0
}
```

#### 8. POST `/detection/config`

파이프라인 설정 업데이트 (부분 변경 가능).

```
POST /detection/config HTTP/1.1
Content-Type: application/json

{
  "mv_threshold": 3.0,
  "cooldown_s": 4.0
}

→ 200 OK
{
  "status": "ok",
  "config": {
    "window_sec": 3.0,
    "stride_sec": 0.5,
    ...
    "mv_threshold": 3.0,
    ...
    "cooldown_s": 4.0,
    ...
  }
}

→ 422 Unprocessable Entity (타입 오류)
{
  "detail": [
    {
      "type": "float_parsing",
      "loc": ["body", "mv_threshold"],
      "msg": "Input should be a valid number"
    }
  ]
}
```

### 8.2 WebSocket Endpoint

#### `/ws/live`

실시간 감지 결과 스트림 (10Hz, 100ms 간격).

**연결**:

```
GET /ws/live HTTP/1.1
Upgrade: websocket
Connection: Upgrade
```

**수신 메시지** (JSON, 100ms마다):

```json
{
  "running": true,
  "packet_count": 512,
  "detection": {
    "state": "FALL",
    "mv_current": 3.2,
    "mv_threshold": 2.5,
    "confidence": 1.28,
    "cooldown_remaining_s": 0.0,
    "last_fall_at": "2026-07-03T08:35:42.123456",
    "just_triggered": true,
    "selection": {...},
    "quality": {...},
    "final_signal": [...],
    "pipeline_latency_ms": 9.87,
    "updated_at": "2026-07-03T08:35:44.123456"
  }
}
```

**특징**:

- **푸시 방식**: 서버가 10Hz로 일방향 전송 (클라이언트는 수신만)
- **손실 허용**: 네트워크 지연 시 오래된 메시지 삭제
- **자동 재연결**: 클라이언트에서 1500ms 후 자동 재연결 시도

### 8.3 Pydantic 모델 (요청/응답)

#### StatusResponse

```python
class StatusResponse(BaseModel):
    running: bool        # 모니터 실행 여부
    port: str | None     # 시리얼 포트 이름 (실행 중이 아니면 null)
    packet_count: int    # 누적 수신 패킷
    error: str | None    # 에러 메시지 (성공 시 null)
```

#### SnapshotResponse

```python
class SnapshotResponse(BaseModel):
    running: bool
    packet_count: int
    detection: DetectionInfo  # 아래 참고
```

#### DetectionInfo

```python
class DetectionInfo(BaseModel):
    state: str                           # "IDLE", "SUSPECT", "FALL", "COOLDOWN"
    mv_current: float                    # 현재 이동분산값
    mv_threshold: float                  # 임계값
    confidence: float                    # mv_current / mv_threshold (unbounded)
    cooldown_remaining_s: float          # COOLDOWN 남은 시간 (아니면 0)
    last_fall_at: str                    # ISO8601 타임스탐프 (없으면 null)
    just_triggered: bool                 # 이 틱에서 FALL 진입했는가?
    selection: SubcarrierSelection       # 선택된 부반송파 정보
    quality: SignalQuality               # 신호 품질 메트릭
    final_signal: list[float]            # 정규화된 신호 (z-score)
    pipeline_latency_ms: float           # 파이프라인 실행 시간
    updated_at: str                      # 타임스탐프
```

#### SubcarrierSelection

```python
class SubcarrierSelection(BaseModel):
    indices: list[int]      # 선택된 부반송파 인덱스 (0~305)
    q_values: list[float]   # 각 부반송파의 q-value
```

#### SignalQuality

```python
class SignalQuality(BaseModel):
    interp_steps: int         # 보간된 샘플 수
    fallback_steps: int       # 큰 갭으로 인한 fallback 샘플
    irregular_gaps: int       # 2ms 초과 갭 개수
    nonpositive_gaps: int     # 음수/0 갭 개수 (시계 역행)
    actual_pps: float         # 실제 패킷율 (Hz)
    window_duration_s: float  # 실제 윈도우 지속시간
```

#### DetectionConfigUpdate

```python
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
    # 모두 선택사항 (부분 업데이트 지원)
```

#### StartRequest

```python
class StartRequest(BaseModel):
    port: str    # 시리얼 포트 이름
```

---

## 9. 프론트엔드 대시보드

### 9.1 개요 [구현됨]

**파일**: `static/index.html`

- **단일 HTML 파일**: ~975줄 vanilla JavaScript (프레임워크 없음, 빌드 단계 없음)
- **UI 프레임워크**: 커스텀 CSS (dark glass-panel 테마)
- **언어**: 한국어

### 9.2 페이지별 기능

#### A. 모니터링 (Monitor) 페이지 [구현됨]

**좌측 사이드바**:

| 항목 | 기능 |
|------|------|
| **상태 표시** | 녹색/빨강 점 + "실행중"/"중지됨" 텍스트 |
| **ESP 포트** | 텍스트 입력 (placeholder: `COM3`, 실행 중 비활성화) |
| **시작 버튼** | 포트 입력 → POST `/monitor/start` 호출 |
| **중지 버튼** | POST `/monitor/stop` 호출 (실행 중일 때만 활성화) |
| **낙상 상태** | 컬러 배지: idle(회색)/suspect(주황)/fall(빨강)/cooldown(파랑) |
| **마지막 낙상** | ISO8601 타임스탐프 (처음에는 "감지되지 않음") |
| **네비게이션** | 4개 탭 버튼: 모니터링/탐지설정/낙상기록/Train |
| **이벤트 로그** | 80줄 제한의 스크롤 로그 (시작/중지/에러/낙상 이벤트) |

**우측 메인 영역**:

| 항목 | 설명 | 구현 |
|------|------|------|
| **상태 (State)** | 현재 상태 (한글 번역) | [구현됨] |
| **MV값** | `mv_current` (소수점 3자리) | [구현됨] |
| **신뢰도** | `confidence × 100` (백분율, 소수점 1자리) | [구현됨] |
| **임계값** | `mv_threshold` (소수점 3자리) | [구현됨] |
| **이동분산 라인 차트** | 최근 240틱 MV값 시각화 (canvas, 수동 그리기) | [구현됨] |
|   - 파란색 라인 | 5-샘플 평활된 `mv_current` | [구현됨] |
|   - 빨간색 수평선 | `mv_threshold` (고정) | [구현됨] |
| **CSI 진폭 히트맵** | README에 기술, 미구현 | [문서상 계획, 미구현] |
| **신뢰도 바 위젯** | README에 기술, 미구현 | [문서상 계획, 미구현] |
| **신호 품질 (PPS/gap)** | README에 기술, 미구현 | [문서상 계획, 미구현] |

**이동분산 차트 상세**:

```
Y축: 자동 스케일 (min(MV)에서 max(MV)×1.08까지)
X축: 시간 (마지막 240틱 = 240 × 100ms = 24초)

데이터 수집:
- WS 메시지마다 new_mv = detection.mv_current 추가
- smoothed_mv = 5-샘플 moving average 적용
- 차트 버퍼 = deque(maxlen=240) → 자동 오래된 항목 제거

렌더링:
- HTML5 Canvas 사용
- 매 WS 메시지마다 redraw (10Hz)
- 디바이스 픽셀 비율(DPR) 대응 (고해상도 모니터)
```

#### B. 탐지설정 (Detection Config) 페이지 [구현됨]

13개 수치 입력 폼 (1:1 `PipelineConfig` 매핑):

```
[ window_sec          ] [ stride_sec         ]
[ fs_hz               ] [ mv_window_sec      ]
[ n_streams           ] [ bandpass_low       ]
[ bandpass_high       ] [ bandpass_order     ]
[ mv_threshold        ] [ min_duration_s     ]
[ merge_gap_s         ] [ max_duration_s     ]
[ cooldown_s          ]

[적용] 버튼
```

**동작**:

1. 페이지 로드: `GET /detection/config` → 폼 자동 채우기
2. 사용자 수정 → `적용` 클릭
3. POST `/detection/config` (비어있지 않은 필드만)
4. 응답 받으면 폼 다시 로드 (서버 확정값 표시)
5. "설정이 적용되었습니다" 메시지 표시 (3초 후 자동 소실)

#### C. 낙상기록 (Fall History) 페이지 [구현됨]

**테이블**:

```
┌────────────────────────────┬──────────┐
│       시각 (Timestamp)      │ 신뢰도(%) │
├────────────────────────────┼──────────┤
│ 2026-07-03T08:35:42.123456 │  128     │
│ 2026-07-03T08:34:15.987654 │   95     │
│ ...                        │ ...      │
└────────────────────────────┴──────────┘
```

**특징**:

- **클라이언트 메모리만**: 서버 저장 안 함, 페이지 새로고침 시 초기화
- **데이터 소스**: WS 메시지의 `detection.just_triggered = true`일 때 캡처
  ```javascript
  if (detection.just_triggered) {
      fallHistory.push({
          timestamp: detection.updated_at,
          confidence: detection.confidence
      });
      // 최대 100개 유지
      if (fallHistory.length > 100) fallHistory.shift();
  }
  ```
- **최대 100개 항목 유지** (오래된 순서대로 제거)

#### D. Train 페이지 [문서상 계획, 미구현]

```
DNN 모델 학습 기능은 추후 제공될 예정입니다.
(DNN Training feature coming soon)
```

### 9.3 알람 모달 [구현됨]

**트리거**: WS 메시지에서 `detection.just_triggered = true`

**표시**:

```
┌─────────────────────────────────┐
│  ⚠️ 낙상 감지됨                  │
│                                 │
│  System detected a potential    │
│  fall. Please respond if needed.│
│                                 │
│         [확인]                   │
└─────────────────────────────────┘
```

**동작**:

- **수동 dismiss**: `확인` 버튼 클릭
- **자동 dismiss**: 5000ms 후 자동 닫힘 (`setTimeout`)
- **시각적**: 풀스크린 어두운 오버레이 + 중앙 흰색 박스

### 9.4 통신 흐름 [구현됨]

#### 연결 시작 (페이지 로드)

```
1. GET / → index.html 로드
2. JavaScript 실행 (connectWs, refreshStatus, loadDetectionConfig)
3. WS 연결 시작 (ws://localhost:8000/ws/live)
4. REST 폴링:
   - GET /monitor/status (현재 상태 확인)
   - GET /detection/config (설정값 로드)
```

#### 실시간 업데이트 (모니터링 중)

```
Server 10Hz (100ms):
  → WS 메시지 (SnapshotResponse)
      ├─ running, packet_count
      └─ detection {state, mv_current, ...}

Client (WS onmessage):
  → UI 업데이트
      ├─ 상태 배지 색 변경
      ├─ MV/신뢰도/임계값 스탯 카드 업데이트
      ├─ 차트 데이터 추가
      └─ just_triggered = true면 알람 표시
```

#### REST 폴링 (비실시간)

```
GET /monitor/status:
  - 모니터 시작/중지 후 1회 호출
  - WS 연결 끊김 시 폴백 (추가 정보 확인 용)

GET /detection/config:
  - 페이지 로드 시 1회
  - 설정 적용 후 1회 (확인값 표시용)

POST /detection/config:
  - 적용 버튼 클릭 시 (비어있지 않은 필드만)
```

#### 재연결 로직

```
WS.onclose:
  → 1500ms 후 자동 재연결 시도
  → 3회 이상 실패 시 "WS 연결 끊김" 경고
  
사용자 수동 재설정:
  → 새로고침(F5) → 자동 재연결
```

### 9.5 [문서상 계획, 미구현] 기능

#### CSI 진폭 히트맵

**설계**: README/API.md에 기술

```
- 시간축: 최근 N초
- 주파수축: 306개 부반송파
- 색상: 진폭 크기 (파랑~빨강 히트맵)
- 업데이트: 10Hz
```

**현재 상태**:

- `SnapshotResponse` 모델에 `csi_amplitudes` 필드 없음 (데이터 소스 부재)
- index.html에 렌더 코드 없음
- **필요한 작업**:
  1. main.py에서 DetectionInfo에 `csi_amplitudes` 필드 추가 (최신 window의 진폭)
  2. static/index.html에 canvas 기반 히트맵 렌더러 구현
  3. 테스트 & 성능 프로파일링 (306×300 매트릭스 10Hz는 계산량 많음)

#### 신뢰도 바 위젯

**설계**: README에 기술

```
0% ├────────────●────────────┤ 100%
   └─────────┬─────────┘
           confidence %
```

**현재 상태**:

- 스탯 카드에 백분율로만 표시 (바 형태 없음)
- **필요한 작업**:
  1. HTML에 `<progress>` 태그 또는 CSS bar 요소 추가
  2. confidence 값으로 프로그레스 업데이트

#### 신호 품질 위젯

**설계**: README에 기술

```
PPS: 99.8 fps ██████████
Gap: 0
Interp: 98
```

**현재 상태**:

- `SignalQuality` 데이터는 WS로 전송되지만 UI에 표시 안 됨
- **필요한 작업**:
  1. HTML에 품질 표시 영역 추가
  2. quality.actual_pps, quality.irregular_gaps 등을 스탯 카드로 렌더

---

## 10. 파일 및 디렉터리 구조

### 10.1 폴더 트리

```
tools/fall_detect/
├── .gitignore                          # env/, .vscode/, build/, __pycache__/, data/
├── SPEC.md                             # 이 파일 (기능 명세서)
├── README.md                           # 빠른 시작 & 아키텍처 개요
├── API.md                              # REST/WS API 상세 레퍼런스
├── main.py                             # FastAPI 서버, CSI 파싱, API 라우트
├── requirements.txt                    # pip 의존성
│
├── src/
│   ├── __pycache__/                    # Python 컴파일 바이트코드 (빌드)
│   ├── fall_state_machine.py           # FallDetector 상태 머신 구현
│   ├── pipeline.py                     # PipelineConfig, DetectionPipeline, detection_loop
│   ├── streaming_features.py           # 실시간 신호처리 (resample, filter, select, MV)
│   ├── preprocessing.py                # 오프라인 레퍼런스 파이프라인 & q-value 구현
│   └── true_activity_time.py           # 오프라인 배치 분석 CLI (실행 불가, 알고리즘 문서용)
│
├── static/
│   └── index.html                      # 단일 HTML 대시보드 (vanilla JS, inline CSS)
│
├── tests/
│   └── test_pipeline.py                # 7개 단위 테스트 (MV, q-value, 필터, 상태 머신 등)
│
└── data/
    └── esp32_1/
        └── csi_260703_014318.csv       # 샘플 CSI 캡처 (gitignored, 헤더만 있음)
```

### 10.2 핵심 파일 설명

| 파일 | 줄 수 | 목적 |
|------|------|------|
| `main.py` | ~440 | FastAPI 메인 서버, 시리얼 수집, API 라우트 |
| `src/fall_state_machine.py` | ~180 | IDLE→SUSPECT→FALL→COOLDOWN 상태 로직 |
| `src/pipeline.py` | ~160 | 파이프라인 오케스트레이션, 설정 관리 |
| `src/streaming_features.py` | ~220 | resample, bandpass, subcarrier select, MV 계산 |
| `src/preprocessing.py` | ~210 | 오프라인 레퍼런스 알고리즘, q-value 구현 |
| `static/index.html` | ~975 | 브라우저 대시보드, vanilla JS |
| `tests/test_pipeline.py` | ~250 | 단위 테스트 |

### 10.3 .gitignore

```
env/              # Python 가상환경
.vscode/          # VSCode 설정
build/            # 빌드 산출물
__pycache__/      # 파이썬 바이트코드
data/             # 로컬 CSI 캡처 (gitignored)
```

---

## 11. 테스트

### 11.1 테스트 커버리지

**파일**: `tests/test_pipeline.py`

```bash
# 실행 방법
cd tools/fall_detect
python tests/test_pipeline.py
# 또는
pytest tests/test_pipeline.py -v
```

### 11.2 테스트 항목

| # | 테스트명 | 검증 내용 |
|---|---------|---------|
| 1 | `test_moving_variance_flat_signal` | 평탄 신호(MV ≈ 0) vs 스파이크 신호(MV 높음) 구분 |
| 2 | `test_q_value_sensitivity` | q-value가 활동(높은 MV 변동) 감지 |
| 3 | `test_subcarrier_selection` | 상위 n개 부반송파 올바르게 선택 |
| 4 | `test_bandpass_nyquist_clamping` | fs=100Hz일 때 high_hz 자동 49Hz로 강등 |
| 5 | `test_state_machine_transitions` | IDLE→SUSPECT→FALL→COOLDOWN→IDLE 순차 전이 |
| 6 | `test_synthetic_burst_end_to_end` | 합성 낙상 신호 전체 파이프라인 실행 |
| 7 | `test_timestamp_unwrap` | 32비트 타임스탐프 오버플로우 처리 |

### 11.3 테스트 실행 예시

```python
# test_state_machine_transitions 예시
def test_state_machine_transitions():
    detector = FallDetector(
        mv_threshold=1.0, min_duration_s=0.5, merge_gap_s=0.25,
        max_duration_s=2.0, cooldown_s=3.0
    )
    
    # IDLE (t=0s)
    result = detector.update(now_s=0.0, mv_value=0.5)
    assert result.state == FallState.IDLE
    
    # SUSPECT (t=0.1s, mv >= threshold)
    result = detector.update(now_s=0.1, mv_value=1.5)
    assert result.state == FallState.SUSPECT
    
    # FALL (t=0.6s, 0.5초 이상 유지)
    result = detector.update(now_s=0.6, mv_value=1.5)
    assert result.state == FallState.FALL
    assert result.just_triggered == True
    
    # COOLDOWN (t=0.7s)
    result = detector.update(now_s=0.7, mv_value=0.3)
    assert result.state == FallState.COOLDOWN
    
    # IDLE (t=3.8s, 3초 cooldown 경과)
    result = detector.update(now_s=3.8, mv_value=0.5)
    assert result.state == FallState.IDLE
```

---

## 12. 알려진 이슈 및 제약사항

### 12.1 문서-코드 불일치

| 항목 | 문서 | 코드 | 영향 |
|------|------|------|------|
| CSI 진폭 히트맵 | 있다고 설명 | 데이터 소스 없음 | 대시보드에 미구현 |
| Confidence Bar | 있다고 설명 | UI 미구현 | 스탯 카드만 표시 |
| Signal Quality 위젯 | 있다고 설명 | UI 미구현 | 데이터는 전송되지만 미표시 |

### 12.2 시스템 제약사항

| 제약 | 설명 | 해결책 |
|------|------|--------|
| **2대 보드 필수** | Transmitter + Receiver 모두 필요 | ESP32-C5 2개 구매 |
| **5GHz WiFi 필수** | 일부 2.4GHz 전용 라우터 비호환 | WiFi 라우터 5GHz 대역 확인 |
| **고정 채널 48** | 다른 장비와 간섭 가능 | 간섭 적은 채널로 변경 후 재컴파일 |
| **낙상 기록 미영속화** | 페이지 새로고침 시 기록 소실 | 서버 DB 추가 구현 필요 |
| **GUI 튜닝 필요** | mv_threshold 등을 수동 조정 | 자동 보정 알고리즘 미지원 |

### 12.3 실행 불가능한 파일

**`src/true_activity_time.py`**

- 목적: 오프라인 배치 분석 (Mendeley 낙상 데이터셋용)
- 문제: 누락된 모듈 `src.dwt_coef.data_loader` / `src.dwt_coef.preprocessing`
- 용도: **알고리즘 문서용 레퍼런스만 (실행 불가)**
- 해결책: 오프라인 분석이 필요하면 별도 구현

### 12.4 샘플 CSI 데이터

**`data/esp32_1/csi_260703_014318.csv`**

- 상태: 헤더만 있고 데이터 없음 (gitignored)
- 원인: 미완성 테스트 또는 캡처 실패
- 용도: 실제 기능에 미사용

---

## 13. 트러블슈팅

### 13.1 "packet_count = 0" 진단 흐름

브라우저 대시보드에서 "신호 없음" 또는 packet_count가 0으로 유지되는 경우:

**Step 1: Transmitter 전원 확인**
```
✓ Transmitter ESP32 보드에 전원 공급 중인가?
✓ LED 깜빡임 등 동작 신호가 보이는가?
→ 가장 흔한 원인
```

**Step 2: 시리얼 포트 열기 오류**
```
GET /monitor/status
→ error 필드 확인
  - error = "Port COM3 not found" → 포트명 확인
  - error = "Permission denied" → 포트 권한 문제
  - error = null → 포트 정상
```

**Step 3: 백엔드 콘솔 로그**
```
python main.py --host 0.0.0.0 --port 8000
→ 스타트업 로그:
  ✓ "serial port COM3 opened successfully (baud=921600)"가 보이는가?
  → 없으면 포트 오픈 실패 (위 Step 2 참고)
```

**Step 4: 주기적 진단 로그 (10초마다)**
```
콘솔:
  "2026-07-03 08:35:45 | 54321 bytes | 100 frames | 0 rejected | 100 packet_count | OK"
  → 패킷 수신 중 (정상)
  
  "2026-07-03 08:35:55 |     0 bytes |   0 frames | 0 rejected |   0 packet_count | ERROR: No RX data"
  → 바이트 수신 0 (시리얼 선로 문제)
  
  "2026-07-03 08:36:05 |  5432 bytes |   5 frames | 95 rejected |   5 packet_count | ERROR: 95 checksum failures"
  → 대부분의 프레임이 체크섬 실패
```

**Step 5: 프레임 체크섬 다중 실패 진단**
```
가능한 원인:
  1. magic 0xA55A 불일치 (펌웨어 버전 차이)
  2. 보드율 921600이 아닌 다른 값 (하드웨어 설정 오류)
  3. Receiver 스레드 멈춤 (드물지만 발생 가능)
  4. USB 케이블 불량 (간헐적 신호 손상)

해결책:
  - csi_recv 펌웨어 재플래시
  - 보드율 다시 확인 (isp_tool 로그)
  - 백엔드 재시작
  - USB 케이블 교체
```

**Step 6: WiFi 채널 확인**
```
Receiver 보드 콘솔:
  → "Channel: 48" 또는 유사 로그 확인
  
Transmitter:
  → 동일 채널 48에서 전송 중 확인 (핀아웃 or LED)
```

### 13.2 임계값 캘리브레이션

**목표**: mv_threshold를 최적값으로 설정하여 오감지/미감지 방지

**Step 1: 기본값 관찰**
```
대시보드 [모니터링] 탭 → 이동분산 차트 보기

조용한 상태 (no activity):
  → MV ≈ 0~0.5 (낮음)

손으로 흔들 때:
  → MV peaks ≈ 3.0~5.0 (높음)

낙상 시뮬레이션 (의자에서 일어서기, 몸 던지기):
  → MV peaks ≈ 5.0~10.0 (매우 높음)
```

**Step 2: 초기 임계값 추천**
```
관찰된 max MV × 0.5 = 초기 threshold
  예: max = 5.0 → threshold = 2.5 (현재 기본값)
```

**Step 3: 미세 조정**
```
오감지 많음:
  → threshold ↑ (예: 2.5 → 3.0)
  
미감지:
  → threshold ↓ (예: 2.5 → 2.0)
  
또는 min_duration_s 조정 (현재 0.5초):
  오감지 많음 → 1.0초로 올림 (확실한 낙상만 감지)
  미감지 → 0.3초로 내림 (빠른 감지)
```

**Step 4: 실시간 테스트**
```
대시보드 [탐지설정] 탭에서:
  mv_threshold = 새로운 값 입력
  [적용] 클릭
  → 다음 파이프라인 실행부터 적용 (재시작 불필요)

[모니터링] 탭에서 차트 관찰:
  임계값 선(빨강)이 원하는 위치에 있는지 확인
```

### 13.3 WebSocket 연결 끊김

**증상**: 브라우저 콘솔에 `WS 연결 끊김` 경고

**원인 & 해결**:

| 원인 | 확인 방법 | 해결책 |
|------|----------|--------|
| 방화벽 포트 8000 차단 | `netstat -an \| find "8000"` | 방화벽 규칙 추가 |
| 백엔드 충돌 | `lsof -i :8000` | 포트 해제 후 재시작 |
| 네트워크 불안정 | ping / 신호 강도 확인 | WiFi/유선 재연결 |
| 페이지 새로고침 필요 | 브라우저 콘솔 확인 | F5 새로고침 |

---

## 14. 참고 자료

### 14.1 외부 문서

| 문서 | 위치 | 내용 |
|------|------|------|
| README.md | `tools/fall_detect/` | 빠른 시작, 아키텍처 개요, 트러블슈팅 |
| API.md | `tools/fall_detect/` | REST/WS API 상세 (JSON 스키마, curl 예시) |
| main.py | `tools/fall_detect/` | FastAPI 서버, CSI 파싱 (main.py:29~440) |
| src/fall_state_machine.py | `tools/fall_detect/src/` | 상태 머신 로직 (line 25~180) |
| src/pipeline.py | `tools/fall_detect/src/` | 파이프라인 오케스트레이션 (line 20~160) |
| src/streaming_features.py | `tools/fall_detect/src/` | 신호처리 상세 (line 50~220) |
| csi_recv/main/app_main.c | `csi_recv/` | Receiver 펌웨어 (magic 0xA55A 참조) |
| csi_send/ | `csi_send/` | Transmitter 펌웨어 (ESP-NOW 100Hz) |

### 14.2 주요 상수 및 기본값

| 항목 | 값 | 참조 |
|------|-----|------|
| WiFi 채널 | 48 | csi_recv/main/app_main.c, config |
| TX MAC (spoofed) | `1a:00:00:00:00:00` | csi_send/, main.py 필터링 |
| 시리얼 속도 | 921600 bps, 8N1 | main.py:342, csi_recv 펌웨어 |
| Binary frame magic | `0xA55A` | main.py:29 |
| CSI 부반송파 수 | 306개 | main.py:410 (IQ 612/2) |
| CSI 버퍼 크기 | 1500 프레임 | main.py 초기화, ~15초 @100pps |
| 파이프라인 주기 | 0.5초 (stride_sec) | src/pipeline.py:20 |
| WS 푸시 속도 | 10Hz (100ms) | main.py WS handler |

### 14.3 알고리즘 레퍼런스

- **Q-value**: `src/preprocessing.py:_compute_q()` — 신호 활동 민감도 지표
- **Moving Variance**: `src/preprocessing.py:_moving_variance()` — 신호 변동성 윈도우 평균
- **State Machine**: `src/fall_state_machine.py:FallDetector` — 임계값 기반 상태 전이
- **Bandpass Filter**: `src/streaming_features.py:safe_bandpass()` — Butterworth zero-phase

---

## 부록: 용어 정리

| 용어 | 정의 | 비고 |
|------|------|------|
| **CSI** | Channel State Information | WiFi 신호의 amplitude/phase 정보 |
| **부반송파** (subcarrier) | OFDM 신호의 개별 주파수 채널 (0~305) | 총 306개 |
| **이동분산** (Moving Variance) | 시간 윈도우 내 신호의 표준편차 제곱 | 움직임 강도 지표 |
| **Q-value** | max(MV) / mean(MV) | 신호의 활동성 민감도 |
| **임계값** (threshold) | 낙상 감지 기준 MV 값 | 기본 2.5 |
| **상태 머신** | IDLE→SUSPECT→FALL→COOLDOWN | 낙상 감지 로직의 핵심 |
| **Confidence** | MV / threshold (%) | 임계값 대비 얼마나 초과했는가 |
| **Unwrap** | 32비트 타임스탐프 overflow 처리 | +2^32 offset 적용 |
| **Resampling** | 불규칙 시간 → 규칙적 100Hz 그리드 | Linear interpolation |
| **Bandpass Filter** | 2~50Hz 통대역 추출 | Butterworth, zero-phase |

---

**End of Specification Document**

---

**작성자**: Claude Code (AI Assistant)  
**작성일**: 2026-07-03  
**버전**: 1.0  
**대상**: tools/fall_detect MVP v1 (commit 9bba36c 이후)

**문서 변경 이력**:

| 버전 | 날짜 | 변경 내용 |
|------|------|---------|
| 1.0 | 2026-07-03 | 초안 작성, 코드 조사 기반 상세 명세서 완성 |
