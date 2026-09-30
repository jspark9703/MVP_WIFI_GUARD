# WIFI-GUARD PR 준비 상태

기준일: 2026-10-01

## 결론

현재 브랜치는 Raspberry Pi 3B+ 기반 운영 구조의 정리와 재검증을 마친 PR 후보다.
이 문서는 제출 판단의 단일 체크리스트이며 Jetson/VMware 이식 설계는 이번 PR 범위에
포함하지 않는다.

운영 후보 모델은 `services/csi-fall-segmentation` 하나다. 가중치, 학습 데이터,
로컬 `.env`, ESP 빌드 결과와 원시 프로파일 로그는 Git에 넣지 않는다. 이전 ACFD 실험
패키지와 중복 검증 산출물은 후보에서 제거했다.

## 유지하는 검증 근거

| 파일 | 의미 |
|---|---|
| `artifacts/validation/software-e2e.json` | MQTT/Kafka, 실제 모델 추론, 상태 판정, DB/API 소프트웨어 E2E |
| `artifacts/validation/segmentation-reference-cpu.json` | 원본 모델과 이식 모델의 피처·확률 수치 일치 |
| `artifacts/validation/physical-e2e-summary.json` | 실물 SPI/Pi/Wi-Fi/적재 검증의 비식별 요약 |
| `artifacts/validation/training-smoke.json` | 최소 학습 실행 결과 |
| `artifacts/validation/finetune-smoke.json` | 최소 파인튜닝 실행 결과 |
| `artifacts/validation/finetune-mlflow.json` | MLflow 등록 검증 결과 |
| `artifacts/resource-profiles/*.json` | CPU/RAM/GPU/VRAM/저장공간 요약 |
| `artifacts/validation/aws-*.json` | AWS 용량·비용 산정 입력과 결과 |

대용량 체크포인트와 원시 시계열은 위 결과를 재현할 때 외부에서 주입한다.

## 검증된 범위

- ESP32-C5 Batch8 WGSP → Raspberry Pi direct ioctl → MQTT 전송
- 유선 및 Wi-Fi 기반 Pi 운영 경로
- MQTT → Kafka → temporal segmentation 추론 → 추론 결과 Kafka
- 낙상 상태 전이 → PostgreSQL → REST/WebSocket
- 실제 Gmail SMTP 발신 → 외부 수신함 도착(사람이 수신함에서 2건 확인)
- `/train` 화면, 학습·파인튜닝 API, MLflow 등록, 리소스 측정
- 프론트엔드 test/typecheck/build/lint와 Compose 정적 해석

다음은 검증 범위가 아니다.

- 실제 사람 낙상/비낙상 데이터의 정확도·오탐·미탐
- 장시간 하드웨어 soak와 장애 주입
- 실제 AWS 계정에 배포한 부하·비용 실측
- SMS/ARS 사업자 연동

## PR 직전 필수 게이트

루트에서 다음을 실행한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\validate-repository.ps1 `
  -SkipInstall `
  -BackendDatabaseUrl '<disposable PostgreSQL test DSN>'
```

전체 로컬 스택과 외부 체크포인트가 준비된 경우에만 `-RunSoftwareE2E`를 추가한다. 실행 후
`scripts/clean-local-e2e-data.ps1 -Execute`가 합성 사용자·기기·낙상 행을 제거한다.

추가 제출 조건:

- `git -c core.whitespace=cr-at-eol diff --check` 통과
- 강한 비밀 패턴 및 개인 절대경로 검출 0
- Git 후보 파일 중 10 MiB 초과 파일 0
- `compose/.env`, 모델 가중치, 학습 데이터, 원시 프로파일 로그가 후보에 없음
- 생성된 merge-ready ZIP의 파일 목록을 마지막으로 검토

검증 결과와 남은 경계는 PR 본문에 그대로 적고, 미검증 항목을 완료로 표현하지 않는다.

## 2026-09-29 최종 정리·검증 결과

`scripts/validate-repository.ps1`를 일회용 PostgreSQL 테스트 DB와 함께 실행했고 전체 게이트가
`REPOSITORY_VALIDATION_PASS`로 끝났다. 테스트 DB는 실행 후 제거했다.

| 게이트 | 결과 |
|---|---:|
| Raspberry 테스트 | 92 passed, 2 skipped |
| Backend 단위 테스트(DB 제외) | 44 passed |
| Backend API DB 통합 테스트 | 50 passed |
| Live inference 테스트 | 9 passed |
| Temporal segmentation 테스트 | 3 passed |
| Legacy model 회귀 테스트 | 21 passed, 2 skipped |
| Frontend 테스트 | 34 passed |
| Frontend typecheck / production build | PASS / PASS |
| Frontend lint | 0 errors, 기존 Fast Refresh warning 7건 |
| Docker Compose 정적 해석 | PASS |
| Git whitespace 검사 | PASS |

합계는 **253 passed, 4 skipped**다. 또한 합성 E2E 사용자 7명, 기기 7대, 낙상 8건과
관련 Timescale 텔레메트리 163,712행을 로컬 DB에서 정리했다. 운영 후보 파일에서는 개인
이메일·사설 IP·로컬 사용자 경로와 일반적인 private-key/AWS/Google key 패턴이 검출되지
않았고, 10 MiB를 넘는 후보 파일도 없다.

독립형으로 생성됐던 리소스 대시보드 복제본은 E2E 실행 경로와 무관하고 Prometheus/Grafana
구성과 중복돼 제거했다. 운영 모니터링 코드는 Compose에 연결된 Prometheus, Grafana,
Alertmanager, Loki, cAdvisor, MLflow 구성만 유지한다.

## 2026-10-01 PR 제출 재검증

Raspberry Pi 3B+ 범위로 PR 제출 직전 전체 게이트를 다시 실행했다. 일회용 PostgreSQL에서
API DB 통합 테스트 50개를 실행하고 테스트 컨테이너를 제거했으며, Docker Compose 정적
해석도 다시 통과했다. 최종 결과는 **253 passed, 4 skipped**, 프런트엔드 lint는 오류 0건과
기존 Fast Refresh 경고 7건이다. Jetson/VMware 이식 설계는 이 제출에 포함하지 않는다.
