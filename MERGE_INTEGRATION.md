# WIFI-GUARD 통합 및 머지 인계서

작성일: 2026-09-16

## 결론

기존 4개 레포의 UART·replay·API·웹 기능을 삭제하지 않고, 실기에서 검증한
ESP32-C5 Batch8 SPI 수집 경로와 별도 CSI 낙상 학습/예측 패키지를 추가했다.

현재 코드 기준으로 펌웨어 빌드와 각 소프트웨어 테스트는 통과한다. 라이브 모델 경로도 구현되어
**승인 모델 가중치 파일만 외부에서 주입하면 되는 상태**다. 가중치가 없을 때 추론 컨테이너는
fail-closed로 기동을 거부하며, 합성값을 운영 판정으로 대신하지 않는다.

## 레포별 변경 범위

| 레포 | 추가·수정 내용 | 기존 기능 보존 |
|---|---|---|
| `WIFIGUARD-ESP` | 320Hz UDP 송신기, WGSP v1 Batch8 SPI 수신기, 공개 보드 설정과 비밀 설정 템플릿 | 기존 `csi_send`, `csi_recv`, 보정 및 라우터 데모 유지 |
| `WIFIGUARD-RASPBERRY` | READY 기반 Batch8 수집기, WGSP 디코더, Linux direct ioctl safe-open 및 자동 재동기화 | UART와 replay 전송, 재실감지, 피처, MQTT 경로 유지 |
| `WIFIGUARD-BACKEND` | 학습 패키지, 라이브 Kafka inference worker, 체크포인트 어댑터, causal mode5, EDGE 이벤트 저장, WS, ntfy 라우팅, 로컬 compose 추가 | 기존 계약, ingest, API, DB, 웹소켓 테스트 유지 |
| `WIFIGUARD-FRONTEND` | 통합 과정에서 발견한 포맷 오류 수정 | 기존 화면·라우트·API·웹소켓 기능 유지 |

## 고정된 실기 계약

- 수신 프레임: WGSP v1, 프레임당 576바이트
- SPI 배치: 8프레임, 트랜잭션당 4608바이트
- SPI: mode 0, 6MHz, Pi `/dev/spidev0.0`
- READY: Pi BCM25, active-high, pull-down
- Pi spidev 버퍼: 최소 4608바이트, 실기 설정 8192바이트
- 전송 구현: Linux `SPI_IOC_MESSAGE(1)` direct ioctl
- 복구: 한 배치가 전부 무효면 SPI 파일 디스크립터를 닫고 0.05초 뒤 다시 연다

실기 30초 측정에서 19,268프레임, 320.014Hz, MQTT 발행 168건, 시퀀스 누락 5프레임,
transport error 0을 확인했다. Kafka 토픽 offset 증가도 별도로 확인했다.

## 2026-09-10 검증 결과

| 대상 | 결과 |
|---|---|
| ESP32-C5 Batch8 수신기 | ESP-IDF 6.0.2 빌드 통과, Batch8=8·queue=16·CS GPIO23 확인 |
| ESP32-C5 320Hz 송신기 | ESP-IDF 6.0.2 빌드 통과, 320Hz·채널48·UDP 3333 확인 |
| Raspberry Pi 패키지 | pytest 89 통과, 하드웨어 선택 테스트 2 skip |
| Backend | PostgreSQL 16 격리 DB 기준 pytest 77 통과 |
| Frontend | Vitest 34 통과, typecheck·production build·lint 통과(React-refresh 경고 7개) |
| CSI 낙상 파이프라인 | pytest 21 통과, 선택 테스트 2 skip |
| 합성 raw CSI 데모 | 데이터 생성 → 전처리 → 학습 → 체크포인트 저장 → 예측 파일 생성 통과 |
| 모의 MQTT→Kafka 적재 | 계약 메시지 3건 적재·재검증 통과; Raspberry 12초 replay 85건 적재 통과 |

모의 replay 적재에서는 `presence` 46건, `signal` 31건, `telemetry` 8건이 저장됐다. 브리지의
거부와 처리 오류는 모두 0이었고, Kafka에서 같은 device UUID의 레코드를 다시 읽어 계약 모델로
검증했다. 이 과정에서 발견한 `FeatureLoop`·`PresenceLoop`·UART reader의 Thread `_stop`
이름 충돌도 수정하고 정상 종료 회귀 테스트를 추가했다.

Pi 설치 스크립트는 현재 작업 위치와 무관하게 레포 경로를 계산하고, 실행 코드·기본 설정·공통
계약 패키지를 `/opt/wifiguard-edge`에 설치한다. 기존 `config/device.toml`은 재설치 시 덮어쓰지
않는다. 네 개 레포를 서로 다른 상위 경로에 clone한 경우에는 `CONTRACTS_DIR`로 백엔드의
`packages/contracts` 절대 경로를 지정해야 한다.

Windows에서 ESP-IDF 프로젝트를 공백이 들어간 긴 경로에서 직접 빌드하면 ESP-IDF의 Ninja/ldgen
명령 인용 문제가 발생한다. 같은 소스를 짧은 임시 경로로 복사한 빌드는 성공했으므로 펌웨어 코드
오류가 아니다. CI 또는 개별 ESP 레포에서는 짧은 checkout 경로를 사용한다.

## 2026-09-16 모델 E2E 준비 추가

- `SignalMsg`의 1-D 대표신호를 Raspberry와 동일한 `features_from_signal()`로 ACF/CWT 변환한다.
- 신/구 체크포인트 형식을 지원하되, 현재 와이어 계약과 재현 가능한 `legacy_map + cwt`만 허용한다.
- 추론 결과는 `csi-inference-result`로 발행하고, 인제스트가 5-window causal vote를 적용한다.
- 확정 FALL은 `source_event_key`로 멱등 저장하고 `source="EDGE"`, 모델 버전을 보존한다.
- REST/WS 최신상태와 ntfy 알림 큐를 갱신한다.
- 모델 파일 형식 검사와 합성 Kafka 왕복 검증 도구를 제공한다.
- 로컬 compose는 모델 프로필을 분리해 가중치 없이도 API/적재 스택을 실행할 수 있다.

## 아직 완료로 표시하면 안 되는 항목

1. 제공된 낙상 파이프라인에는 실제 학습 데이터와 승인된 운영 체크포인트가 없다.
2. 실제 가중치가 없으므로 정확도·실기 낙상 판정·장시간 모델 soak는 아직 검증할 수 없다.
3. SMS/ARS 사업자 어댑터는 미선정이며 현재 자동 알림 채널은 ntfy push다.
4. SPI는 현재 receive-only라 기존 UART의 `train` 제어 명령을 전송하지 못한다.
5. 네트워크 단절 spool과 장시간 하드웨어 soak/fault-injection은 후속 검증 대상이다.
6. 제공된 낙상 파이프라인 압축본에는 별도 LICENSE 파일이 없었다. 외부 공개·배포 전 출처와 라이선스를 확인해야 한다.

## 2026-09-16 PR 직전 안정화

- inference worker는 처리 실패 후 다음 Kafka 레코드로 진행하지 않는다. 같은 레코드의
  모델 변환·추론·발행이 성공한 뒤에만 수동 offset을 커밋한다.
- 영구적으로 형식이 잘못된 레코드는 오류로 기록한 뒤 명시적으로 거부해 poison record가
  파티션을 무한 차단하지 않게 했다.
- 로컬 compose에 PostgreSQL·TimescaleDB·Mosquitto·Kafka healthcheck와 조건부
  `depends_on`을 적용했다. API migration은 DB가 준비되고 Kafka topic 초기화가 성공한 뒤
  실행하며 inference도 topic 초기화 완료 후 시작한다.
- 루트 ignore 규칙과 재현 가능한 merge-ready ZIP 생성 스크립트를 추가해 가중치, 비밀 설정,
  가상환경, QA 렌더링, ESP 빌드 산출물이 PR/ZIP에 포함되지 않게 했다.
- E2E를 제외한 최신 회귀 결과는 189 passed, 4 skipped이며 Compose 정적 해석,
  프론트 typecheck/build, 임시 호환 체크포인트 CPU 추론도 통과했다.

세부 PR 판정과 의도적으로 제외한 acceptance 항목은 루트의
`PR_READINESS_20260916.md`를 따른다.

## 머지 요청 준비 순서

이 작업장은 Git 이력과 remote가 없는 ZIP에서 만들어졌기 때문에 여기서 바로 PR/MR을 생성할 수 없다.
각 실제 원격 레포를 clone한 뒤 아래 순서로 해당 폴더 내용을 적용한다.

1. `WIFIGUARD-ESP`: 펌웨어 두 프로젝트와 문서 적용, 비밀 설정은 커밋하지 않는다.
2. `WIFIGUARD-RASPBERRY`: WGSP transport와 배포 설정 적용 후 전체 pytest를 실행한다.
3. `WIFIGUARD-BACKEND`: 모델 패키지·inference worker·DB migration을 적용하고 전체 테스트를 실행한다.
4. `WIFIGUARD-FRONTEND`: 포맷 수정 적용 후 test/typecheck/build/lint를 실행한다.
5. 운영 체크포인트는 Git에 넣지 말고 배포 시 read-only secret/artifact로 주입한다.

소프트웨어 회귀 검증은 루트의 `scripts/validate-repository.ps1`로 반복할 수 있다. Backend DB 테스트는
`-BackendDatabaseUrl`을 전달했을 때만 실행해, 개발자의 기존 DB를 임의로 변경하지 않게 했다.
