# WIFI-GUARD Frontend (웹 관제 대시보드)

TanStack Start(React 19) 기반 실시간 관제 대시보드. **화면은 이미 다 있다.** 인메모리 목업 스토어가 소유하던 상태를 클라우드 API/WebSocket으로 옮기는 것이 이 레포의 과제다.

```
현재                                    목표
────────────────────────────────────    ────────────────────────────────────
mock-store.ts (1,195줄)                  TanStack Query 훅
  · setInterval(tick, 100) 시뮬레이션      · REST + WS (다기기 구독)
  · FACILITY 100% 목업                     · FACILITY 실데이터
  · localStorage 세션                      · JWT
backend.ts → 127.0.0.1:8000 하드코딩      → 환경변수 (클라우드 API)
```

> ## 현재 상태: 원본 프론트엔드 전체 복사 완료. 그대로 빌드·실행된다.
>
> `src/`는 원본 `Guardian Angel Alert/src/`와 **바이트 단위로 동일**하다 (`src/api/` 빈 디렉토리만 추가).
> 아직 아무것도 바꾸지 않았다 — `BACKEND_URL` 하드코딩도, 목업 최상위 실행도 그대로다.
> 무엇을 바꿔야 하는지는 [PORTING.md](PORTING.md)를 볼 것.

---

## 책임 경계

| 하는 것                                                                   | 하지 않는 것                                   |
| ------------------------------------------------------------------------- | ---------------------------------------------- |
| 실시간 관제 · 낙상 이력 · 이벤트 로그 · 장치/거주자 관리 · 알림 · 설정 UI | 신호처리·감지 판정 → Pi(재실) / 클라우드(낙상) |
| 클라우드 API 연동 (TanStack Query) + WebSocket 실시간 구독                | 알림 발송 → 백엔드 `notification` 서비스       |
| JWT 인증 · 역할 기반 화면 가드                                            | 기기 직접 통신 → 백엔드 → MQTT → Pi            |
| 낙상 전체화면 알람 (`/ws/live` 구독, **앱 내 경로**)                      |                                                |
| 캘리브레이션 진행 UI (4단계 61초)                                         |                                                |

> **알림 이중 경로**: 휴대폰 푸시(SMS/ntfy)는 백엔드에서 직접 나가고 프론트를 거치지 않는다. 앱 내부 `FallAlarmModal`은 `/ws/live`의 `detect_state`를 구독하는 **별개 경로**다. 이 이중화가 한 쪽 장애 시의 안전망이며, 프론트는 자신의 경로만 책임진다.

---

## 실행

```bash
bun install
cp .env.example .env      # 아직 읽는 코드는 없다 — Phase 0에서 backend.ts 가 읽게 만든다
bun run dev
```

| 명령                          |                      |
| ----------------------------- | -------------------- |
| `bun run dev`                 | vite 개발 서버       |
| `bun run build` / `build:dev` | 프로덕션 / 개발 빌드 |
| `bun run preview`             | 빌드 결과 미리보기   |
| `bun run lint` / `format`     | eslint / prettier    |

테스트 러너는 없다. 도입(Vitest + Playwright)은 별도 합의 사항.

## 구조

```
src/
  routes/            파일기반 라우팅 13개 (구조 무변경)           routeTree.gen.ts 는 자동생성 — 손대지 말 것
  components/        AuthGate · AppSidebar · FallAlarmModal · BackendDetectionBridge · EventLogPanel
  components/ui/     shadcn/radix 프리미티브 50개
  lib/
    mock-store.ts    ★ 전 화면 상태 소유 (1,195줄) — 개발용 폴백으로 격리 예정
    backend.ts       ★ 로컬 백엔드 계약 (572줄) — src/api/ 로 분해 예정
    error-*.ts       SSR 오류 처리 — 건드리지 말 것
  api/               ★ 신규 데이터 계층 자리 (비어 있음)
    generated/       OpenAPI 코드젠 산출물 (수정 금지)
    queries/         useDevices, useResidents, useFalls, useEventLogs, useRecipients …
    mutations/       upsertDevice, upsertResident, updateResponse …
    realtime/        socket.ts (재연결·백오프) · useLiveStream.ts (기기별 구독)
docs/                명세 문서
.env.example         VITE_API_BASE_URL · VITE_WS_URL · VITE_USE_MOCK
```

---

## 세 축의 임계값 — 절대 섞지 말 것

| 값                                | 출처              | 척도                                 | UI 라벨                                |
| --------------------------------- | ----------------- | ------------------------------------ | -------------------------------------- |
| `presence_mv_threshold`           | 캘리브레이션 (Pi) | MV 스케일, 기본 2.0                  | **"움직임 임계값"**                    |
| `wander_baseline`                 | 캘리브레이션 (Pi) | Welch PSD 스케일, 기본 0.5           | **"재실 baseline"**                    |
| `wander_ratio_threshold`          | 설정 (Pi)         | **baseline 대비 배수** 1~5, 기본 1.8 | "WANDER 비율 임계값"                   |
| `threshold` = **0.468**           | 모델 (클라우드)   | **확률** 0~1                         | **"판정 임계값"** / "낙상 확률 임계값" |
| `PipelineConfig.wander_threshold` | 목업 전용         | **절대값** 0~1                       | (목업 폴백에서만)                      |

목업의 `wander_threshold`(절대값)와 실백엔드의 `wander_ratio_threshold`(배수)는 **이름이 비슷해도 서로 변환되지 않는 별개의 숫자**다.

## `/ws/live` 계약 — 낙상 필드의 부재는 "낙상 없음"이 아니다

재실 필드(`presence_state`, `mv_current` …)는 엣지가 연결되어 있으면 **항상** 있고, 낙상 필드(`detect_state`, `proba_fall` …)는 모델이 로드된 경우에만 추가된다. **낙상 필드가 없으면 "낙상 감지가 동작하지 않음"이다.** 안전 기능이므로 이 상태를 무증상으로 넘기면 안 된다 — 사이드바 3상태 표시(정상 / **낙상 감지 중단** / 완전 오프라인) + 대시보드 배너가 Phase 4-2의 신규 요구다.

## 도메인 용어

| 개념                 | UI 표기                                                              |
| -------------------- | -------------------------------------------------------------------- |
| MV / moving variance | **움직임 감지** (임계값·라벨), "이동 분산 (딥러닝 입력)" (신호 자체) |
| 낙상 상태            | IDLE=**대기**, SUSPECT=**의심**, FALL=**낙상**, COOLDOWN=**냉각중**  |
| 재실 상태            | PRESENT=**재실**, ABSENT=**퇴실**                                    |
| 서비스 유형          | HOME=**가정**, FACILITY=**시설**                                     |
| FACILITY 역할        | ROOT=**시설 등록자**, MEMBER=**초대코드로 참여**                     |
| 응답 상태            | 대기중 / 확인함 / 출동중 / 오탐지                                    |

---

## 문서

- [PORTING.md](PORTING.md) — 이식 매핑 · 전환 작업 목록 · 미결정 사항
- [CLAUDE.md](CLAUDE.md) — 아키텍처 · 컨벤션 (프론트 전용판)
- [docs/WIFI-GUARD\_레포명세\_frontend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_frontend_v1.0_20260804.md) — 이 레포의 명세
- [docs/WIFI-GUARD\_레포명세\_backend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md) §7 — **4레포 공통 계약 SSOT**
- [docs/CSI-Guard\_기능정의서\_HOME_v1.0_20260717.md](docs/CSI-Guard_기능정의서_HOME_v1.0_20260717.md) — F-001~F-077 기능 정의
- [docs/CSI-Guard\_기능명세서\_v2.0_20260715.md](docs/CSI-Guard_기능명세서_v2.0_20260715.md) — 페이지별 상세 (현행 구현 기준)
- [docs/CSI-Guard*HOME*유저플로우\_v1.0_20260715.md](docs/CSI-Guard_HOME_유저플로우_v1.0_20260715.md) — 8개 사용자 시나리오
