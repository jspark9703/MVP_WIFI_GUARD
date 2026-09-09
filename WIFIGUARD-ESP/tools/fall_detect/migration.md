# 온보딩 + 재실감지 로직 이식 가이드

> **대상 독자**: 낙상 감지는 이미 딥러닝(DL) 모델로 구현돼 있지만, 온보딩 캘리브레이션과 재실감지(움직임/Wander 감지) 기능은 없는 다른 프로젝트에 이 두 기능을 이식하려는 개발자.
> **전제 확인됨**: 이식 대상의 CSI 수신 계층은 이 저장소의 `EspMonitor`(바이너리 프레임 프로토콜, magic `0xA55A`)와 동일/유사하고, ESP32 펌웨어도 이미 `"train"` 유사 캘리브레이션 시리얼 명령을 지원한다. 즉 **데이터 소스/프로토콜 레이어는 대부분 호환**되며, 이식의 본체는 **신호처리 로직**과 **재실감지를 낙상 DL 모델과 독립적으로 동작시키는 통합 방식**이다.

---

## 1. 개요/범위

**이식 대상**:
- 온보딩 캘리브레이션: 4단계 위저드(서비스 안내 → 장치 등록 → 초기 캘리브레이션 → 완료) + 백엔드 `run_calibration()` 오케스트레이션
- 재실감지: MV(이동분산) + Wander(저주파 PSD 에너지) 두 신호를 결합한 `PresenceDetector` 상태머신

**이식 제외 대상**:
- `src/fall_state_machine.py`(`FallDetector`, `FallState`) — 낙상 상태머신은 이식하지 않는다. 이식 대상엔 이미 DL 기반 낙상감지가 있으므로 불필요.
- 낙상 전용 REST 필드(`DetectionInfo`의 `state`/`just_triggered`/`last_fall_at` 등)

**핵심 통찰 (가장 중요)**: 이 저장소에서 재실감지의 MV 신호는 낙상감지와 신호 계산을 **공유**한다 — `pipeline.py`의 `run_once()`가 `compute_final_signal()`을 한 번 호출해 그 결과(`mv_current`)를 `FallDetector`와 `PresenceDetector` 양쪽에 넘긴다. 하지만 이식 대상은 낙상감지가 **DL 모델**이라 이 고전적 MV 계산을 이미 하고 있지 않을 가능성이 높다.

**따라서 이식 시 재실감지는 DL 모델과 완전히 독립적으로, 자체적으로 다음 두 계산을 직접 수행해야 한다**:
1. MV 계산: `compute_final_signal()` 1회 (`bandpass_low`/`bandpass_high` 대역)
2. Wander 계산: `compute_final_signal()` 1회 더 (이중 대역 — 아래 §4 참고)

이 둘은 DL 모델의 입력/출력과 무관한 **별도의 병렬 계산 경로**다. DL 모델을 건드리거나 그 출력을 재사용하려 하지 말 것 — 처음부터 새로 계산해야 한다.

---

## 2. 의존성 맵

```
PresenceDetector (src/presence_state_machine.py)
  └─ 입력: mv_value, wander_value (스칼라, 매 틱 외부에서 계산해 전달)
       상태: PRESENT / ABSENT, 순수 로직, 외부 의존성 없음(dataclass/enum만 사용)

mv_value, wander_value 계산 — pipeline.py의 run_once()가 레퍼런스 구현 (§4)
  └─ compute_final_signal()  ×2  (src/streaming_features.py)
       ├─ resample_signal, bandpass_filter   (src/preprocessing.py)
       ├─ select_top_subcarriers_2d, sum_and_normalize, _moving_variance
       └─ compute_band_energy_welch()  (Wander 전용, Welch PSD 밴드 에너지)

run_calibration() (src/onboarding.py)  ── 온보딩 3단계(캘리브레이션)를 구동
  ├─ compute_final_signal() 재사용 (baseline 구간에 대해 MV/Wander 각각 1회)
  ├─ monitor 덕타이핑 계약: .running / .packet_count / .send_line() / .get_window()
  │    → 이식 대상의 EspMonitor 동급 클래스가 이 4개만 제공하면 그대로 동작
  └─ PipelineConfig 동급 dataclass 필요 (필드 목록은 §5)
```

**의존성 방향**: `PresenceDetector`는 아무것도 몰라도 된다(순수 함수형 상태머신). `streaming_features.py`/`preprocessing.py`는 numpy/scipy만 의존하는 순수 신호처리 유틸리티다. `onboarding.py`가 이 둘을 조합해 캘리브레이션 흐름을 만든다. 이 순서대로 이식하면 각 단계에서 독립적으로 단위 테스트가 가능하다(§9 체크리스트 참고).

---

## 3. 파일별 레퍼런스

| 파일 | 역할 | 이식 방식 |
|---|---|---|
| `src/presence_state_machine.py` | `PresenceDetector`, `PresenceState`, `PresenceStatus` | 거의 그대로 복사 — 외부 의존성 없음 |
| `src/streaming_features.py` | `compute_final_signal()`, `compute_band_energy_welch()`, `select_top_subcarriers_2d`, `safe_bandpass`, `sum_and_normalize` | 거의 그대로 복사 — numpy/scipy만 의존 |
| `src/preprocessing.py` | `_moving_variance`, `_compute_q`, `resample_signal`, `bandpass_filter` | `streaming_features.py`의 의존성이므로 함께 복사 |
| `src/onboarding.py` | `run_calibration()`, `derive_threshold()`, `_compute_baseline_thresholds()`, `OnboardingState`/`CalibrationState` | 거의 그대로 복사 — `from .pipeline import PipelineConfig` 임포트만 대상의 설정 객체로 교체 |
| `main.py`의 `EspMonitor.send_line()` (398행) | 시리얼 쓰기 경로 (`"train"` 명령 전송용) | 대상 monitor 클래스에 쓰기 경로가 없다면 그대로 참고해 추가 |
| `main.py`의 온보딩 Pydantic 모델 (140-165행: `OnboardingStatus`, `ServiceRequest`, `DeviceRequest`, `CalibrationStatusResponse`) 및 라우트 (709-780행: `/onboarding/*` 6개 엔드포인트) | REST API 계층 | FastAPI면 거의 그대로, 다른 프레임워크면 스키마/로직만 참고해 재구현 |
| `static/index.html`의 재실 배지 (523행) + 온보딩 위저드 (661-732행 HTML, 1139-1317행 JS) | 프런트엔드 | UI 스택에 맞게 재구현, 문구/흐름 로직은 그대로 참고 |
| `csi_recv_calibrate/main/app_main.c` (80-573행) | 펌웨어 `train` 프로토콜 레퍼런스 | 이미 유사 지원 확인됨 — §6 체크리스트로 세부 검증만 |

---

## 4. 핵심 통합 지점: `run_once()`에서 재실감지 부분만 추출

`src/pipeline.py`의 `DetectionPipeline.run_once()`(130-196행)가 매 틱 실행하는 전체 로직 중, **낙상 관련 줄을 제외**하고 재실감지에 필요한 부분만 추출하면 다음과 같다 (괄호 안은 원본 줄 번호):

```python
# 1) MV 신호 계산 (150-161행) — DL 모델과 무관하게 새로 계산
result = compute_final_signal(
    timestamps_us, amp_2d,
    window_sec=cfg.window_sec, stride_sec=cfg.stride_sec, fs_hz=cfg.fs_hz,
    omega=cfg.omega, n_streams=cfg.n_streams,
    bandpass_low=cfg.bandpass_low, bandpass_high=cfg.bandpass_high,
    bandpass_order=cfg.bandpass_order,
)
if result is None:
    return None

# ※ 낙상 관련 줄(원본 115-121, 167행: FallDetector 생성/update)은 제외 — DL 모델이 대신함

# 2) Wander 신호 계산 (175-194행) — 별도 윈도우 + 이중 대역
now_s = time.time()
wander_current = 0.0
wander_window = monitor.get_window(cfg.wander_window_sec)
if wander_window is not None and len(wander_window[0]) >= 30:
    w_ts, w_amp = wander_window
    wander_result = compute_final_signal(
        w_ts, w_amp,
        window_sec=cfg.wander_window_sec, stride_sec=cfg.stride_sec, fs_hz=cfg.fs_hz,
        omega=cfg.wander_omega, n_streams=cfg.n_streams,
        bandpass_low=cfg.wander_prefilter_low, bandpass_high=cfg.wander_prefilter_high,
        bandpass_order=cfg.bandpass_order,
        compute_band_energy=True,
        energy_band_low=cfg.wander_bandpass_low, energy_band_high=cfg.wander_bandpass_high,
    )
    if wander_result is not None and wander_result.band_energy is not None:
        wander_current = wander_result.band_energy

# 3) 재실 상태 갱신 (196행)
presence_status = presence_detector.update(now_s, result.mv_current, wander_current)
```

이 블록을 **대상 프로젝트의 매 틱 루프(DL 추론이 도는 바로 그 자리)에 병렬로 삽입**하는 것이 이식의 본체다. `monitor.get_window(...)`는 이식 대상의 CSI 링버퍼에서 트레일링 윈도우를 가져오는 동급 메서드로 교체(§5의 계약 참고).

**Wander가 이중 대역을 쓰는 이유** (그대로 유지할 것 — `wander_prefilter_low/high`를 `wander_bandpass_low/high`와 같게 만들면 회귀가 재발함, `occupation_pipline.md` §2/§7 참고): 사전 필터(`wander_prefilter_*`, 넓은 0.05~5Hz)로 부반송파 선택/합산까지 처리한 뒤, Welch 에너지 적분만 좁은 측정 대역(`wander_bandpass_*`, 0.1~0.5Hz)으로 잡는다. 두 대역을 같게 하면 사전 필터가 이미 신호를 그 대역에 가둬버려 정규화 후 측정값이 입력과 무관하게 거의 일정해지는 문제가 있었다(실측으로 확인된 회귀).

---

## 5. 필요한 설정 파라미터

`PipelineConfig`(`src/pipeline.py:18-72`)의 전체 필드 중, 재실감지/온보딩이 실제로 쓰는 것만 추림. 대상 프로젝트에 이미 설정 객체가 있다면 이 필드들만 추가하면 된다 (기본값은 이 저장소의 현재 값, 2026-07-14 기준 잠정적으로 민감하게 튜닝됨 — `occupation_pipline.md` §7 참고):

| 필드 | 기본값 | 용도 |
|---|---|---|
| `window_sec` | 3.0 | MV 신호 슬라이딩 윈도우 (초) |
| `stride_sec` | 0.5 | 틱 주기 (초) |
| `fs_hz` | 100.0 | 리샘플링 목표 주파수 (Hz) |
| `n_streams` | 10 | 선택할 부반송파 개수 |
| `bandpass_low` / `bandpass_high` | 0.5 / 50.0 | MV 대역통과 필터 (Hz) |
| `bandpass_order` | 4 | Butterworth 필터 차수 |
| `mv_threshold` | 2.0 | MV 임계값 (재실감지 전용 — 이식 시 DL 낙상감지와 공유할 필요 없음) |
| `wander_window_sec` | 6.0 | Wander 신호 슬라이딩 윈도우 (초) |
| `wander_mv_window_sec` | 1.0 | → `wander_omega` 파생용 |
| `wander_prefilter_low` / `wander_prefilter_high` | 0.05 / 5.0 | Wander 사전 필터 (넓은 대역) |
| `wander_bandpass_low` / `wander_bandpass_high` | 0.1 / 0.5 | Wander Welch 에너지 측정 대역 (좁은 대역) |
| `wander_baseline` | 0.5 | 온보딩 캘리브레이션으로 갱신되는 baseline PSD 에너지 |
| `wander_ratio_threshold` | 1.8 | `wander_current / wander_baseline` 트리거 배수 |
| `wander_min_duration_s` | 2.0 | Wander 순간 스파이크 방지 디바운스 (초) |
| `presence_timeout_s` | 6.0 | 활동 없을 시 ABSENT 전환 대기 시간 (초) |

또한 `PipelineConfig`에는 `omega`/`wander_omega` **프로퍼티**가 있다(`pipeline.py:64-72`) — `mv_window_sec`/`wander_mv_window_sec`에서 파생되는 계산값이므로 대상 설정 객체에도 동일하게 프로퍼티(또는 계산 함수)로 재현할 것:
```python
omega = max(1, round((mv_window_sec * fs_hz - 1) / 2))
wander_omega = max(1, round((wander_mv_window_sec * fs_hz - 1) / 2))
```

**monitor 덕타이핑 계약** (이식 대상이 이미 호환된다고 확인됨, 그래도 명시): `run_calibration()`과 위 §4 블록이 필요로 하는 건 다음 4개뿐이다.
- `.running: bool`
- `.packet_count: int`
- `.send_line(text: str) -> bool`
- `.get_window(window_sec: float) -> tuple[np.ndarray, np.ndarray] | None` — `(timestamps_us, amp_2d)` 반환, 부족하면 `None`

---

## 6. 펌웨어 프로토콜 검증 체크리스트

"유사함"이 확인됐지만 "동일함"까지 확인된 건 아니므로, 이식 전 다음을 확인할 것 (레퍼런스: `csi_recv_calibrate/main/app_main.c` 80-573행):

- [ ] 캘리브레이션 명령 문자열이 정확히 `"train"`인지 (`CSI_TRAIN_COMMAND`, 85행)
- [ ] 상태 전이가 `CSI_PHASE_IDLE → CSI_PHASE_TRAINING → CSI_PHASE_STREAMING` 동일한 시맨틱인지 — 특히 **TRAINING 중엔 CSI 프레임을 전혀 전송하지 않는지**(`onboarding.py`의 `waiting_ack`/`waiting_agc` 단계가 이 "침묵"을 감지해 진행 상황을 판단하므로 핵심 전제)
- [ ] AGC 보정 창의 실제 길이 — 이 저장소는 `CSI_TRAIN_DURATION_US=1000000LL`(~1초)로 튜닝됨. 대상 펌웨어가 다르면 `run_calibration()`의 다음 타이밍 상수들을 재튜닝해야 함:
  - `leave_wait_s`(기본 10.0초, AGC 창과 무관 — 설치자 퇴실 대기)
  - `silence_confirm_s`(기본 0.2초 — AGC 창보다 충분히 짧아야 함, `occupation_pipline.md` §5 "타이밍 마진" 참고)
  - `resume_timeout_s`(기본 20.0초 — AGC 창보다 충분히 긴 안전 타임아웃)
  - `baseline_window_s`(기본 20.0초 — AGC 창과 무관, baseline 측정 길이)

---

## 7. API 레이어 요약

전체 요청/응답 스키마는 `API.md`의 `## Onboarding` 섹션 참고. 엔드포인트 목록:

| 메서드/경로 | 역할 |
|---|---|
| `GET /onboarding/status` | 온보딩 진행 상태 조회 |
| `POST /onboarding/service` | 1단계: 서비스 유형 저장 |
| `POST /onboarding/device` | 2단계: 장치명/공간명 저장 (사전에 `/monitor/start`류 연결 필요) |
| `POST /onboarding/calibrate/start` | 3단계: `run_calibration()`을 백그라운드 태스크로 시작 |
| `GET /onboarding/calibrate/status` | 3단계 진행 상황 폴링 (phase, phase_elapsed_s, agc_duration_s 등) |
| `POST /onboarding/complete` | 4단계: `onboarded=true` 확정 |

`DetectionInfo`(WS로 매 틱 push)에는 재실감지 관련 필드가 추가돼 있다: `presence_state`, `wander_current`, `wander_baseline`, `wander_ratio_threshold`, `wander_ratio`, `wander_confirmed`, `last_activity_at`, `presence_just_changed` — 이식 대상의 실시간 상태 push 채널(WS든 폴링이든)에 동일하게 추가할 것.

---

## 8. 프런트엔드 요약

`static/index.html`의 온보딩 위저드(661-732행 HTML, 1139-1317행 JS)는 4단계 풀스크린 모달로 구현돼 있다:
1. **서비스 안내**: 서비스 유형 선택(드롭다운) + 안내 문구
2. **장치 등록**: 포트 입력 → 연결(`/monitor/start` 재사용) → 장치명/공간명 입력 → `/onboarding/device`
3. **초기 캘리브레이션**: "캘리브레이션 시작" 클릭 → `/onboarding/calibrate/start` → 1초 간격으로 `/onboarding/calibrate/status` 폴링 → phase별 라벨 표시(퇴실 대기 카운트다운 포함) → 완료 시 `mv_threshold`/`wander_baseline` 표시
4. **완료**: `/onboarding/complete` → 모달 닫고 대시보드 진입

재실 배지(523행)는 `.badge.present`/`.badge.absent` 클래스로 재실/퇴실을 표시하며, WS 메시지의 `presence_state` 변화를 감지해 이벤트 로그에 "재실 감지"/"퇴실 감지"를 남긴다(서버 측 로그는 없음 — 순수 클라이언트 상태 비교).

UI 스택이 다르면 이 구조(4단계 모달, 상태 폴링 기반 진행 표시, 재실 배지)만 재현하고 마크업/스타일은 대상 스택에 맞게 새로 작성할 것.

---

## 9. 이식 순서 체크리스트

1. `presence_state_machine.py` + `streaming_features.py` + `preprocessing.py` 복사, 단위 테스트(`tests/test_presence_state_machine.py`, `tests/test_pipeline.py`의 관련 테스트 참고)로 하드웨어 없이 독립 동작 확인
2. 대상 설정 객체에 §5 파라미터 추가 (`omega`/`wander_omega` 프로퍼티 포함)
3. 대상의 매 틱 루프(DL 낙상 추론과 병렬)에 §4 통합 지점 삽입
4. `onboarding.py` 복사, monitor 계약(§5) 확인, 없으면 `EspMonitor.send_line()` 이식
5. §6 체크리스트로 펌웨어 프로토콜 세부 검증 (특히 TRAINING 중 침묵 여부)
6. 온보딩 REST API 이식 (§7)
7. 프런트엔드 위저드 + 재실 배지 이식 (§8)
8. 실 하드웨어로 온보딩 전체 흐름 및 재실감지 동작 검증

---

## 10. 참고 문서

- [`occupation_pipline.md`](./occupation_pipline.md) — 재실감지 알고리즘 상세(신호 체인, 상태머신, 캘리브레이션 연동, 알려진 제약). 이식 전 필독.
- [`SPEC.md`](./SPEC.md) §7.1 — 전체 `PipelineConfig` 파라미터표(낙상감지 포함 전체).
- [`API.md`](./API.md) `## Onboarding` 섹션 — 온보딩 REST API 전체 스펙. `DetectionInfo` 섹션 — WS 필드 전체 스펙.
