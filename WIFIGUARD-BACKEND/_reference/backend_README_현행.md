# CSI-Guard 로컬 백엔드

수신기(csi_recv) 시리얼 스트림을 받아 실시간 CSI 데이터와 낙상 판정을 제공하는 FastAPI 서버.
전 과정이 로컬(127.0.0.1)에서 동작한다. 작업 명세는 dcos/작업명세_로컬_실시간_낙상감지_v1.0.md 참조.

## 설치와 실행

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

수신기 포트는 자동 탐지된다(cu.usbmodem 첫 번째). 포트가 여러 개면 다음처럼 지정한다.

```bash
.venv/bin/python main.py --port /dev/cu.usbmodem5B7B0323351
```

모델 추론 관련 옵션:

```bash
.venv/bin/python main.py --no-model            # 추론 없이 수신/스트림만
.venv/bin/python main.py --device cpu          # 기본 auto (macOS는 MPS 우선)
.venv/bin/python main.py --threshold 0.468     # 낙상 판정 임계값
.venv/bin/python main.py --checkpoint path/to/best_model.pt
```

체크포인트 기본 경로는 저장소 루트의 Window3BestModelInference/weights/best_model.pt 이다.

## 푸시 알림 (단계 5, ntfy)

FALL 확정 시 ntfy 토픽으로 휴대폰 푸시 알림을 보낸다. 팀 합의로 ntfy.sh 공개 서버를 사용한다.

설정 순서:

1. 팀에서 정한 토픽명을 준비한다. 토픽명을 아는 누구나 구독과 발행이 가능하므로
   추측하기 어려운 이름을 쓴다 (예: csi-guard-fall-x7k2m9).
2. 휴대폰에 ntfy 앱(iOS/Android)을 설치하고 해당 토픽을 구독한다.
3. 백엔드를 토픽과 함께 실행한다.

```bash
.venv/bin/python main.py --ntfy-topic csi-guard-fall-x7k2m9
# 또는 환경변수: NTFY_TOPIC=csi-guard-fall-x7k2m9 .venv/bin/python main.py
# 셀프호스트 서버 사용 시: --ntfy-server https://ntfy.example.com
```

토픽을 지정하지 않으면 알림은 비활성 상태로 서버가 뜬다(/monitor/status의 notify에 표시).
경로 점검은 서버 실행 후 다음으로 한다. 수 초 내 휴대폰 알림이 오면 정상이다.

```bash
curl -X POST http://127.0.0.1:8000/notify/test
```

동작 방식: 발송은 전용 스레드와 큐로 처리해 0.25초 탐지 루프를 막지 않으며,
실패 시 1, 2, 4초 백오프로 최대 3회 재시도한다. FALL 확정당 1회만 발송되고
COOLDOWN(10초) 동안 재알람이 억제된다. 구현은 notifier.py, 훅은 detector.py의
FALL 확정 지점(on_fall 콜백)이다.

## 엔드포인트

| 경로 | 내용 |
|---|---|
| GET /monitor/status | 시리얼 연결 상태, 수신률(Hz), 체크섬 오류, 버퍼, 낙상 판정, 알림 발송 상태 |
| GET /monitor/window?seconds=3 | 최근 N초 윈도우 요약 (프레임 수, 진폭 통계) |
| GET /monitor/detect | 낙상 판정 상세 + 최근 60초 확률 히스토리 |
| POST /notify/test | ntfy 테스트 알림 발송 (알림 경로 수동 점검) |
| POST /onboarding/calibrate/start | 온보딩 3단계: 움직임/재실 캘리브레이션 시작 (leaving→waiting_ack→waiting_agc→measuring, 총 약 61초) |
| GET /onboarding/calibrate/status | 캘리브레이션 진행상황 폴링 (phase, phase_elapsed_s, agc_duration_s, presence_mv_threshold, wander_baseline) |
| WS /ws/live | 10Hz 실시간 요약 푸시 (수신률, RSSI, 진폭, 낙상 확률/상태, 재실 상태) |

## 움직임(MV) · 재실(Presence) 감지 (온보딩 캘리브레이션)

낙상 DL 추론과 **완전히 독립적인 스레드**(`presence_loop.py`)에서 도는 별도 신호
파이프라인이다(`presence/`, `onboarding.py`). reference/fall_detect(구 이동분산
임계값 기반 백엔드)에서 이식했다 — 자세한 배경은
`../reference/fall_detect/migration.md`, `../reference/fall_detect/occupation_pipline.md` 참고.

**`detector.py`(낙상 DL)와는 별도 스레드**로 `main.py`가 조건 없이 항상 기동한다
(`start_presence_loop()`) — `--no-model`이거나 체크포인트 로드에 실패해 `detector`가
아예 없어도, 시리얼 수신기가 CSI를 흘려보내는 한 이 루프는 계속 돈다. 처음
구현에서는 이 계산을 `FallDetector` 안에 얹어 두어서 `--no-model`일 때 재실/움직임
감지 전체가 죽는 결함이 있었다 — 지금은 분리되어 있다.

```
매 0.25초 틱 (presence_loop.py, 독립 스레드):
  MV 신호   : ring.get_window(3s)  -> compute_final_signal(...) -> mv_current
  Wander 신호: ring.get_window(6s) -> compute_final_signal(..., compute_band_energy=True) -> wander_current
  PresenceDetector.update(mv_current, wander_current) -> presence_state(PRESENT/ABSENT)
```

`/ws/live`는 `presence_loop`가 있으면(항상 있음) 그 페이로드를 먼저 병합하고, 그 위에
`detector`가 있으면(모델 로드 성공 시에만) 낙상 판정 필드를 덧붙인다 — 즉 `mv_current`/
`presence_state`는 모델 유무와 무관하게 항상 내려오고, `proba_fall`/`detect_state`만
모델이 있을 때 추가된다. `/monitor/status`의 `presence` 키로도 동일 상태를 폴링할 수 있다.

- `presence_mv_threshold`(이동분산 임계값)와 `wander_baseline`(조용한 방의 Welch PSD
  에너지 기준값)은 `POST /onboarding/calibrate/start`가 구동하는 캘리브레이션으로
  도출된다 — 낙상 판정에 쓰이는 DL 확률 임계값(`--threshold`, 기본 0.468)과는 이름도
  척도도 다른 완전히 별개의 값이다.
- 캘리브레이션은 `leave_wait_s`(30s, 설치자 퇴실 대기) → `"train"` 명령 전송 →
  `waiting_ack`(~0.2s, 스트림 무음 확인) → `waiting_agc`(~1s, 펌웨어 AGC 보정) →
  `measuring`(30s, baseline 캡처) 순으로 진행되며 총 약 61초 걸린다 — 압축하지 않고
  실제 소요시간 그대로 노출한다.
- `"train"` 명령은 `csi_recv_calibrate` 펌웨어가 이미 구현하고 있다고 가정한다
  (별도 구현/수정 없음 — `SerialReader.send_line()`은 명령을 그대로 전달할 뿐).

## 낙상 탐지 파이프라인 (0.25초 주기)

```
링버퍼 3초 윈도우 -> 균일 그리드 리샘플 (native Hz)
  -> 서브캐리어 30개 선택 -> PCA motion signal
  -> S3 scalogram (224,224) + PCA-ACF (1,128,64)
  -> DualBranchResNet 추론 (best_model.pt)
  -> 임계값 0.468 + 인과 다수결(최근 5윈도우) -> IDLE/SUSPECT/FALL/COOLDOWN
```

- 피처 코드는 ACF_Scalogram_FeatureExtraction(연구단 제공, gitignore)에서 이식했고,
  합성 입력에 대해 원본과 비트 단위 동일 출력을 확인했다.
- 검증에 쓰인 mode5는 중심 윈도우 기준(미래 2윈도우 필요)이라 실시간에서는
  인과 다수결로 대체했다. 연구단 권장안 확인 후 조정 여지 있음 (detector.py 참조).
- CWT scale 계산(freq_to_scale, 호출당 약 0.5초)은 fs와 윈도우 길이에 결정적이라
  캐시한다. 측정 fs를 0.25Hz 격자로 양자화해 캐시가 적중하게 한다.
- 실측 지연: 피처 약 27ms + 추론(MPS) 약 15ms = 윈도우당 약 42ms (스트라이드 250ms 이내).

벤치마크:

```bash
.venv/bin/python bench_pipeline.py --fs 166.67
```

## 구조

```
backend/
  main.py                FastAPI 앱, 엔드포인트, 탐지/재실 루프와 알림 기동
  detector.py            0.25초 주기 낙상 추론 루프, 상태머신, 인과 다수결 (DL 전용)
  presence_loop.py       움직임(MV)/재실 감지 독립 루프 (DL 모델과 무관, 항상 기동)
  notifier.py            ntfy 푸시 알림 발송 (전용 스레드, 재시도)
  onboarding.py          온보딩 3단계 캘리브레이션 오케스트레이션 (움직임/재실 baseline 도출)
  csi/protocol.py        바이너리 프레임 파서 (매직 0xA55A, 체크섬, 재동기화)
  csi/serial_reader.py   포트 탐지, 921600 연결, 자동 재연결 스레드, train 명령 전송
  csi/buffer.py          링버퍼 (30초), 타임스탬프 unwrap, 수신 품질 지표
  features/common.py     S3 scalogram 코어 (원본 amfall_losnlos_common.py 이식)
  features/acf.py        PCA-ACF 계산 (원본 build_losnlos_pca_motion_acf_dataset.py 이식)
  features/realtime.py   실시간 윈도우 -> 모델 입력 피처 래퍼
  inference/model.py     DualBranchResNet 정의 (체크포인트와 1:1, 구조 변경 금지)
  inference/engine.py    체크포인트 로드, 정규화, 단일 윈도우 추론
  presence/config.py     재실 감지 파라미터 (PresenceConfig, presence_mv_threshold/wander_*)
  presence/streaming_features.py  MV/Wander 신호체인 (reference/fall_detect 이식)
  presence/preprocessing.py       리샘플/대역통과/이동분산 저수준 함수 (동일 이식)
  presence/state_machine.py       PresenceDetector 상태머신 (PRESENT/ABSENT)
  bench_pipeline.py      파이프라인 지연 벤치마크
```

프레임 프로토콜 정의의 원본은 esp32c5/csi_recv/main/app_main.c 이다.
프로토콜이 바뀌면 csi/protocol.py 의 HEADER_FMT 를 함께 갱신해야 한다.
피처 파라미터(FeatureConfig)는 학습 설정과 일치해야 하며, 근거는
ACF_Scalogram_FeatureExtraction/README_KO.md 의 모델 호환 설정 표이다.
