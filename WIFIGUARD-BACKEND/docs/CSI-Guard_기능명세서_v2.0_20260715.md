# CSI-Guard 기능명세서 v2.0

Wi-Fi CSI 기반 비접촉 낙상 감지 대시보드 · 기능 명세서 (현재 구현 기준)

버전 2.0 · 작성일 2026-07-15

## 0. 문서 개요

본 문서는 CSI-Guard 프론트엔드 대시보드(`src/`)와 가정(HOME) 서비스용 로컬 백엔드(`backend/`)의 **현재 구현 상태**를 기준으로 작성되었다. 기존 `CSI-Guard_기능명세서_v1.4.docx`(2026-07-09)는 온보딩 위저드, MQTT 실연동, `/api/*` REST 계층, SMS(Twilio/KT)·FCM/APNs·ARS 등 당시 목표 아키텍처를 이미 구현된 것처럼 서술하고 있었으나, 그 이후 실제 개발은 다른 경로로 진행되었다(실백엔드 신설·온보딩 위저드 제거·재실감지/캘리브레이션/ntfy 알람 신규 구현). 본 문서는 이 간극을 없애고 **현재 실제로 동작하는 기능**을 기준으로 다시 작성한 것이며, 필요한 부분에서 v1.4와 달라진 지점을 함께 명시한다.

**관련 문서**
- `작업명세_로컬_실시간_낙상감지_v1.0.md` — 하드웨어(ESP32-C5 송수신기), 시리얼 바이너리 프레임 프로토콜, 초기 5단계 구축 계획.
- `CSI-Guard_데이터모델_ERD_v1.0.docx` — mock-store 기준 데이터 모델을 실서비스 스키마로 승격할 때의 후보 ERD(본 문서 §2.4 "데이터베이스 연결" 참고 자료).
- `CSI-Guard_기능명세서_v1.4.docx` — 이전 버전(참고용, 본 문서가 대체).

**용어**

| 용어 | 설명 |
|---|---|
| CSI | Wi-Fi Channel State Information. 물리 채널의 위상/진폭 변화 신호. |
| MV (움직임 감지) | Moving Variance. CSI 진폭의 이동분산. 재실감지·낙상DNN 모두의 입력 신호이지만 두 파이프라인은 서로 독립적으로 각자 계산한다(§2.1). |
| Wander | 저주파(0.1–0.5Hz) 대역 에너지 기반의 미세 움직임(호흡 등) 지표. 큰 동작(MV)과 구분되는 "재실 유지" 신호. |
| 재실/PRESENT · 퇴실/ABSENT | 재실감지 상태머신의 두 상태. |
| IDLE/SUSPECT/FALL/COOLDOWN | 낙상 상태머신: 대기/의심/낙상/냉각중. |
| DNN 낙상 확률 | `DualBranchResNet`이 출력하는 낙상 softmax 확률(0~1). |
| FACILITY / HOME | 시설(다수 입소자·다수 장치, 100% mock) / 가정(단일 사용자, 실백엔드 연동 가능) 서비스 유형. |
| mock vs 실백엔드 | mock = `src/lib/mock-store.ts`의 인메모리 시뮬레이션. 실백엔드 = `backend/`의 실제 FastAPI 서버(HOME 전용). |

---

## 1. 프론트엔드

### 1.1 간단요약

스택은 TanStack Start(React 19) + TanStack Router 파일기반 라우팅이며, 서버 데이터 계층 없이 `src/lib/mock-store.ts`(`useSyncExternalStore` 기반 단일 pub-sub 스토어, 100ms `tick()` 시뮬레이션 루프)가 FACILITY와 HOME 양쪽의 화면 상태를 전부 소유한다. HOME 계정만 `src/lib/backend.ts`(HTTP+WebSocket 클라이언트, `127.0.0.1:8000`)를 통해 실제 로컬 백엔드에 연결되며, `BackendDetectionBridge` 컴포넌트가 실백엔드의 감지 결과를 mock-store 스키마로 매핑해 나머지 UI가 mock/실데이터를 구분하지 않고 그대로 렌더링하도록 한다. FACILITY는 항상 100% mock이다(실백엔드·DB·MQTT 브로커 없음).

| 페이지 | 경로 | 한 줄 요약 |
|---|---|---|
| 실시간 관제 | `/` | 현재 상태·MV·DNN 신뢰도 StatCard, 실시간 MV 그래프, 최근 낙상 이벤트 |
| 낙상 이력 | `/history` | 전체 낙상 이벤트 목록, 응답상태 필터·CSV 내보내기, 파형 상세 |
| 이벤트 로그 | `/event-log` | 시스템/장치 이벤트 로그 전체 뷰, 레벨·텍스트 검색 |
| 장치 설정 | `/devices` | 통신(Serial/MQTT) 설정, 장치 목록·상세, 캘리브레이션 실행 |
| 재실 대상 관리 | `/residents` | 거주자(입소자) 등록, 다중 기기 매핑 |
| 알림 게이트웨이 | `/notifications` | 알림 수신자 관리(FACILITY: mock SMS/Push/ARS, HOME: 실제 ntfy) |
| 알고리즘 설정 | `/config` | 움직임/wander 임계값 등 감지 파라미터 조정 |

이 외 계정 관리(`/account`), 시설 멤버 관리(`/facility-members`, FACILITY ROOT 전용), 모델 학습(`/train`, 스텁), 로그인/회원가입(`/login`,`/signup`)이 있다(§1.2 하단).

### 1.2 페이지별 기능 상세

#### 실시간 관제 (`src/routes/index.tsx`)

- **데이터 소스**: FACILITY는 `mock-store`의 `residents`/`activeResidentId`/`mvHistory`. HOME은 `useLiveStream()`(`/ws/live` 10Hz 구독, 앱 전역 공유 WebSocket 싱글턴)의 실시간 샘플.
- **화면 구성**: StatCard 4종(대상자/재실감지/낙상 감지/수신기 상태), MV vs 임계값 기준선 차트(Recharts), `EventLogPanel`, 최근 낙상 5건 테이블. FACILITY는 다중거주자 상태 그리드도 함께 표시.
- **주요 동작**: "⚠ 낙상 시뮬레이션" 버튼 → `simulateFall()` (서비스 무관, 대상자에 강제 FALL 이벤트 발생). FACILITY의 거주자 카드 클릭 → `setActiveResident()`. HOME의 장치 선택 → `setActiveDevice()`.
- **HOME↔FACILITY 차이**: HOME 사용자가 아직 실백엔드에 연결되지 않은 장치를 선택하면 mock 값으로 대체하지 않고 "아직 연결되지 않은 장치"를 명시적으로 표시한다(실데이터 원칙 유지).

#### 낙상 이력 (`src/routes/history.tsx`)

- **데이터 소스**: `mock-store`의 `falls`, 서비스 스코프(FACILITY=`facilityId`, HOME=`ownerUserId`)로 필터.
- **화면 구성**: 응답상태 필터(전체/대기중/확인함/출동중/오탐지) + CSV 내보내기(클라이언트 전용 Blob 다운로드, 서버 호출 없음). 행 클릭 시 확장되는 상세 패널에 낙상 전후 6초 파형과 상태 타임라인을 표시.
- **주의**: 이 파형은 `fall.id`로 시드된 결정론적 PRNG로 **재구성한 시각화**이며, 실제로 저장된 CSI/MV 샘플이 아니다. 현재 어떤 낙상 이벤트에도 원본 파형은 보존되지 않는다.
- **주요 동작**: 응답상태 변경 → `updateResponse(fall.id, value)`.
- **HOME↔FACILITY 차이**: 스코프 필터링 외 UI 차이 없음.

#### 이벤트 로그 (`src/routes/event-log.tsx`)

- **데이터 소스**: `useScopedLogs()` — 거주자 태그가 있는 로그는 현재 사용자 스코프에 속할 때만, 시스템 로그(거주자 미태그)는 항상 노출.
- **화면 구성**: 레벨(FALL/ERROR/WARN/INFO) 필터 + 텍스트 검색 + 테이블. 순수 읽기 전용, 상태를 변경하는 동작 없음.
- 실시간 관제 페이지의 `EventLogPanel`과 동일한 로그 소스를 공유하는 전체 뷰다.

#### 장치 설정 (`src/routes/devices.tsx`)

- **데이터 소스**: mock(`devices`,`port`,`serialBaud`,`mqttBroker`) + HOME 실백엔드(`useBackendUp`,`fetchPorts`,`useCalibrationStatusPoll`,`useMonitorStatus`). 한 장치가 실제 캘리브레이션 경로를 쓸지 여부는 `isRealDevice(d) = !isFacility && backendUp && !!d` 하나의 규칙으로 결정한다.
- **화면 구성**: 통신설정 패널(Serial 활성, **MQTT 패널은 상시 비활성화** — "로컬 시리얼 구조 확정 전까지"), HOME+백엔드 연결 시에만 보이는 진단 패널(`/monitor/status`를 시리얼/버퍼·스트림/재실루프/낙상모델 그룹으로 구조화 표시), 장치 목록·상세(RSSI/AGC/Noise/움직임 임계값/재실 baseline/MQTT topic).
- **주요 동작**: 장치 추가 시 **항상 자동으로 캘리브레이션이 트리거**된다(`upsertDevice` 직후 `startCalibration()` 또는 `startDeviceReset()`). 기존 장치의 "장치 재설정" 버튼도 동일하게 캘리브레이션을 재실행한다(§2.2 "캘리브레이션" 참고 — 이 재설정이 곧 현재 유일한 재캘리브레이션 방식이다).
- **HOME↔FACILITY 차이**: FACILITY 장치는 항상 mock 타이머로, HOME+백엔드가뜬 장치는 항상 실제 API로 캘리브레이션이 진행된다(같은 화면에서 장치별로 분기 가능). 진단 패널은 HOME 전용.

#### 재실 대상 관리 (`src/routes/residents.tsx`)

- **데이터 소스**: `mock-store`의 `residents`/`devices`, 서비스 스코프 필터.
- **화면 구성**: 거주자 목록(이름/나이/담당자/매핑 장치 칩/임계값 오버라이드/온라인 상태) + 편집 모달의 다중 기기 매핑 UI(체크박스로 장치 선택, ★로 대표 장치 지정).
- **주요 동작**: 등록/수정 → `upsertResident()`(저장 전 대표 장치가 매핑 목록에 포함되도록 정규화). 삭제 → `deleteResident()`.
- **HOME↔FACILITY 차이**: FACILITY는 자유 호실명, HOME은 고정 공간 목록(`HOME_SPACES`: 거실/침실/안방/주방/화장실/현관/복도/기타)에서 선택. 다중 기기 매핑 로직 자체는 동일.

#### 알림 게이트웨이 (`src/routes/notifications.tsx`)

- **데이터 소스**: FACILITY는 mock `recipients`. HOME은 `useNtfyRecipients()`(`GET /notify/recipients` 폴링).
- **화면 구성**: FACILITY는 입소자별 수신자 그룹 + 공용 수신자 테이블(SMS/Web·App Push/ARS 카드는 항상 "ACTIVE"로 표시되는 장식용 UI, 실제 채널 온오프 로직 없음). HOME은 `RealNtfySection` — 실제 ntfy 수신자 테이블(이름/토픽/서버/낙상알림 on-off/발송·실패·드롭 카운트/테스트 발송).
- **주요 동작**: FACILITY → `upsertRecipient()`/`deleteRecipient()`(mock). HOME → `addNtfyRecipient()`/`updateNtfyRecipient()`/`removeNtfyRecipient()`/`testNtfyRecipient()`/`testAllNtfyRecipients()`(모두 실제 백엔드 REST 호출, §2.2 "알람 ntfy" 참고).
- **알려진 현재 동작**: HOME 사용자는 실백엔드가 꺼져 있어도 항상 `RealNtfySection`이 보인다(폴백 mock 섹션으로 가는 분기가 현재 도달하지 않음). 백엔드가 꺼져 있으면 수신자 목록 폴링이 조용히 비어있는 상태로 유지된다.

#### 알고리즘 설정 (`src/routes/config.tsx`)

- **데이터 소스**: FACILITY/백엔드 미연결 시 mock `config`(`PipelineConfig`). HOME+백엔드 연결 시 `GET/POST /presence/config` + `GET/POST /detection/config`.
- **화면 구성**: 두 갈래 폼이 같은 5개 항목(움직임 감지 임계값/wander 감지 임계값/낙상 신뢰도 임계값/재감지 lockout/퇴실 판단 대기시간)을 다루지만 **척도가 다르다** — 예를 들어 mock의 `wander_threshold`는 절대값(0~1)인 반면 실백엔드의 `wander_ratio_threshold`는 baseline 대비 배수(1~5)다. 두 값은 이름은 비슷해도 서로 변환되지 않는 별개의 숫자이므로 문서·UI 모두에서 구분해 표기해야 한다.
- **주요 동작**: mock → `updateConfig()`. 실백엔드 → `updatePresenceConfig()` + (DL 모델이 로드되어 있을 때만) `updateDetectionConfig()`.
- **권한**: FACILITY MEMBER에게는 경고 배너가 뜨지만 "Apply" 버튼 자체를 막지는 않는다(강제 RBAC 없음, 현재 동작 그대로 기술).

#### 그 외 페이지

- **시설 멤버 관리** (`facility-members.tsx`, FACILITY ROOT 전용): 초대코드 재발급(`regenerateInviteCode`), 멤버 제거(`removeMember`). 라우트 레벨에서 `role !== "ROOT"`이면 즉시 `/`로 리다이렉트하는 가드가 있어, 사이드바에 링크가 없어도 URL 직접 접근이 차단된다.
- **계정 관리** (`account.tsx`): 프로필/비밀번호 변경(`updateAccount`), 로그아웃.
- **모델 학습** (`train.tsx`): "Coming Soon" 정적 스텁 확인됨 — 데이터/상태 연동 없음. 실제 모델 학습은 `Window3BestModelInference/scripts/`에서 완전히 오프라인으로만 수행되며 이 화면과 연동되어 있지 않다(§2.4 "딥러닝 AIOps" 참고).
- **로그인/회원가입** (`login.tsx`/`signup.tsx`): 이메일/비밀번호 인증(mock), FACILITY 가입 시 신규등록(Root, 초대코드 자동발급)/기존시설참여(Member, 초대코드 입력) 분기. **가입 완료 후 강제 온보딩 위저드로 이동하지 않는다** — 안내 토스트와 함께 대시보드로 이동하며, 거주자/장치 등록은 사이드바에서 사용자가 원할 때 시작한다.

#### 공통 컴포넌트

- **`AuthGate`** (`src/components/AuthGate.tsx`): 세션을 `localStorage`에서 hydrate 후 `PUBLIC_PATHS`(`/login`,`/signup`) 기준으로 리다이렉트. 로그인 상태에서만 `AppSidebar`/`FallAlarmModal`/`BackendDetectionBridge`를 렌더링(항상 마운트되어 어느 페이지에서도 알람·백엔드 브릿지가 동작). 코드에 `/onboarding` 경로 체크가 남아있지만 해당 라우트 자체가 존재하지 않아 항상 거짓으로 평가되는 죽은 코드다(제거된 온보딩 위저드의 잔재).
- **`BackendDetectionBridge`**: HOME 계정에서만 동작. 실백엔드의 `/ws/live` 샘플을 구독해 `setBackendConnected()`로 연결 상태를 갱신하고, `applyBackendDetection()`으로 감지 결과(상태/확률/재실/wander/MV)를 해당 사용자의 대표 거주자 레코드에 매핑한다 — mock 시뮬레이션이 만들던 것과 동일한 모양의 데이터를 실측치로 채워 넣는 방식이라 나머지 UI는 mock/실데이터를 구분할 필요가 없다.
- **`FallAlarmModal`**: `alarm`이 있을 때만 전체화면 모달로 표시, "응급 출동 확인"/"오탐지 처리" 두 응답만 존재. **오늘(2026-07-15) 커밋(`57d2064`)으로 30초 자동 타임아웃이 제거**되어, 이제 사람이 명시적으로 응답하기 전까지 모달이 계속 열려 있다(이전에는 30초 후 응답을 `PENDING`으로 남긴 채 자동으로 닫혔다).
- **`AppSidebar`**: 실시간 관제/낙상 이력/이벤트 로그/장치 설정/재실 대상 관리(+시설 멤버, FACILITY ROOT만)/알림 게이트웨이/알고리즘 설정/모델 학습 내비게이션. 상단 상태등은 FACILITY는 `running`(mock 시뮬레이션 on/off), HOME은 `backendConnected`(실백엔드 연결 여부)를 각각 독립적으로 반영한다.

---

## 2. 백엔드 (`backend/`)

가정(HOME) 서비스 전용 실제 로컬 FastAPI 서버. 물리 ESP32-C5 수신기를 시리얼로 읽어 CSI 신호처리를 수행하고 `127.0.0.1:8000`에서 HTTP/WebSocket으로 서빙한다. FACILITY 계정과는 전혀 연결되지 않는다.

### 2.1 아키텍처 간단요약

```
[USB 시리얼 921600bps] → SerialReader(자동 포트탐지, 1초 자동 재연결)
                              → RingBuffer(30초)
                                   ├─→ PresenceLoop   (항상 기동, 0.25s stride)  → 움직임/wander/재실
                                   └─→ FallDetector    (체크포인트 로드 성공 시만) → 낙상 DNN 확률/상태
                              → /ws/live (10Hz, 두 결과 병합)
                                   ├─→ FALL 확정 시 → notifier.py → ntfy.sh → 휴대폰 앱
                                   └─→ 프론트엔드 대시보드
```

재실감지(`PresenceLoop`)와 낙상감지(`FallDetector`)는 **완전히 독립된 두 스레드**로, 같은 `RingBuffer`를 각자 읽을 뿐 상태를 공유하지 않는다. 과거에는 재실감지가 `FallDetector` 내부에 있어 DL 모델 체크포인트가 없으면(`--no-model` 등) 움직임/재실 출력까지 함께 죽는 문제가 있었고, 이를 분리한 뒤로 재실감지는 시리얼 수신기만 붙어 있으면 모델 로드 여부와 무관하게 항상 동작한다. `/ws/live`도 이 원칙을 그대로 반영해 재실 필드(`presence_state`,`mv_current`,`wander_current` 등)는 연결만 되면 항상 존재하고, 낙상 필드(`proba_fall`,`detect_state` 등)는 모델이 로드된 경우에만 추가된다.

### 2.2 기능별 상세

#### 캘리브레이션 (`backend/onboarding.py`)

장치 추가 시(최초 1회) 또는 "장치 재설정" 클릭 시(재캘리브레이션) 동일하게 실행되는 4단계 절차. 별도의 "재캘리브레이션 전용" 엔드포인트는 없고 `POST /onboarding/calibrate/start`를 그대로 재사용하며, 과거 캘리브레이션 이력은 보관하지 않고 새 결과로 덮어쓴다.

| 단계 | 시간 | 내용 |
|---|---|---|
| leaving | 30s | 설치자가 공간을 비울 시간(대기 후 시리얼로 `"train"` 명령 전송 — 수신기 펌웨어의 `csi_recv_calibrate` 처리 전제) |
| waiting_ack | ~0.2s | `packet_count`가 멈춘 것을 확인(펌웨어가 조용해짐 = 명령 수신 확인) |
| waiting_agc | ~1s | `packet_count`가 다시 증가하기 시작하는 것을 확인(펌웨어 AGC 안정화 재개) |
| measuring | 30s | 조용한 공간에서 30초 baseline 윈도우 수집 |

총 소요시간 약 61초(코드 주석에 의도적으로 압축하지 않는다고 명시). 완료 시 baseline 윈도우로부터 두 값을 계산한다: `presence_mv_threshold`는 IQR 이상치 제거 후 `mean + k·std`(k=2.0, 최소 0.3으로 floor), `wander_baseline`은 동일 윈도우의 Welch band-energy 값(최소 0.05로 floor). 두 값 모두 살아있는 `PresenceConfig` 인스턴스에 즉시 반영되어 재시작 없이 적용된다. 실패 시 무조건 `phase="error"`로 귀결되어 재시도가 영구히 막히지 않는다.

오늘(2026-07-15) 커밋 `fe5e26f`에서 `leave_wait_s`를 10s→30s, `baseline_window_s`를 20s→30s로 늘려 총 소요시간이 약 31s→61s로 변경되었고, MV 이상치 제거(IQR)와 3샘플 스무딩이 함께 추가되었다 — 재캘리브레이션 튜닝 작업의 결과다.

#### 재실감지 (`backend/presence_loop.py`, `backend/presence/`)

공통 전처리 파이프라인(리샘플 → 밴드패스 → 상위 10개 서브캐리어 선택(q-value 기준) → 합산·정규화, `compute_final_signal()`)을 거친 뒤 두 개의 독립적인 신호로 갈라진다.

**움직임감지(MV)** — 3초 윈도우, moving-variance. `presence_mv_threshold`(캘리브레이션 산출, 기본 2.0)를 넘으면 활동으로 간주한다. **ABSENT→PRESENT 전환은 오직 이 신호만으로 발생한다.**

**wander 감지** — 10초 윈도우, Welch band-energy를 좁은 측정 대역(0.1–0.5Hz)으로 계산하되 사전 필터는 그보다 넓게(0.05–5Hz) 잡는다(같게 만들면 정규화 후 구분력이 사라짐이 실측으로 확인됨). `wander_ratio = wander_current / wander_baseline`이 임계값(기본 1.8) 이상을 `wander_min_duration_s`(2초) 동안 연속 유지하면 `wander_confirmed`. **오늘(`fe5e26f`) 기준으로 wander는 이미 PRESENT인 상태를 "연장"만 할 수 있고, ABSENT→PRESENT로 새로 전환시키지는 못한다** — 문이 열리거나 바람이 스치는 등의 순간적 노이즈가 재실로 오탐되는 것을 막기 위한 변경이다.

`PresenceDetector` 상태머신은 PRESENT/ABSENT 두 상태만 가지며, 마지막 활동 시각으로부터 `presence_timeout_s`(오늘 6s→10s로 조정) 동안 활동이 없으면 ABSENT로 전환한다. 두 신호의 임계값·타임아웃은 모두 `POST /presence/config`로 실시간 조정 가능(재시작 불필요) — 단 `wander_baseline`은 API로 직접 덮어쓸 수 없고 캘리브레이션으로만 갱신된다.

#### 낙상 DNN 모델 (`backend/detector.py`)

0.25초 스트라이드로 3초 윈도우를 반복 추론한다. 원시 CSI에서 S3 스칼로그램(224×224 이미지형 피처)과 PCA-ACF(1×128×64, lag 0.4초 자기상관 기반) 두 종류의 피처를 추출하고(`backend/features/`, 상위 30개 서브캐리어를 PCA 기반으로 선택 — 재실감지의 q-value 기반 상위 10개 선택과는 별개의 절차), `DualBranchResNet`(ResNet-18 이중분기, 체크포인트 `Window3BestModelInference/weights/best_model.pt`)에 넣어 softmax 낙상확률을 얻는다.

판정: 확률이 임계값(기본 0.468) 이상이면 raw positive, 최근 5개 raw 예측의 다수결(majority)이 positive면 `FALL` 확정. 원래 검증된 후처리안(mode5)은 중심 윈도우 기준이라 미래 데이터 2개가 필요했으나, 실시간 환경에서는 그럴 수 없어 **인과적(causal) 다수결**로 대체했다 — 이 방식의 성능이 원안과 동등한지는 연구단 확인 대기 중인 오픈 아이템이다. 상태머신은 IDLE→SUSPECT→FALL→COOLDOWN(10초)이며, COOLDOWN이 곧 "같은 낙상에 대해 ntfy가 중복 발송되지 않게 하는" 역할도 겸한다. 측정 지연은 피처추출+추론 합쳐 약 42ms/window로 250ms 스트라이드 예산에 여유가 있다.

#### 알람 ntfy (`backend/notifier.py`)

낙상 상태머신이 `FALL`로 전이하는 순간(`_emit_fall`) 등록된 모든 ntfy 수신자에게 병렬로 알림을 발송한다. 수신자마다 독립된 스레드+큐(최대 32건)를 가져 발송 지연이 0.25초 감지 루프를 막지 않는다. 실패 시 최대 3회 시도, 백오프 1초/2초, 최종 실패는 카운트만 남기고 포기(재시작 후 재시도 없음). 메시지는 한국어 고정 포맷("낙상이 감지되었습니다\n시각: …\n확률: …%"). 다중 수신자 CRUD(`/notify/recipients/*`)는 커밋 `74d20f0`에서 추가되었고, 그 이전부터 있던 `--ntfy-topic`/`NTFY_TOPIC` 환경변수는 하위호환을 위해 시작 시 수신자 1명을 자동 등록하는 용도로만 남아있다.

**중요한 구조적 사실**: 실제 휴대폰 푸시는 `FallDetector → notifier.py → ntfy.sh(또는 자체호스트) → 휴대폰 ntfy 앱` 경로로 전달되며, **프론트엔드/백엔드의 HTTP·WebSocket을 전혀 거치지 않는다.** 반면 앱 내부의 `FallAlarmModal`은 `/ws/live`의 `detect_state` 필드를 직접 구독해 여닫히는 완전히 별개의 경로다. 두 알람 경로는 "낙상 상태머신이 FALL로 전이한다"는 같은 트리거 조건을 공유할 뿐, 코드상으로는 서로 연결되어 있지 않다.

### 2.3 API 명세

`backend/main.py`의 단일 FastAPI 앱에 라우터 분리 없이 전부 선언되어 있다. 총 19개 REST 엔드포인트 + 1개 WebSocket.

| Method | Path | 설명 |
|---|---|---|
| GET | `/` | 서비스 식별 |
| GET | `/ports` | 사용 가능한 시리얼 포트 목록 |
| GET | `/monitor/status` | 시리얼+버퍼+감지+재실+알림 통합 상태 |
| POST | `/monitor/start` | 시리얼 리더 (재)시작 |
| POST | `/monitor/stop` | 시리얼 리더 중지 |
| GET | `/monitor/detect` | 낙상 감지기 상태 + 최근 60초 확률 히스토리 |
| GET | `/monitor/window` | 최근 N초 원시 CSI 요약 통계 |
| POST | `/notify/test` | 등록된 전체 ntfy 수신자에게 테스트 발송 |
| GET | `/notify/recipients` | ntfy 수신자 목록 |
| POST | `/notify/recipients` | ntfy 수신자 추가 |
| PATCH | `/notify/recipients/{id}` | 수신자의 낙상알림 on/off 토글 |
| DELETE | `/notify/recipients/{id}` | 수신자 삭제 |
| POST | `/notify/recipients/{id}/test` | 특정 수신자에게 테스트 발송 |
| POST | `/onboarding/calibrate/start` | 캘리브레이션 시작(재캘리브레이션도 동일 엔드포인트) |
| GET | `/onboarding/calibrate/status` | 캘리브레이션 진행 상태 조회 |
| GET | `/presence/config` | 재실감지 파라미터 조회 |
| POST | `/presence/config` | 재실감지 파라미터 갱신(움직임/wander 임계값, 퇴실 타임아웃) |
| GET | `/detection/config` | 낙상 DNN 파라미터 조회 |
| POST | `/detection/config` | 낙상 DNN 파라미터 갱신(임계값, cooldown — 모델 미로드 시 409) |
| WS | `/ws/live` | 10Hz 통합 실시간 텔레메트리(재실 필드는 항상, 낙상 필드는 모델 로드시만) |

참고로 프론트엔드의 FACILITY/mock 경로는 이런 REST 계층 자체가 없으며, `mock-store.ts`의 함수를 컴포넌트에서 직접 호출하는 구조다. v1.4에 서술되었던 `/api/devices`,`/api/falls` 등의 REST 엔드포인트는 실재하지 않는다.

### 2.4 앞으로 구현 예정

- **자동/주기적 재캘리브레이션**: 현재 재캘리브레이션은 사용자가 "장치 재설정"을 직접 클릭해야만 실행되는 수동 절차뿐이다. 환경 변화(가구 배치, RF 간섭 등)로 인한 드리프트를 자동으로 감지해 재측정을 제안·실행하는 기능은 아직 없다.
- **facility 서비스 기능 추가**: FACILITY는 현재 100% 인메모리 mock이며 실백엔드·DB·MQTT가 전혀 없다. HOME과 동등한 실감지 파이프라인을 다수 기기·다수 입소자 규모로 확장하는 것이 과제이며, 아래 세 항목(DB/MQTT/클라우드)과 직접 맞물린다.
- **데이터베이스 연결**: 현재 새로고침 시 세션을 제외한 모든 상태가 초기화되며 영속 저장이 전혀 없다. 후보 스키마(서비스 XOR 스코프, 거주자 다중기기 매핑 등)는 이미 `CSI-Guard_데이터모델_ERD_v1.0.docx`에 설계되어 있어 이를 실제 DB로 승격하는 작업이 남아 있다.
- **MQTT 기기 연결**: `devices.tsx`에 UI(통신설정 패널의 MQTT 섹션)는 이미 존재하지만 "로컬 시리얼 구조 확정 전까지" 상시 비활성화되어 있고, 실제 MQTT 클라이언트/브로커 연동은 없다(의존성에도 mqtt 라이브러리 없음).
- **클라우드 배포**: 현재 전 과정이 로컬(127.0.0.1) 전용이며 Dockerfile·CI 설정이 없다. 이는 "외부 서버·클라우드 DB를 쓰지 않는다"는 로컬 우선 원칙에 따른 의도된 상태이지만, 다수 가정/시설을 원격으로 지원하려면 필요해진다.
- **딥러닝 AIOps(자가학습)**: `/train` 페이지는 프론트엔드 전용 정적 스텁이며 대응하는 백엔드 엔드포인트가 아예 없다. 실제 모델 학습은 `Window3BestModelInference/scripts/`에서 완전히 오프라인으로만 이루어지고 구동 중인 앱과 연동되어 있지 않다. 오탐(FALSE_ALARM) 응답을 학습 데이터로 자동 반영해 재학습하는 파이프라인은 아직 구현되지 않았다.
