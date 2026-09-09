# WIFI-GUARD-frontend 레포 개발 명세서

**웹 관제 대시보드 · 목업 스토어 → 클라우드 실데이터 전환**

v1.0 · 작성일 2026-08-04

> 4-레포 분리 중 **프론트엔드** 레포의 명세다. UI는 대부분 완성되어 있고, 이번 작업의 본질은 **데이터 소스 교체**다.
> 전제 문서: `CSI-Guard_기능정의서_HOME_v1.0_20260717.md`(F-001~F-077), `CSI-Guard_기능명세서_v2.0_20260715.md` §1, `CSI-Guard_HOME_유저플로우_v1.0_20260715.md`
> 인접 레포: [backend](WIFI-GUARD_레포명세_backend_v1.0_20260804.md)

---

## 1. 레포 개요 · 책임 경계

### 1.1 한 줄 정의

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

### 1.2 하는 것

| # | 책임 |
|---|---|
| W1 | 실시간 관제 · 낙상 이력 · 이벤트 로그 · 장치/거주자 관리 · 알림 · 알고리즘 설정 UI |
| W2 | 클라우드 API 연동 (TanStack Query) + WebSocket 실시간 구독 |
| W3 | JWT 인증 · 역할 기반 화면 가드 |
| W4 | 낙상 전체화면 알람 (`/ws/live` 구독, 앱 내 경로) |
| W5 | 캘리브레이션 진행 UI (4단계 61초) |
| W6 | 다기기·다입소자 관제 (FACILITY) |

### 1.3 하지 않는 것

| 항목 | 어디서 |
|---|---|
| 신호처리·감지 판정 | Pi(재실) / 클라우드(낙상) |
| 알림 발송 | 백엔드 `notification` 서비스 |
| 기기 직접 통신 | 백엔드 → MQTT → Pi |

> **알림 이중 경로 유지**: 휴대폰 푸시(SMS/ntfy)는 백엔드에서 직접 나가고 프론트를 거치지 않는다. 앱 내부 `FallAlarmModal`은 `/ws/live`의 `detect_state`를 구독하는 **별개 경로**다. 두 경로는 트리거만 공유하며, 이 이중화가 한 쪽 장애 시의 안전망이다. 프론트는 자신의 경로만 책임진다.

---

## 2. 핵심 기능 목록

기존 기능정의서의 F-001~F-077을 승계한다. 아래는 **이번 전환에서 바뀌는 것**만 정리한다.

| ID | 기능 | 현재 | 목표 |
|---|---|---|---|
| F-W01 | 백엔드 주소 | `backend.ts:6` 하드코딩 `http://127.0.0.1:8000` | **환경변수** |
| F-W02 | 데이터 계층 | `mock-store.ts`가 전 화면 상태 소유 | **TanStack Query** (설치·Provider 연결 완료, 실사용만 남음) |
| F-W03 | 인증 | `localStorage` mock 세션 (`{userId}`만) | **JWT** + refresh |
| F-W04 | 타입 계약 | `LiveSample` 등을 손으로 미러링 | **OpenAPI 코드젠** (`packages/contracts`) |
| F-W05 | 실시간 | 단일 WS 싱글턴 (`sharedWs`), 1기기 | **다기기 구독** + 중앙 pub/sub 팬아웃 |
| F-W06 | FACILITY | 100% 인메모리 시뮬레이션 | **실데이터** (시뮬레이션은 개발용 폴백으로 격리) |
| F-W07 | MQTT 카드 | 상시 비활성 ("로컬 시리얼 구조 확정 전까지") | **실동작** — 기기 프로비저닝 연결 |
| F-W08 | 낙상 파형 | `fall.id` 시드 PRNG로 **재구성한 시각화** | 실제 저장된 MV/피처 시계열 |
| F-W09 | `/train` | "Coming Soon" 정적 스텁 | **MLflow 연동** — 모델 버전·드리프트 추세 |
| F-W10 | 시설 멤버 관리 | mock CRUD | 실 RBAC |
| F-W11 | 오프라인 표시 | HOME만 "연결 안 됨" 처리 | **낙상 감지 중단 상태를 명시적으로 경고** (신규, 안전 요구) |
| F-W12 | 드리프트 추세 | 없음 | `devices` 화면에 시계열 표시 (신규, G5) |

---

## 3. 디렉토리 구조

현행 구조를 거의 그대로 유지하되 `lib/` 아래를 재편한다.

```
WIFI-GUARD-frontend/
├── package.json                    bun. 현행 의존성 승계
├── vite.config.ts                  @lovable.dev/vite-tanstack-config 래핑 유지
├── components.json                 shadcn/ui new-york
├── tsconfig.json                   @/* → src/*
├── bunfig.toml                     24h 공급망 가드 (minimumReleaseAge)
├── .env.example                    ★ VITE_API_BASE_URL, VITE_WS_URL
├── public/
└── src/
    ├── routes/                     파일기반 라우팅 (구조 무변경)
    │   ├── __root.tsx              QueryClientProvider, AuthGate, Toaster
    │   ├── index.tsx               실시간 관제 (+ 공용 Header/SectionTitle/ResponseBadge 내보냄)
    │   ├── history.tsx  event-log.tsx
    │   ├── devices.tsx  residents.tsx  facility-members.tsx
    │   ├── notifications.tsx  config.tsx  account.tsx
    │   ├── train.tsx               ★ 스텁 → 실구현
    │   ├── login.tsx  signup.tsx
    │   └── routeTree.gen.ts        자동생성 — 손대지 말 것
    ├── components/
    │   ├── AuthGate.tsx            ★ JWT 기반으로 교체, 죽은 /onboarding 분기 제거
    │   ├── AppSidebar.tsx
    │   ├── FallAlarmModal.tsx      ★ 자동 타임아웃 없음 (유지)
    │   ├── EventLogPanel.tsx
    │   ├── LiveBridge.tsx          ★ BackendDetectionBridge 후신 (다기기)
    │   └── ui/                     48개 shadcn/radix 프리미티브
    ├── api/                        ★ 신규: 서버 데이터 계층
    │   ├── client.ts               fetch 래퍼 + JWT 인터셉터 + 에러 정규화
    │   ├── generated/              OpenAPI 코드젠 산출물 (수정 금지)
    │   ├── queries/                useDevices, useResidents, useFalls,
    │   │                           useEventLogs, useRecipients, useConfig …
    │   ├── mutations/              upsertDevice, upsertResident, updateResponse …
    │   └── realtime/
    │       ├── socket.ts           WS 연결 관리 (재연결·백오프)
    │       └── useLiveStream.ts    기기별 구독 훅
    ├── lib/
    │   ├── mock-store.ts           ★ 개발용 폴백으로 격리 (DEV 전용)
    │   ├── domain.ts               ★ 신규: 도메인 타입 (mock-store에서 분리)
    │   ├── format.ts               fmtTime, stateLabel/Color, presenceLabel/Color …
    │   ├── utils.ts
    │   ├── error-capture.ts  error-page.ts   SSR 오류 처리 (유지 — §5.5)
    │   └── lovable-error-reporting.ts
    ├── hooks/
    ├── server.ts  start.ts  router.tsx  styles.css
    └── ...
```

---

## 4. 구현 단계

### Phase 0 — 계약 연결 + 환경변수

`packages/contracts`의 OpenAPI에서 타입을 생성해 `src/api/generated/`에 둔다. 손으로 미러링하던 `LiveSample`/`CalibrationStatus`/`MonitorStatus`/`PresenceConfigSnapshot` 등을 전부 대체한다.

```ts
// .env.example
VITE_API_BASE_URL=http://127.0.0.1:8000      // 개발: 로컬 백엔드 스택
VITE_WS_URL=ws://127.0.0.1:8000/ws/live
```

현재 `src/lib/backend.ts:6`의 하드코딩이 유일한 결합점이므로 전환 비용은 낮다.

**완료 기준**
- [ ] 코드젠 타입으로 빌드 통과, 수동 미러 타입 제거
- [ ] 환경변수로 로컬/스테이징 전환 가능

---

### Phase 1 — 데이터 계층 전환 (mock-store → TanStack Query)

**이 Phase가 이 레포의 본체다.** `mock-store.ts` 1,195줄이 전 화면의 상태를 소유하고 있다.

#### 1-1. 도메인 타입 분리

`mock-store.ts`가 타입 정의와 상태 관리를 겸하고 있다. 타입만 `lib/domain.ts`로 분리하면 목업과 실데이터가 같은 타입을 쓸 수 있다.

분리 대상: `StateMachine`, `Service`, `Role`, `Presence`, `CalibrationStage`, `PipelineConfig`, `Facility`, `UserAccount`, `Session`, `Device`, `Resident`, `FallEvent`, `Recipient`, `EventLogEntry`, `SignupInput`.

#### 1-2. 읽기 경로 교체

| 현재 | 목표 |
|---|---|
| `useStore(s => s.devices)` | `useDevices()` (TanStack Query) |
| `useStore(s => s.residents)` | `useResidents()` |
| `useStore(s => s.falls)` | `useFalls({ response, page })` |
| `useScopedLogs()` | `useEventLogs({ level, q })` — 스코핑은 **서버가** 강제 |
| `useStore(s => s.config)` | `usePresenceConfig()` + `useDetectionConfig()` |
| `useStore(s => s.recipients)` | `useRecipients()` |

**스코핑 이관**: 현재 FACILITY(`facilityId`) / HOME(`ownerUserId`) 스코핑을 프론트에서 필터링한다. **서버 쿼리 레벨 강제로 옮긴다** — 클라이언트 필터는 보안이 아니다.

#### 1-3. 쓰기 경로 교체

`upsertResident` / `upsertDevice` / `updateResponse` / `updateConfig` / `upsertRecipient` / `removeMember` / `regenerateInviteCode` 등 모든 뮤테이션을 `useMutation` + 낙관적 업데이트 + 무효화로 교체한다.

`upsertResident`의 **대표 장치 정규화**(저장 전 대표 장치가 매핑 목록에 포함되도록)는 서버로 옮기거나 양쪽에서 보장한다.

#### 1-4. 시뮬레이션 격리

```ts
// mock-store.ts 상단
if (import.meta.env.DEV && import.meta.env.VITE_USE_MOCK === "1") {
  setInterval(tick, 100);
}
```

현재 `setInterval(tick, 100)`이 **모듈 최상위에서 무조건 실행**된다(`typeof window` 가드만 있음). 실데이터 경로와 분기시켜 프로덕션 번들에서 죽은 코드가 되게 한다.

**완료 기준**
- [ ] 전 화면이 실데이터로 렌더링
- [ ] `VITE_USE_MOCK=1`에서만 시뮬레이션 동작
- [ ] 프로덕션 번들에 시뮬레이션 코드가 포함되지 않음 (번들 분석)

---

### Phase 2 — 인증 · 권한

| 항목 | 현재 | 목표 |
|---|---|---|
| 세션 | `localStorage["csi-guard-session"]` = `{userId}` | JWT access + refresh |
| hydration | `hydrateSession()` — SSR 불일치 방지용 `hydrated` 게이트 | 토큰 검증 + 자동 갱신. **`hydrated` 게이트는 유지** |
| 라우트 가드 | `PUBLIC_PATHS` (`/login`, `/signup`) | 유지 + 401 시 자동 로그아웃 |
| RBAC | `facility-members`만 `role !== "ROOT"` 리다이렉트 | 서버 인가 + UI 가드 이중화 |
| `/config` 권한 | MEMBER에게 경고 배너만, Apply 버튼은 막지 않음 | 서버가 403으로 차단, UI도 비활성화 |

**정리 대상**: `AuthGate.tsx`에 `/onboarding` 경로 체크가 남아 있으나 **해당 라우트 자체가 없어 항상 거짓으로 평가되는 죽은 코드**다(제거된 온보딩 위저드의 잔재). 삭제한다.

**완료 기준**
- [ ] 로그인 → 토큰 저장 → 만료 시 자동 갱신 → 실패 시 로그아웃
- [ ] MEMBER 계정으로 `/facility-members` 직접 URL 접근 차단 (서버 + UI)

---

### Phase 3 — 실시간 다기기 구독

현재 `backend.ts`의 WS는 **모듈 레벨 싱글턴 하나**(`sharedWs` + `sharedListeners`)로, 구독자가 있으면 2초마다 재연결하고 마지막 구독자가 언마운트되면 닫는다. 히스토리는 300 샘플로 제한한다. **1기기 전제**다.

FACILITY는 다수 클라이언트가 다수 기기를 구독한다:

```ts
// api/realtime/useLiveStream.ts
function useLiveStream(deviceId: string): LiveStreamState
function useLiveStreams(deviceIds: string[]): Map<string, LiveStreamState>
```

- 연결은 하나로 유지하고 **기기별 구독/해제 메시지**를 보낸다 (현재는 클라이언트→서버 프로토콜이 없다)
- 서버 측 팬아웃은 Redis pub/sub (백엔드 §4-2)
- `BackendDetectionBridge` → `LiveBridge`로 개칭. 현재 `user?.service !== "HOME"`이면 `null`을 반환하는 **HOME 전용** 제약을 제거한다
- 3초 마운트 유예로 거짓 "연결 끊김"을 억제하는 처리는 유지

**완료 기준**
- [ ] FACILITY 다기기 그리드가 각 기기의 실시간 상태를 표시
- [ ] 기기 20대 구독 시 성능 저하 없음
- [ ] 연결 끊김 → 재연결 시 상태 복구

---

### Phase 4 — 신규/전환 화면

#### 4-1. MQTT · 기기 프로비저닝 (`devices.tsx`)

현재 통신설정 패널의 MQTT 섹션은 **상시 비활성**이다("로컬 시리얼 구조 확정 전까지"). 활성화하고 기기 등록 시 자격증명 발급·회수 흐름을 연결한다.

MQTT 토픽 표시를 `wifiguard/{facility}/{device}/...` 형식으로 갱신한다. 현재 `csi/home/{userId 뒷4자리}/{공간 로마자}` 형식으로 자동 생성하는 로직(F-034)은 서버 발급으로 옮긴다.

진단 패널(`/monitor/status`를 시리얼/버퍼·스트림/재실루프/낙상모델 4그룹으로 구조화)은 **엣지 진단 패널**로 확장한다 — 게이팅률, 스풀 적체, CPU 온도, 스로틀 여부 등 `telemetry` 필드 추가.

#### 4-2. 낙상 감지 중단 경고 (신규, 안전 요구)

네트워크 단절 시 **재실감지는 유지되지만 낙상 감지는 중단된다.** 안전 기능이므로 무증상 중단은 허용되지 않는다.

- 사이드바 상태등에 3상태 표시: 정상 / **낙상 감지 중단(재실만 동작)** / 완전 오프라인
- 대시보드 상단 배너로 중단 사실과 지속 시간 명시
- 이벤트 로그에 중단·복구 구간 기록

현재 상태등은 FACILITY = `running`(목업 시뮬레이션 on/off), HOME = `backendConnected`로 갈린다. 실데이터 전환 후에는 **엣지 연결 상태 + 클라우드 추론 가용성** 두 축으로 통합한다.

#### 4-3. 낙상 이력 파형 (F-W08)

현재 상세 패널의 낙상 전후 6초 파형은 **`fall.id`로 시드된 결정론적 PRNG로 재구성한 시각화**이며 실제 저장된 CSI/MV 샘플이 아니다. 백엔드가 TimescaleDB에 MV 시계열을 보존하므로 **실데이터로 교체**한다.

교체 전까지는 UI에 "재구성 시각화"임을 명시할 것.

#### 4-4. `/train` 실구현 (F-W09)

현재 "Coming Soon" 정적 스텁이다(Data Collection / Model Training / Deploy & Evaluate 3단계 표시만). MLflow 연동으로 채운다:

- 현재 배포 모델 버전 · 등록 시각 · 적응 공간
- 버전별 성능 지표 비교 (Macro F1, 낙상 Recall)
- 드리프트 추세 시계열 (G5)
- 재학습 이력 및 Validation Gate 통과/차단 결과
- 롤백 액션 (승인 필요)

#### 4-5. 캘리브레이션 UI

현행 4단계 진행 모달을 유지한다. 목업 타이머(`startDeviceReset`, 100ms 인터벌)와 실백엔드 폴링(`applyCalibrationStatus`)이 **같은 `Device` 필드에 쓰기** 때문에 렌더링 로직이 통합되어 있다 — 이 구조를 유지하면 개발용 폴백이 그대로 산다.

```
CALIBRATION_PHASE_SECONDS = { LEAVING: 30, WAITING_ACK: 0.2, WAITING_AGC: 1, MEASURING: 30 }
                                                                    총 약 61.2초
```

이제 캘리브레이션은 백엔드 → MQTT `cmd` → Pi 경로이므로, 상태 조회는 `ack` 집계 결과를 폴링한다.

---

## 5. 핵심 구현 모듈 상세

### 5.1 유지해야 할 세 축의 임계값 구분 ★ 중요

이 프로젝트에는 **이름이 비슷하지만 척도가 전혀 다른 임계값이 세 축** 있다. UI 라벨까지 구분을 유지해야 한다.

| 값 | 출처 | 척도 | UI 라벨 |
|---|---|---|---|
| `presence_mv_threshold` | 캘리브레이션 산출 (Pi) | MV 스케일, 기본 2.0 | **"움직임 임계값"** |
| `wander_baseline` | 캘리브레이션 산출 (Pi) | Welch PSD 스케일, 기본 0.5 | **"재실 baseline"** |
| `wander_ratio_threshold` | 설정 (Pi) | **baseline 대비 배수** 1~5, 기본 1.8 | "WANDER 비율 임계값" |
| `threshold` = **0.468** | 모델 (클라우드) | **확률** 0~1 | **"판정 임계값"** / **"낙상 확률 임계값"** |
| `PipelineConfig.wander_threshold` | 목업 전용 | **절대값** 0~1 | (목업 폴백에서만 노출) |

> 목업의 `wander_threshold`(절대값)와 실백엔드의 `wander_ratio_threshold`(배수)는 **이름이 비슷해도 서로 변환되지 않는 별개의 숫자**다. 기능명세서 §1.2가 명시적으로 경고하는 사항이다.

DL 모델이 로드되지 않은 경우 낙상 관련 필드를 **잠금 처리**한다(백엔드가 409 반환).

### 5.2 `api/realtime/useLiveStream.ts`

**`/ws/live` 페이로드 계약**:

```ts
interface LiveSample {
  // 항상 존재
  t: number; connected: boolean; hz_1s: number | null;
  rssi: number | null; buffered_seconds: number;
  amp_mean: number | null; amp_std: number | null;

  // 재실 — 엣지가 연결되어 있으면 항상
  presence_state: "present" | "absent" | null;
  mv_current: number; presence_mv_threshold: number;
  wander_current: number; wander_baseline: number;
  wander_ratio_threshold: number; wander_ratio: number;
  wander_confirmed: boolean;
  last_activity_at: number; presence_just_changed: boolean;

  // 낙상 — 모델이 로드된 경우에만 추가
  detect_state?: "IDLE" | "SUSPECT" | "FALL" | "COOLDOWN";
  proba_fall?: number; threshold?: number;
  fall_count?: number; last_fall_time?: number | null;
}
```

**낙상 필드의 부재는 "낙상 없음"이 아니라 "낙상 감지가 동작하지 않음"이다.** 두 상태를 UI에서 반드시 구별해야 한다 (§4-2).

### 5.3 `FallAlarmModal`

- `detect_state === "FALL"` 진입 시 전체화면 모달
- 응답은 **"응급 출동 확인"(DISPATCHED) / "오탐지 처리"(FALSE_ALARM)** 두 가지
- **자동 타임아웃 없음** — 사람이 명시적으로 응답할 때까지 열려 있다. (이전에 30초 후 `PENDING`으로 남기고 자동으로 닫히던 동작은 제거되었다. 안전 기능이므로 이 정책을 유지한다.)
- `AuthGate`의 인증 셸 안에 **항상 마운트**되어 어느 페이지에서도 동작한다

FACILITY 확장 시: 다기기 동시 낙상 처리, 담당자 배정 표시, 에스컬레이션 단계 표시가 추가된다.

### 5.4 `mock-store.ts` — 개발용 폴백으로 격리

버리지 않는다. 백엔드 없이 UI를 개발·시연할 수 있는 자산이다.

| 유지 | 격리 |
|---|---|
| 시드 데이터 (시설 1, 기기 8, 거주자 6, 계정 3, 수신자 5) | `VITE_USE_MOCK=1`에서만 로드 |
| `tick()` 시뮬레이션 | 동상. 모듈 최상위 무조건 실행 제거 |
| 캘리브레이션 목 타이머 | 유지 — 실백엔드와 같은 필드에 쓰므로 렌더 로직 공유 |
| `simulateFall()` | 유지 — 알람 UI 검증용. 실서비스에서도 유용할 수 있으나 권한 제한 필요 |
| 도메인 타입 | → `lib/domain.ts`로 분리 |

**주의**: 현재 `tick()`은 `backendDrivenResidentId`(실백엔드가 구동 중인 거주자)를 시뮬레이션에서 제외한다. 실데이터 전환 후에는 이 개념이 불필요해지지만, 목업 폴백 모드에서는 여전히 의미가 있으므로 격리된 코드 안에 남긴다.

### 5.5 SSR 오류 처리 — 건드리지 말 것

`src/start.ts`(요청 미들웨어)와 `src/server.ts`(fetch 래퍼)가 서버 측 오류를 잡아 `src/lib/error-page.ts`로 폴백을 렌더링한다. h3/Nitro가 핸들러 내부 throw를 불투명한 `{"unhandled":true,"message":"HTTPError"}` 500 응답으로 삼켜버리기 때문이다. `src/lib/error-capture.ts`가 전역 `error`/`unhandledrejection` 리스너로 마지막 실제 오류를 out-of-band 기록해 두어, `server.ts`가 그 삼켜진 형태를 감지했을 때 원래 스택 트레이스를 복원한다.

**이것은 특정 upstream h3 동작에 대한 우회이지 부수적 복잡도가 아니다.** SSR/오류 처리를 손댈 때 이 배관을 제거하지 말 것.

---

## 6. 현재 구현 코드에서 참고·이식할 코드

### 6.1 이식 매핑

| 현재 위치 | 신규 위치 | 변경 |
|---|---|---|
| [src/routes/](../src/routes/) 전체 (13개 라우트) | `src/routes/` | 데이터 소스만 교체. 라우팅 구조 무변경 |
| [src/components/ui/](../src/components/ui/) (48개) | 동일 | 무변경 |
| [src/lib/backend.ts](../src/lib/backend.ts) | `src/api/` 로 분해 | `BACKEND_URL` → 환경변수, 타입 → 코드젠, 훅 → TanStack Query |
| [src/lib/mock-store.ts](../src/lib/mock-store.ts) | `src/lib/mock-store.ts` (DEV 격리) + `src/lib/domain.ts` + `src/lib/format.ts` | 타입·포매터 분리, 시뮬레이션 격리 |
| [src/components/BackendDetectionBridge.tsx](../src/components/BackendDetectionBridge.tsx) | `src/components/LiveBridge.tsx` | HOME 전용 제약 제거, 다기기 |
| [src/components/AuthGate.tsx](../src/components/AuthGate.tsx) | 동일 | JWT, 죽은 `/onboarding` 분기 제거 |
| [src/components/FallAlarmModal.tsx](../src/components/FallAlarmModal.tsx) | 동일 | 다기기 대응, 에스컬레이션 표시 |
| [src/routes/train.tsx](../src/routes/train.tsx) | 동일 | 스텁 → MLflow 연동 |
| [src/lib/error-capture.ts](../src/lib/error-capture.ts), [src/lib/error-page.ts](../src/lib/error-page.ts), [src/server.ts](../src/server.ts), [src/start.ts](../src/start.ts) | 동일 | **무변경** (§5.5) |
| [package.json](../package.json), [vite.config.ts](../vite.config.ts), [components.json](../components.json), [tsconfig.json](../tsconfig.json), [bunfig.toml](../bunfig.toml) | 동일 | 무변경 |

### 6.2 유의할 코드 구조

- **`index.tsx`가 공용 컴포넌트를 내보낸다**: `Header`, `SectionTitle`, `ResponseBadge`를 다른 모든 페이지가 import한다. 626줄짜리 라우트 파일이 사실상 공용 모듈을 겸하고 있으므로, 리팩터 시 `components/`로 옮기는 것을 권장한다.
- **`devices.tsx`가 1,002줄**로 가장 무겁고 백엔드 결합이 가장 강하다. Phase 1·4의 주요 작업 지점.
- **`notifications.tsx`(637줄)**: HOME은 실제 ntfy 수신자 테이블, FACILITY는 항상 "ACTIVE"로 표시되는 장식용 SMS/Push/ARS 카드. **SMS가 실동작하게 되므로 이 장식 UI를 실기능으로 채운다.**
- **알려진 동작**: HOME 사용자는 실백엔드가 꺼져 있어도 항상 `RealNtfySection`이 보인다(폴백 목업 섹션으로 가는 분기가 도달하지 않음). 전환 시 정리.
- **`vite.config.ts`는 `@lovable.dev/vite-tanstack-config`가 대부분을 감싼다.** TanStack devtools, `tanstackStart`, `viteReact`, `tailwindcss`, `tsConfigPaths`, nitro, `@` 별칭 플러그인을 직접 추가하지 말 것 — 이미 주입되어 있다.
- **`routeTree.gen.ts`는 자동생성**이다. 절대 손으로 고치지 말 것.

### 6.3 Lovable 동기화 주의

이 브랜치는 [Lovable](https://lovable.dev)에 연결되어 있고 push가 Lovable 에디터로 동기화된다. **이미 푸시된 커밋의 force-push·리베이스·amend를 피할 것** — Lovable 측 히스토리를 재작성해 프로젝트 이력이 유실될 수 있다. 연결 브랜치는 항상 빌드 가능한 상태로 유지한다.

레포 분리 시 Lovable 연결을 새 프론트엔드 레포로 옮길지, 끊을지 결정이 필요하다(§9-4).

---

## 7. 인접 레포와의 인터페이스 계약

SSOT는 [backend 명세서 §7](WIFI-GUARD_레포명세_backend_v1.0_20260804.md)이다. 프론트가 쓰는 부분:

### 7.1 REST

`packages/contracts/api.py` → OpenAPI → `src/api/generated/`. 현행 19종 계약을 승계하므로 마이그레이션 비용이 낮다.

### 7.2 WebSocket

`/ws/live` 10Hz. 페이로드는 §5.2. **계약 원칙**: 재실 필드는 엣지 연결 시 항상, 낙상 필드는 모델 로드 시에만.

### 7.3 도메인 용어 (한국어 UI)

| 개념 | UI 표기 |
|---|---|
| MV / moving variance | **움직임 감지** (임계값·라벨), "이동 분산 (딥러닝 입력)" (신호 자체를 가리킬 때) |
| 낙상 상태 | IDLE=**대기**, SUSPECT=**의심**, FALL=**낙상**, COOLDOWN=**냉각중** |
| 재실 상태 | PRESENT=**재실**, ABSENT=**퇴실** |
| 서비스 유형 | HOME=**가정**, FACILITY=**시설** |
| FACILITY 역할 | ROOT=**시설 등록자**, MEMBER=**초대코드로 참여** |
| 응답 상태 | 대기중 / 확인함 / 출동중 / 오탐지 |

신규 문자열은 한국어로, 위 용어와 일관되게 작성한다.

---

## 8. 리스크

| # | 리스크 | 영향 | 완화 |
|---|---|---|---|
| R1 | `mock-store.ts` 전환 범위가 큼 (1,195줄이 전 화면 상태를 소유) | 회귀 다발 | 화면 단위 점진 전환. 목업 폴백을 남겨 대조 가능하게 |
| R2 | 테스트 러너 부재 | 회귀 검출 불가 | Vitest + Playwright 도입 검토 (§9-3) |
| R3 | FACILITY 실데이터 전환 시 성능 (다기기 실시간) | 렌더 병목 | 구독 단위 최적화, 가상 스크롤 |
| R4 | `bunfig.toml` 24h 공급망 가드 | 신규 패키지 즉시 도입 불가 | 릴리스 24시간 경과 대기. 긴급 시 예외 목록 추가에 **별도 합의 필요** |
| R5 | Lovable 동기화와 레포 분리 충돌 | 이력 유실 | §6.3, §9-4 |
| R6 | 낙상 필드 부재를 "낙상 없음"으로 오해 | **안전 사고** | §5.2 계약 문서화 + §4-2 명시적 경고 UI |

---

## 9. 미결정 사항

1. **FACILITY 목업 시뮬레이션을 언제 끌 것인가.** 백엔드 FACILITY 실서비스(G3)는 8~10주 규모다. 그 전까지 프론트는 목업으로 데모해야 하므로, 두 경로를 얼마나 오래 병행 유지할지 결정 필요.

2. **낙상 이력 파형의 실데이터 전환 시점.** TimescaleDB에 MV 시계열이 쌓이기 시작해야 가능하다(백엔드 Phase 2). 그 전까지는 UI에 "재구성 시각화"임을 명시할 것인지 결정.

3. **테스트 체계 도입.** 현재 러너가 없다. 데이터 계층 전면 교체 규모의 작업을 테스트 없이 하는 것은 위험하다(Vitest + Playwright). **별도 합의 사항.**

4. **Lovable 연결 처리.** 새 프론트엔드 레포로 옮길지, 끊고 순수 git으로 갈지. 옮긴다면 `.lovable/`, `src/lib/lovable-error-reporting.ts`, `@lovable.dev/vite-tanstack-config` 의존을 함께 가져가야 한다.

5. **`simulateFall()` 실서비스 노출 여부.** 알람 경로 검증에 유용하지만 실운영에서 오알람을 만들 수 있다. 관리자 전용 + 감사 로그 기록으로 남길지, 제거할지.

6. **모바일 대응.** FACILITY는 근무자 모바일 푸시·현장 확인이 전제다(`FACILITY 구상도` §3.5). 반응형으로 충분한지, 별도 모바일 앱이 필요한지.

---

## 참고 문서

- `CSI-Guard_기능정의서_HOME_v1.0_20260717.md` — F-001~F-077 기능 정의, API-01~API-20
- `CSI-Guard_기능명세서_v2.0_20260715.md` §1 (페이지별 상세, 현행 구현 기준)
- `CSI-Guard_HOME_유저플로우_v1.0_20260715.md` — 8개 사용자 시나리오
- `FACILITY 구상도.md` §3.5 (알림·대응), §3.6 (대시보드)
- `CSI-Guard_완성목표_실행계획_v1.0_20260729.md` §2.1-6 (프런트 전환), §4 (G3 다기기 UI·권한)
- [CLAUDE.md](../CLAUDE.md) — 아키텍처·컨벤션·도메인 용어
- [backend 명세서](WIFI-GUARD_레포명세_backend_v1.0_20260804.md) §7 (인터페이스 계약 SSOT)
