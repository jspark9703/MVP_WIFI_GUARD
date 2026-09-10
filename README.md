# MVP_WIFI_GUARD — WIFI-GUARD 4-레포 분리 작업장

현행 단일 레포(`Guardian Angel Alert`: 로컬 FastAPI + TanStack 목업)를 **엣지 / 클라우드 / 프론트엔드 / 펌웨어** 4갈래로 쪼개기 위한 로컬 스테이징 디렉토리다.

> **이 디렉토리는 부모 레포에서 `.gitignore` 처리되어 있다.** 각 하위 폴더는 준비가 끝나면 **독립 GitHub 레포**로 올라간다. 부모 레포(Lovable 동기화 브랜치)에 절대 커밋하지 말 것 — ESP 하나만 1.6GB다.

> ## 📄 현재 구현 상태의 정본: [DOCS/CSI-Guard_구현현황_v2.0_20260910.md](DOCS/CSI-Guard_구현현황_v2.0_20260910.md)
>
> 무엇이 동작하고 무엇이 없는지, 확정된 아키텍처 결정 5가지, 실측 수치, 남은 작업과 리스크,
> AWS 현황을 한 문서에 정리했다. **처음 보는 사람은 여기부터.**

배치 작업일: 2026-09-03 · 명세 작성일: 2026-08-04 (v1.0) · 검토 보고서 [REVIEW_20260907.md](REVIEW_20260907.md) (§8 클라우드 기동, §9 2차 구현) · 실시간 파이프라인 계획 `~/.claude/plans/rosy-wobbling-balloon.md`

---

## 레포 4종

```
[ESP32-C5 TX] ──ESP-NOW 5GHz──► [ESP32-C5 RX] ──UART 2Mbaud──► [Raspberry Pi] ──MQTT/TLS──► [Cloud] ──REST/WS──► [Web]
      └──────── WIFIGUARD-ESP ────────┘                 WIFIGUARD-RASPBERRY       WIFIGUARD-BACKEND   WIFIGUARD-FRONTEND
```

> 명세는 ESP↔Pi를 **SPI**로 규정하지만 **펌웨어에 SPI slave 코드가 없다.** 실제로 동작하는 유일한
> 경로는 UART이며, Pi의 SPI 프로토콜 정의(CSI 128B/64서브캐리어)는 펌웨어가 실제로 내보내는
> 612B/306서브캐리어를 담지 못한다. SPI 전환은 후속 과제다(계획서 R5).
>
> **UART는 921600 → 2,000,000으로 올렸다(2026-09-10).** 921600은 660B 프레임 기준 약 140 fps가
> 상한이라 실측 수신율 약 167Hz를 감당하지 못했다. 펌웨어 실측도 같은 결론이다(UART TX가 패킷당
> 7.16ms로 Wi-Fi 콜백을 블로킹). 새 펌웨어를 플래시하면 **921600을 가정한 기존 도구는 멈춘다** —
> 영향 범위는 [WIFIGUARD-ESP/README.md](WIFIGUARD-ESP/README.md)의 baud 절에 표로 정리했다.

| 폴더 | 역할 | 명세 | 상태 | 크기 |
|---|---|---|---|---:|
| [WIFIGUARD-RASPBERRY/](WIFIGUARD-RASPBERRY/) | 엣지 — CSI 수집 · **재실감지** · 서브캐리어 선택·PCA 합성 · MQTT 업링크 | [raspberry](WIFIGUARD-RASPBERRY/docs/WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md) | **엔트리포인트·설정·게이팅·MQTT 구현 완료(2026-09-10) — 브로커까지 실측 발행** · `pytest` 77 · Pi 없이 `--transport replay` 로 전 배선 구동 · 캘리브레이션 cmd·스풀·실기 미검증 | ~400KB |
| [WIFIGUARD-BACKEND/](WIFIGUARD-BACKEND/) | 클라우드 — 서비스 API · DB · Kafka · **피처 변환 + 낙상 추론** · 알림 · MLOps | [backend](WIFIGUARD-BACKEND/docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md) | **서비스 API `/api/v1` + `/ws/live` + 인제스트(MQTT→Kafka→TimescaleDB→캐시) 구현 완료(2026-09-10)** · 계약 5종 · `pytest` 77 · AWS EC2 2대 기동 중(Kafka·MQTT 인스턴스는 M4) · **낙상 추론·알림 미구현** | ~112MB |
| [WIFIGUARD-FRONTEND/](WIFIGUARD-FRONTEND/) | 웹 관제 대시보드 — 클라우드 실데이터 | [frontend](WIFIGUARD-FRONTEND/docs/WIFI-GUARD_레포명세_frontend_v1.0_20260804.md) | **전 라우트 실 API + `/ws/live` 실시간 재실 연결 완료(2026-09-10)** · Vitest 34 · HOME·FACILITY 모두 동작 · 낙상 축은 추론(M5) 대기라 "감지 미동작" | ~3MB |
| [WIFIGUARD-ESP/](WIFIGUARD-ESP/) | 펌웨어 — ESP32-C5 송수신기 | [esp](WIFIGUARD-RASPBERRY/docs/WIFI-GUARD_레포명세_esp_v1.0_20260804.md) | `csi_fall/esp32c5/` 원본 복사 · build/venv/stride CSV **정리 완료(1.6GB → 23MB, 2026-09-08)** · `git init`은 원본 미커밋 변경 정리(REVIEW P7) 후 | ~23MB |

각 폴더의 `PORTING.md`가 **원본 → 목표 매핑 · 남은 작업 · 미결정 사항**을 담고 있다. `README.md`는 그 레포의 책임 경계와 실행법이다.

---

## 배치 원칙

1. **원본은 건드리지 않았다.** `Guardian Angel Alert/backend/`와 `src/`는 지금도 로컬에서 구동되는 현행 코드다. 여기 있는 것은 전부 **사본**이다.
2. **명세 §6.1 매핑의 목표 경로에 바로 배치**했다. 스테이징 디렉토리를 따로 두지 않았다 — 어느 파일이 어디로 가는지는 각 `PORTING.md`가 기록한다.
3. **신규 파이썬/TS 모듈 본문은 작성하지 않았다.** `csi_fall/csi_fall_monorepo/`가 `NotImplementedError` 스텁만 남기고 멈춘 전례가 있다. 설정 파일(compose, toml, requirements, .env.example, systemd)까지만 만들었다.
4. **복사 중 가한 유일한 코드 수정**: RASPBERRY의 4개 파일에서 평평한 절대 import를 상대 import로 바꿨다 (`from presence import` → `from .presence import` 등). 원본과의 `diff`가 그 줄들뿐임을 확인했다.
5. **모델 weights(89MB)는 BACKEND `_reference/`에 복사했다.** GitHub 100MB 한도에 근접하므로 분리 시 Git LFS 또는 MLflow+S3가 필요하다.

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
