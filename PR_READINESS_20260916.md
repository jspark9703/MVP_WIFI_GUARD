# WIFI-GUARD PR 준비 상태

작성일: 2026-09-16

## 결론

운영 모델 가중치와 실제 Docker/DB end-to-end 검증을 제외한 코드·설정·회귀 검증은
PR 제출 가능한 상태다. 운영 가중치는 저장소에 포함하지 않고 배포 시 읽기 전용으로 주입한다.

이 작업 디렉터리는 원격 Git 이력이 없는 ZIP에서 만들어졌으므로, 실제 PR은 대상 GitHub
저장소를 clone한 뒤 이 변경을 적용해 생성해야 한다. 현재 파일을 부모 작업 저장소에 통째로
커밋하면 안 된다.

## PR 직전 보강 사항

- 추론 워커가 실패한 Kafka 레코드를 건너뛰지 않도록 같은 레코드를 재시도한 뒤에만
  offset을 커밋한다.
- 형식이 잘못된 영구 실패 레코드는 오류로 계수하고 명시적으로 거부해 파티션 전체를
  무한 차단하지 않는다.
- PostgreSQL·TimescaleDB·Mosquitto·Kafka healthcheck를 추가했다.
- API는 DB와 브로커가 healthy이고 Kafka topic 초기화가 성공한 뒤 시작한다.
- 추론 서비스는 Kafka topic 초기화가 성공한 뒤 시작하며 일시적 시작 실패 시 재시작한다.
- 루트 `.gitignore`로 가중치·비밀 설정·가상환경·캐시·ESP 빌드·보고서 QA 산출물을 제외한다.
- `scripts/create-merge-ready-archive.ps1`로 Git ignore 규칙과 동일한 파일만 ZIP으로 묶는다.

## 검증 결과

| 대상 | 결과 |
|---|---|
| Raspberry | 89 passed, 2 skipped |
| Backend 계약·ingest·notification | 38 passed |
| Live inference | 7 passed |
| CSI 모델 파이프라인 | 21 passed, 2 skipped |
| Frontend | 34 passed |
| Frontend typecheck/build | 통과 |
| Frontend lint | 오류 0, 기존 Fast Refresh 경고 7 |
| Compose 정적 해석 | 통과 |
| Alembic head | `0002` 단일 head |
| 호환 체크포인트 로드·CPU 추론 | 임시 체크포인트로 통과 |
| 강한 비밀 패턴 검사 | 검출 0 |
| PR 후보 대용량 파일 | 10 MiB 초과 0 |

총 자동 테스트는 **189 passed, 4 skipped**다. skip은 실제 하드웨어 또는 선택적 학습
의존성이 필요한 테스트이며 기본 소프트웨어 회귀 실패가 아니다.

## 이번 PR에서 의도적으로 제외하는 검증

- 승인된 운영 체크포인트를 사용한 실제 낙상 정확도·실기 판정
- Docker Desktop에서 전체 compose build/up
- 실제 PostgreSQL migration과 API 저장·알림 통합
- 하드웨어부터 모델 판정·DB·WebSocket·알림까지의 전체 end-to-end
- 장시간 하드웨어 soak 및 fault injection

위 항목은 코드가 빠졌다는 의미가 아니라 외부 가중치·실행 인프라·실기 장치가 필요한
후속 acceptance 검증이다. PR 설명과 리뷰 체크리스트에 미검증 상태를 그대로 유지한다.

## PR 설명문 요약

이 변경은 검증된 WGSP Batch8 SPI 수집 경로를 보존하면서 SignalMsg 기반 ACF/CWT 변환,
체크포인트 호환성 검사, Kafka 추론 워커, causal mode5 후처리, 낙상 이벤트 멱등 저장,
실시간 상태 및 ntfy 알림 연결을 추가한다. 실제 모델 가중치는 보안·용량·승인 절차 때문에
저장소에 포함하지 않는다. 기존 UART/replay/API/웹 경로는 유지한다.
