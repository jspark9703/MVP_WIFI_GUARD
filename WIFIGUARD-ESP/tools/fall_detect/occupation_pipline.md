# 재실 감지 (Occupation/Presence Detection) 파이프라인

> **대상**: `tools/fall_detect` — 재실 감지 기능 (PRESENT/ABSENT 상태머신 + Wander 신호)
> **관련 코드**: `src/pipeline.py`, `src/presence_state_machine.py`, `src/onboarding.py`, `src/streaming_features.py`, `main.py`, `static/index.html`
> **관련 문서**: [`SPEC.md`](./SPEC.md) §7.1 (전체 파라미터표), [`API.md`](./API.md) (`## Onboarding`, `DetectionInfo`)

---

## 1. 개요

재실 감지는 기존 낙상 감지(MV 신호 + `FallDetector`)와 **병렬로 동작하는 별도의 신호 체계**다. 목적은 "낙상이 났는가"가 아니라 "이 공간에 지금 사람이 있는가"를 판단하는 것이며, 다음 두 신호를 결합해 판정한다.

| 신호 | 의미 | 판정 기준 | 용도 |
|---|---|---|---|
| **MV** (Moving Variance) | 걷기·자세변경 등 큰 폭의 신체 움직임 | `mv_current ≥ mv_threshold` | 낙상 감지와 **공유**하는 기존 신호 |
| **Wander** | 정지 상태에서의 호흡·미세움직임 등 잔향 신호. 0.1~0.5Hz 대역의 **Welch PSD 에너지** | `wander_current / wander_baseline ≥ wander_ratio_threshold` (기본 2.0배) | 재실 감지 전용 신규 신호 |

두 신호 중 **하나라도** 조건을 넘으면 "활동(activity)"으로 간주해 활동 시각을 갱신하고, 이후 일정 시간(`presence_timeout_s`) 동안 아무 활동도 없으면 퇴실(ABSENT)로 판정한다. 이 설계는 "유효한 MOVE → 지속적인 WANDER = 재실, 유효한 MOVE → 일정시간 NO_WANDER = 퇴실" 규칙을 그대로 구현한 것이다.

MV와 Wander는 **같은 신호처리 체인**(`compute_final_signal`)의 앞 4단계(리샘플링~정규화)를 서로 다른 파라미터로 재사용하되, 마지막 단계만 다르다 — MV는 이동분산, Wander는 **주파수영역 Welch PSD 밴드 에너지**를 쓴다. Wander는 절대 임계값이 아니라 **온보딩 캘리브레이션으로 측정한 baseline 대비 비율**로 판정한다.

---

## 2. 신호 처리 체인

`src/streaming_features.py`의 `compute_final_signal()`은 다음 단계를 수행한다:

```
resample_signal        불규칙 수신 간격 → fs_hz 정규 그리드로 리샘플링
        ↓
safe_bandpass           Butterworth 대역통과 필터 (zero-phase, sosfiltfilt)
        ↓
select_top_subcarriers_2d   q-value(활동성 지표) 상위 n_streams개 부반송파 선택
        ↓
sum_and_normalize       선택된 부반송파 합산 + z-score 정규화  →  final_signal
        ↓
   ┌────────────────────┴────────────────────┐
   ▼ (MV 패스)                                ▼ (Wander 패스, compute_band_energy=True)
_moving_variance                    compute_band_energy_welch
이동분산 계산 → mv_current                Welch PSD → [energy_band_low, energy_band_high]
                                     구간을 적분 → band_energy
```

`DetectionPipeline.run_once()` (`src/pipeline.py`)에서 이 함수를 **두 번** 호출한다:

```python
# 1) 기존 MV 신호 (낙상 감지 겸용) — window_sec=3.0초 윈도우, bandpass 2~50Hz
result = compute_final_signal(
    timestamps_us, amp_2d,
    window_sec=cfg.window_sec, fs_hz=cfg.fs_hz, omega=cfg.omega,
    n_streams=cfg.n_streams,
    bandpass_low=cfg.bandpass_low, bandpass_high=cfg.bandpass_high,
    bandpass_order=cfg.bandpass_order,
)

# 2) Wander 신호 — 별도의 6초 윈도우, 두 개의 서로 다른 대역을 사용
wander_window = monitor.get_window(cfg.wander_window_sec)
wander_result = compute_final_signal(
    w_ts, w_amp,
    window_sec=cfg.wander_window_sec, fs_hz=cfg.fs_hz, omega=cfg.wander_omega,
    n_streams=cfg.n_streams,
    bandpass_low=cfg.wander_prefilter_low, bandpass_high=cfg.wander_prefilter_high,  # 0.05~5Hz, 사전 필터
    bandpass_order=cfg.bandpass_order,
    compute_band_energy=True,
    energy_band_low=cfg.wander_bandpass_low, energy_band_high=cfg.wander_bandpass_high,  # 0.1~0.5Hz, 측정 대역
)
wander_current = wander_result.band_energy
```

### 왜 두 개의 서로 다른 대역(사전 필터 vs 측정 대역)을 쓰는가

처음 구현에서는 `bandpass_low`/`bandpass_high`(사전 필터)와 Welch 측정 대역을 **똑같이 0.1~0.5Hz**로 맞췄었는데, 테스트에서 간헐적으로 실패하는 문제가 발견됐다. 원인을 추적한 결과:

- `safe_bandpass`(사전 필터)가 이미 신호를 0.1~0.5Hz로 제한한 뒤,
- `sum_and_normalize`가 항상 z-score 정규화(총 분산 ≈ 1)를 하고,
- 그 뒤에 **같은** 0.1~0.5Hz 구간에 대해 Welch 에너지를 측정하면 —

사전 필터가 이미 "전체 에너지가 그 대역 안에 있음"을 보장해버리기 때문에, 진짜 0.3Hz 진동 신호든 그냥 필터를 통과한 잡음이든 상관없이 측정값이 거의 항상 "총 에너지의 대부분"이 되어버려 **구분력이 거의 사라진다** (30회 반복 측정 중 여러 번, 잡음의 측정값이 진짜 진동 신호의 측정값과 비슷하거나 역전됨).

해결: 사전 필터는 **넓게**(`wander_prefilter_low=0.05Hz` ~ `wander_prefilter_high=5.0Hz`) 걸어서 부반송파 선택/합산까지는 기존과 동일하게 하되, Welch **에너지 적분 구간만** 사용자가 요청한 좁은 대역(`wander_bandpass_low=0.1Hz` ~ `wander_bandpass_high=0.5Hz`)으로 잡는다. 이러면 진짜 0.1~0.5Hz 진동은 넓은 대역(0.05~5Hz) 안에서도 여전히 그 좁은 구간에 에너지가 집중되어 있어 높은 값이 나오고, 광대역 잡음은 0.05~5Hz 전체에 퍼져있어 그 중 0.1~0.5Hz 조각(전체 폭의 약 8%)만 측정되므로 값이 작게 나온다 — 30회 반복 시 비율(osc/noise)의 최솟값이 3.46까지 안정적으로 확보됨 (수정 전엔 1.00까지 떨어짐, 즉 구분 불가).

### 왜 Wander는 별도의 (더 긴) 윈도우를 쓰는가

Wander의 저역 통과 하한(사전 필터 기준 0.05Hz)까지 고려하면 한 주기가 20초에 달할 수 있다. 기존 MV용 3초 윈도우로는 `sosfiltfilt`(zero-phase 필터)가 안정적으로 수렴할 만큼의 주기 수를 확보할 수 없어 신호가 왜곡된다. 그래서 `wander_window_sec=6.0`초로 별도 윈도우를 `monitor.get_window()`에서 다시 가져온다. 이 6초 윈도우는 **라이브 감지에서만** 쓰이고 변경하지 않았다 (응답성 우선, §7 참고) — 온보딩 캘리브레이션의 baseline 캡처는 별도로 더 긴 20초를 쓴다 (§5).

매 틱(`stride_sec`, 기본 0.5초)마다 이 두 신호 계산이 모두 실행되므로, 파이프라인 비용이 기존 대비 대략 2배가 된다 — `pipeline_latency_ms`로 실측 확인 가능.

---

## 3. 재실 상태머신 (`src/presence_state_machine.py`)

`FallDetector`와 동일한 구조(임계값을 인스턴스 속성으로 보관, `update()`가 매 틱 호출)를 따르는 타임아웃 기반 상태머신이다. **Wander 쪽에는 `FallDetector`의 SUSPECT 단계와 같은 사상의 최소 지속시간(debounce)이 있다** — 이유는 §7 참고.

```python
class PresenceState(Enum):
    PRESENT = "present"
    ABSENT = "absent"

class PresenceDetector:
    def __init__(self, mv_threshold=2.5, wander_baseline=0.5, wander_ratio_threshold=2.0,
                 wander_min_duration_s=2.0, presence_timeout_s=6.0):
        self.state = PresenceState.ABSENT   # 부팅 기본값: 아직 아무 활동도 관측 안 됨
        self.last_activity_at = None
        self._wander_confirm_start_s = None

    def update(self, now_s, mv_value, wander_value):
        wander_ratio = wander_value / self.wander_baseline if self.wander_baseline > 1e-8 else 0.0
        raw_wander_active = wander_ratio >= self.wander_ratio_threshold

        if raw_wander_active:
            if self._wander_confirm_start_s is None:
                self._wander_confirm_start_s = now_s
            wander_confirmed = (now_s - self._wander_confirm_start_s) >= self.wander_min_duration_s
        else:
            self._wander_confirm_start_s = None   # 한 틱이라도 끊기면 리셋 (merge_gap 없음)
            wander_confirmed = False

        if mv_value >= self.mv_threshold or wander_confirmed:
            self.last_activity_at = now_s          # 활동 시각 갱신

        active = (self.last_activity_at is not None
                  and (now_s - self.last_activity_at) < self.presence_timeout_s)
        self.state = PresenceState.PRESENT if active else PresenceState.ABSENT
        ...
```

핵심 규칙은 여전히 **"마지막 활동으로부터 `presence_timeout_s`초 이내면 PRESENT, 아니면 ABSENT"** 하나다. 다만 "활동"으로 인정되는 조건이 신호별로 다르다:

- **MV**는 `mv_value >= mv_threshold`인 순간 즉시 활동으로 인정된다 (디바운스 없음) — 이동분산 자체가 이미 `mv_window_sec`(0.5초) 윈도우로 스무딩된 시간영역 지표라 순간적인 단일 틱 스파이크에 상대적으로 덜 취약하다.
- **Wander**는 `wander_ratio >= wander_ratio_threshold`가 **`wander_min_duration_s`(기본 2.0초) 동안 끊김 없이 지속**돼야 비로소 활동으로 인정된다(`wander_confirmed=True`). 한 틱이라도 비율이 임계값 아래로 떨어지면 카운트가 즉시 리셋되고 처음부터 다시 지속시간을 채워야 한다 (`FallDetector`의 `merge_gap_s` 같은 완충 구간은 없음 — 필요 이상으로 복잡하게 만들지 않기 위한 의도적 단순화).

- `mv_threshold`는 낙상 감지의 `FallDetector`와 **같은 값을 공유**한다 (`PipelineConfig.mv_threshold` 하나뿐, 별도의 재실용 MV 임계값은 없음).
- `wander_baseline`은 온보딩 캘리브레이션으로 측정한, 빈 공간의 Welch PSD 에너지(§5) — 절대값이 아니라 비교 기준값이다.
- `wander_ratio_threshold`(기본 2.0)는 `wander_current / wander_baseline`이 몇 배를 넘어야 WANDER로 판정할지 결정하는 배수.
- `wander_min_duration_s`(기본 2.0초)는 위에서 설명한 wander 디바운스 지속시간.
- `wander_baseline`이 0에 가까우면(`≤1e-8`) `ZeroDivisionError` 대신 `wander_ratio=0.0`으로 안전하게 처리한다.
- `just_changed` 플래그는 상태가 이번 틱에 실제로 전환됐을 때만 `True` — 프런트엔드 이벤트 로그가 이 플래그를 보고 "재실 감지"/"퇴실 감지" 로그를 한 번만 남긴다 (§6 참고).

매 틱 `DetectionPipeline.run_once()`가 `presence_detector.update(now_s, mv_current, wander_current)`를 호출하고, 결과(`PresenceStatus`)를 `DetectionOutput`의 `presence_state`/`wander_current`/`wander_baseline`/`wander_ratio_threshold`/`wander_ratio`/`wander_confirmed`/`last_activity_at`/`presence_just_changed` 필드에 담아 반환한다.

---

## 4. 설정 파라미터 (`PipelineConfig`, `src/pipeline.py`)

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `mv_threshold` | **2.0** | MV 임계값 (낙상 감지와 공유). **2026-07-14부터 잠정적으로 민감하게 설정**(이전 2.5) — 오탐 증가를 감수하고 감지가 잘 되는지부터 확인하기 위한 임시값, 추후 실사용 데이터로 재조정 필요 |
| `wander_window_sec` | 6.0 | Wander 신호용 슬라이딩 윈도우 (초, 라이브 감지 전용) |
| `wander_mv_window_sec` | 1.0 | 부반송파 선택 단계의 이동분산 윈도우 (초) → `wander_omega` 파생, PSD 계산과 무관 |
| `wander_prefilter_low` | 0.05 | Wander **사전 필터** 하한 (Hz) — 부반송파 선택/합산 이전 단계 |
| `wander_prefilter_high` | 5.0 | Wander 사전 필터 상한 (Hz) |
| `wander_bandpass_low` | 0.1 | Wander **Welch 에너지 측정** 대역 하한 (Hz) |
| `wander_bandpass_high` | 0.5 | Wander Welch 에너지 측정 대역 상한 (Hz) |
| `wander_baseline` | 0.5 | 온보딩 캘리브레이션으로 측정한 baseline Welch PSD 에너지 |
| `wander_ratio_threshold` | **1.8** | `wander_current / wander_baseline` 트리거 배수. **2026-07-14부터 잠정적으로 민감하게 설정**(이전 2.0) — `mv_threshold`와 같은 이유로 임시 하향 |
| `wander_min_duration_s` | 2.0 | wander 비율이 임계값 이상으로 끊김 없이 지속돼야 하는 최소 시간 (초) — 순간적 노이즈 스파이크 방지 |
| `presence_timeout_s` | 6.0 | 활동 없을 시 ABSENT 전환까지 대기 시간 (초) |

`GET/POST /detection/config`로 실시간 조회·변경 가능 (기존 파라미터와 동일한 partial-update 패턴). 전체 파라미터 표는 [`SPEC.md`](./SPEC.md) §7.1 참고.

---

## 5. 온보딩 캘리브레이션과의 연동 (`src/onboarding.py`)

`mv_threshold`/`wander_baseline`을 수동으로 튜닝하지 않고, 온보딩 3단계(초기 캘리브레이션)에서 **빈 공간의 기준 잡음(baseline noise floor)** 을 측정해 자동으로 설정한다. `run_calibration()`이 전체 흐름을 담당하며, `state.calibration.phase`를 갱신해 `GET /onboarding/calibrate/status`로 진행 상황을 폴링할 수 있게 한다.

```
POST /onboarding/calibrate/start
        │
        ▼
[leaving]       (2026-07-14 추가) train 명령을 보내기 전, 설치자가 공간을
                벗어날 시간을 준다 — leave_wait_s=10.0초 동안 아무 것도
                하지 않고 대기만 한다 (monitor.send_line도 아직 호출 안 됨).
                이전에는 이 단계가 없어 "캘리브레이션 시작" 버튼을 누르면
                곧바로 측정이 시작돼, 설치자가 나갈 시간이 없었다.
        │
        ▼
[waiting_ack]   monitor.send_line("train") 전송 →
                직전까지 흐르던 프레임이 완전히 멈출 때까지 대기
                (silence_confirm_s=0.2초 동안 packet_count 변화 없음 확인,
                 silence_timeout_s=3.0초 내에 안 멈추면 error)
        │
        ▼
[waiting_agc]   펌웨어가 CSI_PHASE_TRAINING(AGC 보정, ~1초) 를 마치고
                CSI_PHASE_STREAMING 으로 전환 → packet_count가 다시
                증가하기 시작할 때까지 대기 (resume_timeout_s=20.0초 타임아웃).
                재개 시점에 calib.agc_duration_s = 이 단계에 실제로 걸린
                시간을 기록한다 (아래 "관측성" 참고).
        │
        ▼
[measuring]     스트리밍 재개 시점부터 baseline_window_s=20.0초 대기 후
                monitor.get_window(20.0)으로 그 구간의 CSI를 캡처
                (학습 중엔 프레임이 전혀 안 오므로, 20초 윈도우는
                 반드시 재개 이후 데이터만 포함 — 별도 필터링 불필요.
                 캘리브레이션은 1회성이라 응답성 제약이 없어, 라이브
                 감지의 6초 윈도우보다 훨씬 긴 20초로 더 안정적인
                 baseline 추정치를 얻는다)
        │
        ▼
                _compute_baseline_thresholds(): 캡처한 20초 구간에 대해
                MV/Wander 신호 체인을 각각 실행.

                MV: moving_variance 배열 전체의 평균/표준편차로 임계값 도출
                    mv_threshold = mean(moving_variance) + k_mv * std(moving_variance)
                    (k_mv=2.0 — 2026-07-14부터 잠정적으로 민감하게 설정, 이전 4.0.
                     mv_floor=0.3로 하한 클램프)

                Wander: Welch PSD 에너지는 윈도우당 스칼라 1개뿐이라
                    시계열 평균/표준편차를 낼 수 없음 — 그 값 자체를 그대로 사용
                    wander_baseline = max(band_energy, wander_baseline_floor)
                    (wander_baseline_floor=0.05로 하한 클램프, 0 나눗셈 방지)

                → cfg.mv_threshold, cfg.wander_baseline에 직접 기록
                  (POST /detection/config가 조작하는 것과 동일한 공유 인스턴스)
        │
        ▼
[done]          calib.mv_threshold / calib.wander_baseline 확정
```

총 소요시간은 약 31초(퇴실대기 10 + ack ~0.2 + AGC ~1 + baseline 20)다.

**전제 조건**: `leaving` 단계가 끝난 뒤부터(즉 `train` 전송 시점부터) 완료까지 **공간이 비어있는 상태**를 유지해야 한다. `wander_baseline`은 "정지한 재실자의 잔향 신호"를 감지하기 위한 비교 기준값인데, 캘리브레이션 도중 사람이 있으면 그 사람의 호흡 자체가 baseline(잡음)으로 흡수되어 실제 재실자를 감지하지 못하게 된다. 이 전제는 `leaving` 단계로 어느 정도 강제되지만, 설치자가 10초 안에 실제로 공간을 벗어난다는 보장은 UI 안내 문구에 의존한다.

**단계별 진행 상황 관측성** (2026-07-14 추가): 기존엔 `GET /onboarding/calibrate/status`가 캘리브레이션 시작부터의 누적 `elapsed_s` 하나만 보여줘서, 지금 단계가 몇 초째인지, AGC가 기대한 ~1초 근처로 끝났는지 알 수 없었다. `CalibrationState.phase_started_at`을 매 단계 전환마다 갱신해, `phase_elapsed_s`(현재 단계 경과시간)를 별도로 노출한다. `waiting_agc`가 끝나는 시점에 그 단계가 실제로 걸린 시간을 `agc_duration_s`에 기록해 함께 노출한다 — **다만 이건 "타이밍이 정상 범위로 보이는가"를 보여주는 관측성 개선일 뿐, 펌웨어의 AGC 보정 자체가 성공했는지에 대한 진짜 검증은 아니다.** 펌웨어는 AGC 성공 여부를 프로토콜로 보고할 방법이 없다(`ESP_LOGI`로만 로그를 남기며, 이건 바이너리 CSI 프로토콜과 무관한 별도 UART 텍스트라 호스트가 파싱할 수 없음 — CLAUDE.md/SPEC.md에 기록된 기존 제약). `agc_duration_s`가 극단적으로 짧거나(예: 0.05초) `waiting_agc`가 `resume_timeout_s`로 실패하면 뭔가 잘못됐다는 간접 신호는 되지만, 정상 범위(~1초 근처)로 나온다고 AGC 품질 자체를 보장하진 않는다.

**타이밍 마진** (2026-07-14 수정): 펌웨어의 AGC 보정 창(`CSI_TRAIN_DURATION_US`, 현재 `csi_recv_calibrate/main/app_main.c`에서 **~1초**)과 `silence_confirm_s` 사이에 안전 마진이 필요하다 — `silence_confirm_s`가 AGC 창보다 크거나 근접하면, `waiting_ack` 단계가 침묵을 충분히 확인하기 전에 스트리밍이 재개되어 타임아웃(`silence_timeout_s`)으로 간헐적으로 실패할 수 있다. 이전 기본값(0.7초)은 마진이 0.3초로 빠듯했다. 현재는 `silence_confirm_s=0.2초`로 낮춰 **~0.8초의 마진**을 확보했다 — 921600 baud에서 CSI 프레임 1개(~650바이트) 드레인 시간이 ~7ms 수준이므로 0.2초는 in-flight 바이트 드레인 확인에 여전히 충분히 여유 있다. `poll_interval_s`도 0.1→0.05초로 낮춰 위상 전환 감지 해상도를 높였다. 다만 이 마진이 실제 WiFi/UART 지터 환경에서도 충분한지는 시뮬레이션만으로 완전히 보장되지 않으므로, 실 하드웨어에서 온보딩 캘리브레이션을 여러 번 반복해 `waiting_ack` 실패율을 확인하는 것을 권장한다.

---

## 6. API·프런트엔드 연동

- **`DetectionInfo`** (WS `/ws/live`, 10Hz push): `presence_state`, `wander_current`, `wander_baseline`, `wander_ratio_threshold`, `wander_ratio`, `last_activity_at`, `presence_just_changed` 필드 포함 (`API.md` 참고).
- **`static/index.html`**: `#fallStatusPanel`에 `#presenceBadge` (재실/퇴실, `.badge.present`/`.badge.absent`)를 렌더링. `ws.onmessage`에서 `lastPresenceState`와 비교해 변화가 있을 때만 `logEvent("재실 감지" | "퇴실 감지")`를 남긴다 — 서버 측 이벤트 로그는 없고 전적으로 클라이언트 상태 비교로 판단한다 (낙상 이벤트 로깅과 동일한 패턴).
- **온보딩 위저드**: 3단계(`onbStep3`)가 `POST /onboarding/calibrate/start` → `GET /onboarding/calibrate/status` 폴링(1초 간격) → 완료 시 계산된 `mv_threshold`/`wander_baseline` 표시.

---

## 7. 알려진 제약

- **영속성 없음**: `OnboardingState`/`CalibrationState`는 인메모리 전용이며 백엔드 재시작 시 초기화된다 (`onboarded=false`로 리셋, 재실 상태머신도 `ABSENT`로 리셋). 기존 `PipelineConfig`/낙상 기록과 동일한 설계.
- **MV 임계값 공유**: 재실 감지와 낙상 감지가 동일한 `mv_threshold`를 사용하므로, 낙상 감지 민감도를 조정하면 재실 감지의 MOVE 판정 민감도도 함께 바뀐다. 별도 분리는 현재 지원하지 않는다.
- **라이브 감지의 주파수 분해능은 거칠다 — debounce로 완화 (2026-07-14)**: `wander_window_sec=6.0`초(600샘플 @ 100Hz)로 Welch PSD를 단일 세그먼트(`nperseg=len(signal)`, 평균화 없음) 계산하면 `Δf = fs_hz/nperseg ≈ 0.167Hz` — 0.4Hz 폭인 0.1~0.5Hz 측정 대역 안에 빈(bin)이 2~3개뿐이다. 응답성을 우선한 의도적 트레이드오프이며, 온보딩 캘리브레이션은 20초의 더 긴 캡처로 더 안정적인 baseline을 얻는다 (§5). 다중 세그먼트 평균화(분산 감소)는 적용하지 않는다.

  분해능 자체는 그대로지만, 순간적인 저주파 노이즈(문 여닫힘의 기압 변화, 바람 등)가 몇 개 안 되는 빈에 몰려 `wander_ratio`가 튀는 문제는 `wander_min_duration_s`(기본 2.0초) debounce로 완화했다(§3) — 비율이 임계값을 넘어도 그 상태가 끊김 없이 2초 이상 지속돼야 활동으로 인정되므로, 1초 미만의 순간적 스파이크는 걸러진다. **근본적으로 PSD 추정 자체의 분산이 줄어드는 것은 아니다** — 지속시간 요구조건으로 "일회성 튐"과 "실제 지속 신호"를 구분하는 완화책일 뿐이므로, 스파이크가 우연히 `wander_min_duration_s`보다 길게 이어지면 여전히 오탐할 수 있다. 윈도우 크기나 세그먼트 수 변경(더 근본적인 해법)은 의도적으로 보류했다 — 응답성 우선 설계를 유지하기 위함.
- **사전 필터와 측정 대역을 분리해야 하는 이유**를 몰랐다면 재현하기 쉬운 회귀 — 향후 `wander_prefilter_low/high`를 `wander_bandpass_low/high`와 같게(또는 더 좁게) 만들면 §2에서 설명한 구분력 상실 문제가 재발한다. 반드시 `wander_prefilter_*`가 `wander_bandpass_*`보다 넓은 대역을 유지해야 한다.
- **성능**: 매 틱 신호 체인이 2회 실행되므로 `pipeline_latency_ms`가 기존 대비 상승한다. `stride_sec`(기본 0.5초) 대비 여유가 줄면 오버런 틱이 조용히 드롭될 수 있음 (`detection_loop`의 `asyncio.wait_for` 타임아웃).
