## 개요

목업 시뮬레이션 기반으로 인증 도입, 서비스 모델 전환(토글 → 계정 기반), MQTT/딥러닝/장치 관리 개념을 UI에 반영합니다. 백엔드 없이 in-memory mock-store를 확장합니다.

---

## 1. 인증 페이지 (목업)

**신규 라우트**

- `src/routes/login.tsx` — 이메일/비밀번호 입력 (목업, 아무 값 통과)
- `src/routes/signup.tsx` — 이메일/비밀번호/이름 + **서비스 유형 선택**(Home / Facility)
  - Facility 선택 시 추가 필드: `역할` (시설 등록(Root) / 기존 시설 참여(Member))
    - Root: 시설명, 시설코드(자동 생성)
    - Member: 초대코드 입력
- `src/routes/onboarding.tsx` — 최초 로그인 시 장치 설정 팝업 흐름(아래 5번)

**라우팅 가드**

- mock-store에 `session: { userId, name, service: 'HOME'|'FACILITY', role: 'ROOT'|'MEMBER'|'USER', facilityId?, onboarded: boolean }` 추가
- `__root.tsx`에서 세션 없으면 `/login`으로, `onboarded=false`면 `/onboarding`으로 리다이렉트
- 사이드바 **Persona 토글 제거**, 대신 로그인한 사용자 정보(이름/서비스/역할) 표시 + 로그아웃 버튼
- 서비스 유형에 따라 사이드바 메뉴 필터링:
  - HOME: 실시간관제 / 낙상이력 / 알림 / 장치설정 / 학습(옵션)
  - FACILITY: + 거주자관리, + 시설 멤버(Root만)

---

## 2. UI 문구 수정

- `MV Variance` → **`움직임 감지`**
- `Moving variance` → `이동 분산 (딥러닝 입력)`
- Threshold 라벨 → **`움직임 감지 임계값`**
- 차트 헤더 `Real-time MV Variance` → `실시간 움직임 감지`
- 관련 위치: `src/routes/index.tsx`, `src/routes/config.tsx`

---

## 3. 시설 계정 구조 (AWS IAM 유사)

**mock-store 확장**

- `facilities: { id, name, code, rootUserId }[]`
- `users: { id, email, name, service, role, facilityId? }[]`
- 시드 데이터: 데모 시설 1개 + Root 1명 + Member 2명
- 신규 라우트 `src/routes/facility-members.tsx` (Root만 접근)
  - 멤버 목록 / 초대코드 발급 / 멤버 제거

---

## 4. 딥러닝 낙상 감지 & 장치(MQTT) 모델

**mock-store 변경**

- `Resident.deviceId` → `Device` 엔티티로 승격
- `devices: Device[]`
  ```
  Device = {
    id, name, room, residentId?, mqttTopic,
    online, lastSeen,
    base_rssi, current_rssi, agc, noise_floor,
    fw_version, mac,
    calibrating: boolean, calibrationEndsAt?: number
  }
  ```
- 시뮬레이션 tick에서 rssi/agc/noise 값 소폭 변동
- Facility 그리드에서 **호실 카드 클릭 = 해당 장치 MQTT 구독 활성화** (활성 장치만 상세 차트/이벤트 스트림 표시)
  - 현재는 `activeResidentId`만 있음 → `activeDeviceId`로 리네임/추가
- 낙상 판정 로직 주석/UI를 "딥러닝 모델(CSI-NET v2) 추론 결과 + confidence" 톤으로 정리(로직 자체는 유지)
- 임계값 라벨을 **움직임 감지 임계값**으로 분리 명명 (내부는 mv_threshold 재사용)

---

## 5. 장치 설정 페이지

**신규 라우트 `src/routes/devices.tsx`**

- 장치 목록 테이블: 이름 / 호실 / MQTT 토픽 / RSSI(base→current) / AGC / Noise Floor / FW / 상태 / 마지막 통신
- 행 클릭 → 상세 패널: 실시간 rssi/noise/agc 값 + **[장치 재설정]** 버튼
- **재설정 플로우 (목업)**:
  1. 버튼 클릭 → 확인 모달 "10초간 감시 공간에서 벗어나 주세요"
  2. 10초 카운트다운 (사람이 없어야 하는 대기 시간)
  3. 이어서 10초 캘리브레이션 진행바 (base_rssi/noise_floor 재수집 시뮬)
  4. 완료 토스트, 장치 base 값 갱신
- `mock-store`에 `resetDevice(deviceId)` 액션 추가 (2단계 setTimeout으로 상태 전이)

---

## 6. 온보딩 팝업 (최초 로그인)

**컴포넌트 `src/components/OnboardingWizard.tsx`**

- 스텝1: 서비스 안내 (Home/Facility에 따라 문구 분기)
- 스텝2: 장치 등록 (MQTT 토픽/이름/호실 입력, Home은 장치 1개 강제)
- 스텝3: **장치 재설정 안내 + 10s 대기 + 10s 캘리브레이션** (5번 플로우 재사용)
- 스텝4: 완료 → `session.onboarded = true`, `/`로 이동
- `/onboarding` 라우트에서 렌더

---

## 7. 파일 변경 요약

**신규**

- `src/routes/login.tsx`, `src/routes/signup.tsx`, `src/routes/onboarding.tsx`
- `src/routes/devices.tsx`, `src/routes/facility-members.tsx`
- `src/components/OnboardingWizard.tsx`, `src/components/DeviceResetModal.tsx`
- `src/components/AuthGate.tsx` (세션/온보딩 가드)

**수정**

- `src/lib/mock-store.ts` — session/facilities/users/devices, resetDevice 액션, 라벨 정리
- `src/components/AppSidebar.tsx` — persona 토글 제거, 세션/로그아웃, 서비스별 메뉴
- `src/routes/__root.tsx` — AuthGate 삽입
- `src/routes/index.tsx` — 라벨 변경(움직임 감지), 활성 장치 개념 반영
- `src/routes/config.tsx` — 임계값 라벨 변경
- `src/routes/residents.tsx` — 장치는 devices 페이지로 분리, 매핑만 유지

**목업 유지**: 데이터는 전부 in-memory, 페이지 새로고침 시 초기화. 딥러닝/MQTT/캘리브레이션 모두 시뮬레이션.
