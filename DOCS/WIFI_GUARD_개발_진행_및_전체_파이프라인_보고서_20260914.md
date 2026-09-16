# WIFI GUARD 개발 진행 결과 및 전체 파이프라인 보고서

공유용 기술 현황

작성 기준 2026년 9월 14일

대상 프로젝트 관계자 및 개발팀

## 보고 목적과 결론

이 보고서는 ESP32 C5 기반 CSI 수집 장치부터 Raspberry Pi 엣지 처리, MQTT와 Kafka 적재,
백엔드와 웹 화면, 낙상 모델까지의 전체 구조와 현재 검증 수준을 공유하기 위해 작성했다.

현재 가장 중요한 결론은 다음과 같다. **실제 장치의 320 Hz CSI를 Batch8 SPI로 Raspberry Pi가
읽고 MQTT로 발행해 Kafka까지 적재하는 경로는 동작한다.** 통합 레포에서도 하드웨어 없이
Raspberry 합성 replay와 계약 메시지를 MQTT에서 Kafka로 흘려 저장하고 다시 읽는 검증을 통과했다.
기존 REST API와 WebSocket, 재실감지, 프론트 화면 기능도 통합 과정에서 보존했다.

다만 실제 낙상 판정은 아직 운영 완료 상태가 아니다. 학습과 오프라인 예측 코드는 들어왔지만,
승인된 운영 체크포인트와 Kafka 실시간 추론 consumer를 연결하지 않았다. 따라서 현재 시스템은
수집과 전송, Kafka 적재, 재실 데이터 처리까지 검증됐으며 실제 낙상 확률 산출과 알림은 다음 단계다.

## 현재 상태 요약

| 영역 | 상태 | 근거 |
|---|---|---|
| ESP32 C5 송수신과 Batch8 SPI | 완료 | 30초 실기에서 19,268프레임, 관측률 320.014 Hz |
| Raspberry Pi 엣지 수집과 MQTT 발행 | 완료 | MQTT 발행 168건, transport error 0 |
| 통합 MQTT에서 Kafka 적재 | 완료 | 계약 메시지 3건과 합성 replay 85건을 Kafka에서 재조회 |
| 재실감지와 서비스 API 및 웹 | 구현 및 회귀 검증 완료 | Backend pytest 77, Frontend Vitest 34와 빌드 검사 통과 |
| CSI 낙상 학습과 오프라인 예측 | 구현 및 합성 검증 완료 | 모델 패키지 pytest 21, 합성 학습과 예측 통과 |
| 실시간 낙상 추론과 알림 | 미완료 | 운영 체크포인트와 Kafka inference consumer 미연결 |
| 장시간 안정성 시험 | 미완료 | 최신 자동 재동기화 버전은 30초 실기까지만 검증 |

## 전체 파이프라인

### 현재 목표 구조

```text
ESP32 C5 송신기
  UDP CSI 트리거 320 Hz 채널 48
        ↓
ESP32 C5 수신기
  Wi Fi CSI 수집과 WGSP v1 Batch8 구성
        ↓  SPI mode 0 6 MHz 4608 bytes
Raspberry Pi 엣지 에이전트
  프레임 검증과 버퍼링
  재실감지 0.25초 주기
  서브캐리어 선택과 PCA 기반 1차원 대표신호 생성
        ↓  MQTT
wifiguard tenant device presence
wifiguard tenant device signal
wifiguard tenant device telemetry
        ↓
MQTT Kafka 브리지
  토픽과 페이로드 신원 검증
        ↓
Kafka
  csi feature stream
  csi telemetry
  csi inference result
        ↓
Backend
  재실 적재와 최신값 캐시
  CWT와 ACF 피처 변환
  낙상 확률 추론과 상태 판정
  REST API와 WebSocket
        ↓
Frontend
  기기 연결 상태와 재실 상태
  낙상 이벤트와 알림 상태
```

### 단계별 책임과 검증 상태

| 단계 | 구성요소 | 책임 | 현재 검증 |
|---:|---|---|---|
| 1 | ESP32 C5 송신기 | 채널 48에서 초당 320회의 CSI 트리거 생성 | 펌웨어 빌드 및 실기 수신율 확인 |
| 2 | ESP32 C5 수신기 | CSI callback 수집, 576바이트 WGSP 프레임 8개 준비 | Batch8 펌웨어 플래시 및 실기 확인 |
| 3 | Raspberry Pi | READY 감지 후 4608바이트 SPI 읽기와 프레임 검증 | direct ioctl 실기와 자동 재동기화 확인 |
| 4 | 엣지 처리 | 재실 상태와 1차원 대표신호, telemetry 생성 | 단위 테스트와 replay 검증 |
| 5 | MQTT | 기기별 presence signal telemetry 발행 | 실제 장치 발행 및 합성 발행 확인 |
| 6 | 브리지와 Kafka | 메시지 계약 검증, Kafka 토픽 라우팅 | 통합 레포에서 88건 재조회 검증 |
| 7 | Backend 저장과 실시간 제공 | DB 적재, 최신값 캐시, REST와 WebSocket 제공 | 기존 로컬 replay와 Backend 테스트로 검증 |
| 8 | 낙상 모델 | CWT와 ACF 생성, 확률 추론, 상태 전이 | 오프라인 코드만 검증, 실시간 consumer 미연결 |
| 9 | Frontend | 연결과 재실, 낙상 상태 표시 | 테스트와 typecheck, build, lint 통과 |

## 이번 작업에서 완료한 내용

### SPI 전송 안정화

초기 단일 프레임 전송은 한 번의 사용자 요청보다 더 많은 프레임이 소비되거나, 연속 전송에서
0 바이트와 잘못된 magic이 반복되는 문제가 있었다. READY와 CS 배선, SPI open 시점,
Python spidev API별 동작을 단계별로 분리해 확인했다.

최종 전송 계약은 다음과 같다.

| 항목 | 확정값 |
|---|---|
| 프로토콜 | WGSP v1 |
| 프레임 크기 | 576바이트 |
| 배치 크기 | 8프레임 |
| SPI 트랜잭션 | 4608바이트 단일 트랜잭션 |
| SPI 설정 | mode 0, 6 MHz |
| Raspberry 장치 | `/dev/spidev0.0` |
| Raspberry CS | BCM8, 물리 핀 24, CE0 |
| 수신기 CS | ESP32 C5 GPIO23 |
| READY | Raspberry BCM25, active high, pull down |
| spidev 버퍼 | 8192바이트 |
| Linux 전송 API | `SPI_IOC_MESSAGE(1)` direct ioctl |
| 복구 방식 | 전체 배치가 무효면 fd를 닫고 0.05초 뒤 재개방 |

이 과정에서 Batch8 수신기 펌웨어와 Raspberry 에이전트를 맞췄고, safe open과 lazy open,
자동 protocol resync를 반영한 엣지 버전 `2.10.2-batch8-auto-resync`까지 배포했다.

### 레포 통합

기존 MVP 레포의 기능을 유지하면서 다음 네 영역으로 정리했다.

| 레포 | 포함 기능 |
|---|---|
| WIFIGUARD ESP | 기존 UART 펌웨어, 320 Hz 송신기, Batch8 SPI 수신기 |
| WIFIGUARD RASPBERRY | SPI와 UART, replay 수집기, 재실감지, 신호 생성, MQTT |
| WIFIGUARD BACKEND | 계약 패키지, MQTT Kafka ingest, API, DB, WebSocket, 모델 패키지 |
| WIFIGUARD FRONTEND | 기존 화면과 라우트, REST 연동, 실시간 WebSocket 파서 |

통합 중 `FeatureLoop`, `PresenceLoop`, `SerialReader`가 Python `Thread`의 내부 `_stop()` 메서드를
이벤트 객체로 덮어써 정상 종료 시 `join()`이 실패하는 문제를 발견했다. 이벤트 이름을
`_stop_event`로 바꾸고 회귀 테스트를 추가했다.

### 모델 파이프라인 편입

전달받은 CSI 낙상 파이프라인을 Backend의 독립 서비스 패키지로 넣고, raw CSI 전처리,
CWT와 ACF 피처 생성, 학습, 체크포인트 저장, 오프라인 예측을 실행할 수 있게 정리했다.
합성 raw CSI로 학습부터 예측 파일 생성까지 실행했고 패키지 테스트도 통과했다.

엣지는 224 x 224 CWT와 1 x 128 x 64 ACF 텐서를 만들지 않는다. Raspberry Pi에서는
서브캐리어 선택과 PCA 기반 1차원 float32 대표신호까지만 만들고, 무거운 변환과 추론은
Backend 모델 서비스가 담당하도록 경계를 정했다. 이 구조는 엣지 연산량과 업링크 크기를 줄이면서
학습 입력 계약을 유지한다.

## 검증 결과

### 실제 장치 30초 시험

최신 Batch8 자동 재동기화 경로의 30초 시험 결과는 다음과 같다.

| 지표 | 결과 | 판정 |
|---|---:|---|
| SPI 프레임 | 19,268 | 목표 9,000 이상 충족 |
| 관측 소스 속도 | 320.014 Hz | 목표 300에서 340 Hz 충족 |
| MQTT publications | 168 | 발행 확인 |
| source sequence gap | 5프레임 | gap ratio 약 0.026퍼센트 |
| SPI dropped total | 0 | 충족 |
| transport errors | 0 | 충족 |
| protocol rejects | 4 | 엄격 기준 2건 이하 미달 |
| window preparation errors | 2 | 추가 안정화 대상 |

이 시험은 수집률과 발행, Kafka offset 증가를 확인했다. 엄격 quality gate는 protocol reject가
4건이어서 실패로 기록됐다. 데이터 경로가 작동한다는 근거는 충분하지만, 장시간 운영 승인을
내리기 전에 reject 원인과 재동기화 동작을 soak 시험으로 확인해야 한다.

### 통합 레포 모의 적재 시험

실제 장치를 사용하지 않고 현재 통합 소스의 Raspberry replay와 Backend 브리지를 실행했다.
매 실행마다 새 tenant와 device UUID를 사용하고, Kafka에서 같은 UUID의 레코드를 다시 읽어
Pydantic 계약으로 검증했다.

| 시험 | Kafka 결과 | 브리지 결과 |
|---|---|---|
| 계약 메시지 직접 주입 | presence 1, signal 1, telemetry 1 | forwarded 3, rejected 0, errors 0 |
| Raspberry 합성 replay 12초 | presence 46, signal 31, telemetry 8 | forwarded 85, rejected 0, errors 0 |

Kafka offset은 계약 시험에서 `csi-feature-stream` 2건과 `csi-telemetry` 1건 증가했다.
replay 시험에서는 각각 77건과 8건 증가했다. 총 88건 모두 Kafka에서 재조회됐다.

### 레포별 회귀 검사

| 대상 | 결과 |
|---|---|
| ESP32 C5 Batch8 수신기 | ESP IDF 6.0.2 빌드 통과 |
| ESP32 C5 320 Hz 송신기 | ESP IDF 6.0.2 빌드 통과 |
| Raspberry Pi 패키지 | pytest 89 통과, 하드웨어 선택 테스트 2 skip |
| Backend | PostgreSQL 16 격리 DB 기준 pytest 77 통과 |
| Frontend | Vitest 34, typecheck, production build, lint 통과 |
| CSI 낙상 파이프라인 | pytest 21 통과, 선택 테스트 2 skip |
| 합성 raw CSI 데모 | 데이터 생성부터 학습과 예측 파일 생성까지 통과 |

## 현재 동작 범위와 해석

### 확인된 기능

1. ESP32 C5 수신기는 실제 CSI를 Batch8 프레임으로 준비한다.
2. Raspberry Pi는 READY 신호를 감지하고 배치를 읽어 유효한 WGSP 프레임으로 해석한다.
3. 엣지는 재실과 signal, telemetry MQTT 메시지를 생성한다.
4. 브리지는 메시지 신원과 스키마를 검사한 뒤 Kafka로 전달한다.
5. 통합 레포의 모의 데이터는 Kafka에 저장되고 동일 레코드로 재조회된다.
6. 기존 Backend API와 WebSocket, Frontend 기능은 회귀 테스트를 통과한다.

### 아직 완료로 표시하면 안 되는 기능

1. 실제 학습 데이터로 승인한 운영 체크포인트와 실시간 inference consumer 연결
2. `csi-inference-result`를 낙상 상태머신과 `fall_events` 저장으로 잇는 처리
3. 실제 알림 발송과 수신자 테스트 API
4. 최신 Batch8 경로를 통합 Backend의 TimescaleDB와 화면까지 연결한 실기 재검증
5. 네트워크 단절 시 엣지 spool과 복구
6. SPI 경로에서 수신기로 보내는 `train` 캘리브레이션 명령
7. 10분 이상 soak와 전원 및 네트워크 fault injection

현재 화면에서 `fall` 값이 비어 있으면 낙상이 없다는 의미가 아니다. 이는 낙상 추론이 동작하지
않는 상태를 뜻한다. 이 구분은 운영 화면과 API에서 유지해야 한다.

## 리스크와 운영 조건

| 리스크 | 현재 관측 | 조치 |
|---|---|---|
| 간헐적 protocol reject | 30초 시험에서 4건 | 원인 로그 강화와 장시간 soak 수행 |
| Raspberry 전원 상태 | 여러 시험에서 `0x80000`과 `0x80008` 이력 | 안정적인 전원 공급과 온도 모니터링 |
| 모델 처리량 | 다기기 4 Hz에서 CWT와 ACF가 병목 가능 | 단계별 프로파일링 후 벡터화 |
| 운영 체크포인트 | 참고 가중치와 운영 승인 모델의 경계가 불명확 | 모델 버전과 입력 계약, 승인 기준 확정 |
| 클라우드 자원 | 현재 AWS 인스턴스와 EBS 없음 | 새 환경 구축 후 secret과 DB host 갱신 |
| 외부 모델 코드 라이선스 | 전달 압축본에 별도 LICENSE 없음 | 외부 배포 전 출처와 라이선스 확인 |

## 다음 작업 순서

1. **실시간 추론 연결**

   `csi-feature-stream`의 signal을 모델 서비스가 읽어 CWT와 ACF를 만들고, 승인 체크포인트로
   확률을 계산해 `csi-inference-result`에 발행한다.

2. **낙상 이벤트와 알림 연결**

   Backend가 확률 시퀀스를 상태로 판정하고 `fall_events`를 저장한 뒤 WebSocket과 알림으로 전달한다.

3. **통합 DB 시험**

   모의 replay를 MQTT, Kafka, TimescaleDB, REST, WebSocket, Frontend까지 흘려 레코드 수와 화면 값을 대조한다.

4. **실기 장시간 시험**

   최신 수신기와 Raspberry 에이전트로 10분, 1시간 순서의 soak를 수행한다. protocol reject,
   gap, restart, 온도, throttling, Kafka offset을 함께 기록한다.

5. **머지 요청 준비**

   실제 원격 레포에서 브랜치를 만들고 ESP, Raspberry, Backend, Frontend 순서로 변경을 적용한다.
   모델 체크포인트와 라이브 consumer는 별도 MR로 분리한다.

## 재현 명령

통합 소프트웨어 회귀 검사는 다음 명령으로 실행한다.

```powershell
cd "C:\Users\rokn2\Documents\ChatGPT\mqtt+ kafka\MVP_WIFI_GUARD-main"
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\scripts\validate-repository.ps1" `
  -SkipInstall
```

하드웨어 없이 MQTT에서 Kafka 적재를 다시 확인하려면 다음 명령을 실행한다.

```powershell
cd "C:\Users\rokn2\Documents\ChatGPT\mqtt+ kafka\MVP_WIFI_GUARD-main"
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\scripts\validate-mock-storage.ps1" `
  -NetworkName "wifi-guard-streaming-toy"
```

## 최종 판단

현재 시스템은 실제 CSI 수집과 Raspberry 처리, MQTT 발행, Kafka 적재까지 실행 가능한 상태다.
통합 레포에서도 모의 데이터로 같은 저장 경로를 재검증했다. 이 상태는 데이터 수집 기반을
개발하고 Backend 및 Frontend 통합을 이어가기에 충분하다.

운영 가능한 낙상 감지 시스템으로 완료하려면 실시간 모델 consumer와 낙상 이벤트 저장,
알림 경로를 연결해야 한다. 최신 Batch8 전송도 엄격 품질 기준과 장시간 soak를 통과한 뒤
운영 승인하는 것이 적절하다.
