# MVP_WIFI_GUARD — WIFI-GUARD 4-레포 분리 작업장

현행 단일 레포(`Guardian Angel Alert`: 로컬 FastAPI + TanStack 목업)를 **엣지 / 클라우드 / 프론트엔드 / 펌웨어** 4갈래로 쪼개기 위한 로컬 스테이징 디렉토리다.

> **이 디렉토리는 부모 레포에서 `.gitignore` 처리되어 있다.** 각 하위 폴더는 준비가 끝나면 **독립 GitHub 레포**로 올라간다. 부모 레포(Lovable 동기화 브랜치)에 절대 커밋하지 말 것 — ESP 하나만 1.6GB다.

> ## 📄 현재 구현 상태의 정본: [DOCS/CSI-Guard_구현현황_v2.0_20260910.md](DOCS/CSI-Guard_구현현황_v2.0_20260910.md)
>
> 무엇이 동작하고 무엇이 없는지, 확정된 아키텍처 결정 5가지, 실측 수치, 남은 작업과 리스크,
> AWS 현황을 한 문서에 정리했다. **처음 보는 사람은 여기부터.**
>
> 모델 실행 경로의 최신 보완 결과는 [DOCS/WIFI_GUARD_모델_E2E_준비결과_20260916.md](DOCS/WIFI_GUARD_모델_E2E_준비결과_20260916.md)에 있다.

> ## 🔀 이번 통합 변경과 머지 인계: [MERGE_INTEGRATION.md](MERGE_INTEGRATION.md)
>
> Batch8 SPI, Raspberry Pi 전송 어댑터, 새 CSI 낙상 학습/예측 패키지의 변경 범위와
> 검증 결과, 아직 완료되지 않은 운영 추론 연결을 구분해 기록했다.

배치 작업일: 2026-09-03 · 명세 작성일: 2026-08-04 (v1.0) · 검토 보고서 [REVIEW_20260907.md](REVIEW_20260907.md) (§8 클라우드 기동, §9 2차 구현) · 실시간 파이프라인 계획 `~/.claude/plans/rosy-wobbling-balloon.md`

---

## 레포 4종

```
[ESP32-C5 TX] ──UDP 320Hz──► [ESP32-C5 RX] ──WGSP Batch8/SPI──► [Raspberry Pi] ──MQTT/TLS──► [Cloud] ──REST/WS──► [Web]
      └──────── WIFIGUARD-ESP ────────┘                 WIFIGUARD-RASPBERRY       WIFIGUARD-BACKEND   WIFIGUARD-FRONTEND
```

> 새 기본 실기 경로는 **WGSP v1 Batch8 SPI**다. 576B 프레임 8개를 4608B 한 트랜잭션으로
> 읽으며, 6MHz·READY BCM25·Linux direct ioctl을 사용한다. 기존 UART·replay 경로도 삭제하지
> 않고 호환 경로로 유지한다. 30초 실측에서 19,268프레임, 320.014Hz, MQTT 발행 168건,
> 전송 오류 0 및 Kafka offset 증가를 확인했다.
>
> **UART는 921600 → 2,000,000으로 올렸다(2026-09-10).** 921600은 660B 프레임 기준 약 140 fps가
> 상한이라 실측 수신율 약 167Hz를 감당하지 못했다. 펌웨어 실측도 같은 결론이다(UART TX가 패킷당
> 7.16ms로 Wi-Fi 콜백을 블로킹). 새 펌웨어를 플래시하면 **921600을 가정한 기존 도구는 멈춘다** —
> 영향 범위는 [WIFIGUARD-ESP/README.md](WIFIGUARD-ESP/README.md)의 baud 절에 표로 정리했다.

| 폴더 | 역할 | 명세 | 상태 | 크기 |
|---|---|---|---|---:|
| [WIFIGUARD-RASPBERRY/](WIFIGUARD-RASPBERRY/) | 엣지 — CSI 수집 · **재실감지** · 서브캐리어 선택·PCA 합성 · MQTT 업링크 | [raspberry](WIFIGUARD-RASPBERRY/docs/WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md) | **Batch8 SPI·UART·replay 구현** · SPI→MQTT→Kafka 실측 · `pytest` 89 통과, 하드웨어 선택 테스트 2 skip · SPI train 제어·스풀은 후속 | ~400KB |
| [WIFIGUARD-BACKEND/](WIFIGUARD-BACKEND/) | 클라우드 — 서비스 API · DB · Kafka · **피처 변환 + 낙상 추론** · 알림 · MLOps | [backend](WIFIGUARD-BACKEND/docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md) | API·WS·인제스트·라이브 추론·EDGE 낙상 저장·ntfy 라우팅 구현 · **실제 학습 가중치만 별도 인수 필요** | ~112MB |
| [WIFIGUARD-FRONTEND/](WIFIGUARD-FRONTEND/) | 웹 관제 대시보드 — 클라우드 실데이터 | [frontend](WIFIGUARD-FRONTEND/docs/WIFI-GUARD_레포명세_frontend_v1.0_20260804.md) | **전 라우트 실 API + `/ws/live` 실시간 재실 연결 완료(2026-09-10)** · Vitest 34 · HOME·FACILITY 모두 동작 · 낙상 축은 추론(M5) 대기라 "감지 미동작" | ~3MB |
| [WIFIGUARD-ESP/](WIFIGUARD-ESP/) | 펌웨어 — ESP32-C5 송수신기 | [esp](WIFIGUARD-RASPBERRY/docs/WIFI-GUARD_레포명세_esp_v1.0_20260804.md) | 기존 UART 유지 + 320Hz 송신기와 WGSP Batch8 SPI 수신기 추가 · 실기 경로 검증 | ~23MB |

각 폴더의 `PORTING.md`가 **원본 → 목표 매핑 · 남은 작업 · 미결정 사항**을 담고 있다. `README.md`는 그 레포의 책임 경계와 실행법이다.

---

## 배치 원칙

1. **원본은 건드리지 않았다.** `Guardian Angel Alert/backend/`와 `src/`는 지금도 로컬에서 구동되는 현행 코드다. 여기 있는 것은 전부 **사본**이다.
2. **명세 §6.1 매핑의 목표 경로에 바로 배치**했다. 스테이징 디렉토리를 따로 두지 않았다 — 어느 파일이 어디로 가는지는 각 `PORTING.md`가 기록한다.
3. 초기 분리 때는 설정 파일만 만들었지만, 이번 통합에서 **실제 동작 코드도 추가했다.** Raspberry에 WGSP Batch8 transport를 넣고 Backend에 독립 CSI 낙상 학습/예측 패키지를 배치했다. 스텁으로 남기지 않았고 테스트와 합성 데모로 실행을 확인했다.
4. 기존 UART·replay·API·웹 경로는 삭제하지 않았다. Raspberry replay 스레드 종료 버그와 Frontend 포맷 오류처럼 회귀 테스트에서 확인된 문제만 최소 수정했다. 상세 변경 범위는 [MERGE_INTEGRATION.md](MERGE_INTEGRATION.md)에 있다.
5. **실제 운영 모델 weights는 포함하지 않는다.** 체크포인트는 `WIFIGUARD-BACKEND/weights/model.pt`에 외부 주입하고 SHA-256을 검증한다. 형식과 인수 절차는 [MODEL_HANDOFF.md](WIFIGUARD-BACKEND/docs/MODEL_HANDOFF.md)에 있다.

하드웨어 없는 적재 회귀는 `scripts/validate-mock-storage.ps1`로 실행한다. 계약 메시지 주입과
Raspberry 합성 replay를 모두 MQTT→Kafka로 흘린 뒤, 고유 UUID의 Kafka 레코드를 다시 읽어
스키마까지 검증한다.

---

## 공통 계약 — SSOT는 `WIFIGUARD-BACKEND/packages/contracts/`

4개 레포가 공유하는 유일한 코드 자산이다 (Pydantic v2). **2026-09-10 작성 완료.**

| 모듈 | 내용 | 케이스 |
|---|---|---|
| `api.py` | REST 요청·응답 → `openapi.json` → 프론트 코드젠 | camelCase |
| `mqtt.py` | `PresenceMsg` · `SignalMsg` · `TelemetryMsg` · `CmdMsg` · `AckMsg` | snake_case |
| `kafka.py` | `FeatureRecord` · `StatusRecord` · `InferenceResult` | snake_case |
| `realtime.py` | `/ws/live` 프레임 → `GET /realtime/schema` → 프론트 코드젠 | snake_case |
| `topics.py` | MQTT·Kafka 토픽 조립·파싱 | — |

`api.py`만 camelCase인 이유: REST는 프론트가 쓰던 이름을 유지해야 하고, 나머지는 엣지 dataclass
`PresenceStatus` = DB 컬럼 `presence_samples` 이름을 **전 구간에서 한 번도 바꾸지 않기** 위해서다.
`packages/contracts/tests/test_contract_parity.py`가 이 일치를 강제한다.

### MQTT 토픽 (구 `csiguard/` → `wifiguard/`)

| 토픽 | 방향 | QoS | 주기 | 페이로드 |
|---|---|---|---|---|
| `wifiguard/{tenant}/{device}/presence` | Pi→ | 1 | 4Hz | `PresenceMsg` (11개 스칼라) |
| `wifiguard/{tenant}/{device}/telemetry` | Pi→ | 1 | ~1s | `TelemetryMsg` (링크·루프·게이트) |
| `wifiguard/{tenant}/{device}/signal` | Pi→ | 0 | **게이팅 시에만** 4Hz | `SignalMsg` (1-D 대표신호 약 2KB, float32 무손실) |
| `wifiguard/{tenant}/{device}/cmd` | →Pi | 1 | 이벤트 | `calibrate` / `set_config` / `set_mode` / `ping` |
| `wifiguard/{tenant}/{device}/ack` | Pi→ | 1 | 이벤트 | 커맨드 결과, 캘리브레이션 페이즈 |

`{tenant}`는 FACILITY면 시설 UUID, HOME이면 `home-{userId}`. 아웃바운드 8883만.

> **`window` leaf는 폐기됐다(2026-09-10).** Pi가 S3(224,224)+PCA-ACF(1,128,64) 텐서 **233KB**를
> 올리는 설계는 4Hz 기준 7.5Mbps/기기라 성립하지 않았고, Pi 벤치 p90 489ms로 250ms 예산도
> 넘겼다. 이제 Pi는 `select_pc_signal()`의 **1-D 합성 대표신호**(약 2KB, 117배 감소)까지만
> 만들고, CWT 스칼로그램·ACF 변환은 클라우드 모델서버가 한다. 신호를 양자화하지 않는 이유는
> `mqtt.SignalMsg` docstring 참조 — q-metric이 바뀌어 디노이즈 강도가 달라진다.

### Kafka

`csi-feature-stream` (presence + signal, 파티션 키 = device_id) · `csi-telemetry` (telemetry + ack) ·
`csi-inference-result` (`InferenceResult`: `proba_fall` · `threshold` · `postprocess` · 지연 계측)

모델서버는 확률까지만 낸다. 상태 판정과 `fall_events` 생성은 백엔드 서비스 영역이다.

### `/ws/live`

`{link, presence, fall}` **중첩** 구조다(구 평탄 dict에서 변경 — 재실의 `mv_threshold`와 낙상의
`threshold`가 충돌해 `presence_*` 접두가 필요했던 문제를 구조로 없앴다).
**`fall`이 없으면 "낙상 없음"이 아니라 "낙상 감지가 동작하지 않음"이다** — 안전 요구. `presence`도 같다.

### 세 축의 임계값 — 절대 섞지 말 것

| 값 | 출처 | 척도 | UI 라벨 |
|---|---|---|---|
| `presence_mv_threshold` | 캘리브레이션 (Pi) | MV 스케일, 기본 2.0 | "움직임 임계값" |
| `wander_baseline` | 캘리브레이션 (Pi) | Welch PSD 스케일, 기본 0.5 | "재실 baseline" |
| `threshold` = **0.468** | 모델 (클라우드) | **확률** 0~1 | "판정 임계값" |

---

## 독립 레포로 올리는 순서 (제안)

1. **`WIFIGUARD-ESP`부터 정리** — `build/`, `.cache/`, `tools/lagacy/env/`, `tools/stride/data/` 제거 **완료(2026-09-08, 23MB)**, `.gitignore`에 `managed_components/`·`sdkconfig`·`sdkconfig.old` 추가. `git init`은 원본 `esp32c5`의 미커밋 변경 정리(REVIEW P7)와 Wi-Fi 비밀번호 회전(P6) 후.
2. **`WIFIGUARD-RASPBERRY`** — 가장 준비가 되어 있다. `git init` 후 바로 Phase 1(전송 계층 어댑터화) 착수 가능.
3. **`WIFIGUARD-BACKEND`** — `_reference/Window3BestModelInference/weights/*.pt`를 LFS로. `.gitignore`에 `*.pt`가 이미 있으므로 LFS 설정 전에는 커밋되지 않는다.
4. **`WIFIGUARD-FRONTEND`** — **Lovable 연결을 옮길지 끊을지 먼저 결정** (frontend PORTING.md §4-1). 옮긴다면 이미 푸시된 커밋의 force-push·rebase를 피할 것.

각 폴더에 `git init`을 하면 부모 레포 입장에서는 gitignored 디렉토리 안의 nested repo라 충돌하지 않는다.

---

## 원본 위치 (참고)

| 자산 | 경로 |
|---|---|
| 현행 프론트·백엔드 | `csi_fall/MVP-MOCKUP/Guardian Angel Alert/{src,backend}/` |
| 라즈베리 SPI 시제품 | `csi_fall/infra/raspberry/` (`.conda/` 105MB 제외) |
| ESP 펌웨어 | `csi_fall/esp32c5/` (git repo) |
| 모델 패키지 | `csi_fall/MVP-MOCKUP/Guardian Angel Alert/Window3BestModelInference/` (109MB, gitignored) |
| DVC 학습 파이프라인 | `csi_fall/dwt_analysis/` (git repo, 데이터셋 수 GB) |
| 구 MV 임계값 파이프라인 | `csi_fall/MVP-MOCKUP/Guardian Angel Alert/reference/fall_detect/` (gitignored) = `csi_fall/esp32c5/tools/fall_detect/` |
| 반면교사 | `csi_fall/csi_fall_monorepo/` (스텁만, uv workspace 깨짐). 이 디렉토리(`csi_fall/MVP_WIFI_GUARD/`)는 과거 같은 이름의 빈 분리 시도 자리를 2026-09-03에 재사용한 것이다 |
