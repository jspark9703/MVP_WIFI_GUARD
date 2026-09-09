# MVP_WIFI_GUARD — WIFI-GUARD 4-레포 분리 작업장

현행 단일 레포(`Guardian Angel Alert`: 로컬 FastAPI + TanStack 목업)를 **엣지 / 클라우드 / 프론트엔드 / 펌웨어** 4갈래로 쪼개기 위한 로컬 스테이징 디렉토리다.

> **이 디렉토리는 부모 레포에서 `.gitignore` 처리되어 있다.** 각 하위 폴더는 준비가 끝나면 **독립 GitHub 레포**로 올라간다. 부모 레포(Lovable 동기화 브랜치)에 절대 커밋하지 말 것 — ESP 하나만 1.6GB다.

배치 작업일: 2026-09-03 · 명세 작성일: 2026-08-04 (v1.0) · 검토 보고서 [REVIEW_20260907.md](REVIEW_20260907.md) (§8 클라우드 기동 결과, §9 2차 구현 결과)

---

## 레포 4종

```
[ESP32-C5 TX] ──ESP-NOW 5GHz──► [ESP32-C5 RX] ──SPI──► [Raspberry Pi] ──MQTT/TLS──► [Cloud] ──REST/WS──► [Web]
      └──────── WIFIGUARD-ESP ────────┘            WIFIGUARD-RASPBERRY        WIFIGUARD-BACKEND   WIFIGUARD-FRONTEND
```

| 폴더 | 역할 | 명세 | 상태 | 크기 |
|---|---|---|---|---:|
| [WIFIGUARD-RASPBERRY/](WIFIGUARD-RASPBERRY/) | 엣지 — CSI 수집 · **재실감지** · 피처 추출 · MQTT 업링크 | [raspberry](WIFIGUARD-RASPBERRY/docs/WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md) | 이식 배치 완료 · import·`pytest`(5) 통과 · `tools/bench_pipeline.py` 피처 추출 전용으로 정리(2026-09-08) · 엔트리포인트·MQTT·게이팅 미구현 | ~400KB |
| [WIFIGUARD-BACKEND/](WIFIGUARD-BACKEND/) | 클라우드 — 서비스 API · DB · (후속) Kafka · **낙상 추론** · 알림 · MLOps | [backend](WIFIGUARD-BACKEND/docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md) | **HOME/FACILITY 서비스 API `/api/v1` + PostgreSQL(Alembic) 구현, AWS EC2 2대 기동 완료(2026-09-08, `deploy/aws/`)** · 실시간·추론·MLOps 미구현 · `_reference/` 이식본은 의도적으로 import 불가 | ~112MB |
| [WIFIGUARD-FRONTEND/](WIFIGUARD-FRONTEND/) | 웹 관제 대시보드 — 클라우드 실데이터 | [frontend](WIFIGUARD-FRONTEND/docs/WIFI-GUARD_레포명세_frontend_v1.0_20260804.md) | **전 라우트 실 API 전환 완료(TanStack Query, 2026-09-08)** · 목업은 `VITE_USE_MOCK=1` 데모 전용 · 실시간 카드는 "감지 미동작" | ~3MB |
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

## 공통 계약 — SSOT는 backend 명세 §7

4개 레포가 공유하는 유일한 코드 자산은 `WIFIGUARD-BACKEND/packages/contracts/` (Pydantic v2)가 될 예정이다. **아직 비어 있다.** 이것이 Phase 0이고, 이게 있어야 4개 레포의 병렬 개발이 가능하다.

### MQTT 토픽 (구 `csiguard/` → `wifiguard/`)

| 토픽 | 방향 | QoS | 주기 | 페이로드 |
|---|---|---|---|---|
| `wifiguard/{facility}/{device}/presence` | Pi→ | 1 | 4Hz | `PresenceMsg` (11개 스칼라) |
| `wifiguard/{facility}/{device}/telemetry` | Pi→ | 1 | ~5s | `TelemetryMsg` |
| `wifiguard/{facility}/{device}/window` | Pi→ | 0 | **게이팅 시에만** 4Hz | `WindowMsg` (S3 + ACF, 양자화+zstd+MessagePack) |
| `wifiguard/{facility}/{device}/cmd` | →Pi | 1 | 이벤트 | `calibrate` / `set_config` / `set_mode` / `ping` |
| `wifiguard/{facility}/{device}/ack` | Pi→ | 1 | 이벤트 | 커맨드 결과, 캘리브레이션 페이즈 |

HOME 계정은 `{facility}` 자리에 `home-{userId}`. mTLS 기기별 인증서, 아웃바운드 8883만.

### Kafka

`csi-feature-stream` (presence + window + 메타) · `csi-telemetry` · `csi-inference-result` (`{proba_fall, state, threshold, ts}`)

### `/ws/live` (10Hz)

재실 필드는 엣지 연결 시 **항상**, 낙상 필드는 모델 로드 시에만. **낙상 필드의 부재는 "낙상 없음"이 아니라 "낙상 감지가 동작하지 않음"이다** — 안전 요구.

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
