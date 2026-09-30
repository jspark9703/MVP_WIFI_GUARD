# WIFI-GUARD 통합 및 머지 인계서

기준일: 2026-10-01

## 통합 결과

이 브랜치는 ESP32-C5 수집부터 Pi, MQTT/Kafka, 실제 temporal segmentation 모델,
저장·API·WebSocket·알림·학습/MLOps 화면까지 한 저장소에서 재현할 수 있게 통합한다.
기존 UART/replay/API/웹 기능은 유지한다.

운영 모델 경로는 다음 하나다.

```text
services/csi-fall-segmentation
        ↓ 직접 의존
services/inference
        ↓ csi-inference-result
services/ingest → PostgreSQL / API / WebSocket / notification
```

가중치는 `weights/temporal_segmentation_fixed_delay.pt` 위치에 외부 artifact로 주입하며
Git에는 포함하지 않는다. 모델이 없거나 계약이 맞지 않으면 추론은 fail-closed한다.

## 레포별 변경 범위

| 영역 | 변경 내용 |
|---|---|
| `WIFIGUARD-ESP` | 320 Hz UDP 송신기, WGSP v1 Batch8 SPI 수신기, ESP32-C5 WROOM-1U 보드 설정 |
| `WIFIGUARD-RASPBERRY` | READY 기반 Batch8 수집, direct ioctl safe-open·재동기화, Wi-Fi 운영 설정 |
| `WIFIGUARD-BACKEND` | segmentation 추론 worker, 저장·알림, 학습/파인튜닝, MLflow, 관측·AWS 산정 |
| `WIFIGUARD-FRONTEND` | `/train`, 알림 설정, 생성 API 타입, production preview/build 보강 |

## 고정된 실기 계약

- WGSP v1: 576 bytes/frame
- Batch8: 8 frames, 4608 bytes/transaction
- SPI mode 0, 6 MHz, `/dev/spidev0.0`
- READY: Raspberry Pi BCM25, active-high, pull-down
- Pi spidev buffer: 8192 bytes
- Linux `SPI_IOC_MESSAGE(1)` direct ioctl, safe-open
- 전 배치가 무효면 파일 디스크립터를 닫고 0.05초 후 재동기화

실기 수집 검증에서 19,268 frames, 320.014 Hz, MQTT publications 168,
sequence gaps 5, transport errors 0을 확인했다. 이후 전체 물리 E2E 관찰에서 feature 1,246건과
inference 154건이 짝을 이루었고 Kafka lag 0, Timescale 저장을 확인했다. 이는 연결·처리량
검증이며 실제 낙상 정확도 검증은 아니다.

## 데이터와 보안 정책

- 저장소에 포함: 코드, 설정 예제, 비식별 요약 JSON, 재현 스크립트
- 저장소에서 제외: `.env`, 키/인증서, 실제 이메일 주소, 앱 비밀번호, 모델 가중치,
  학습 데이터, 임시 체크포인트, 원시 프로파일 JSONL, ESP build 디렉터리
- 외부 이메일 E2E 스크립트는 주소와 대상 UUID를 명시적 인자로 받고 임시 DB 행을 삭제한다.
- `scripts/clean-local-e2e-data.ps1`는 기본 dry-run이며 `-Execute`에서만 식별된 합성 행을 지운다.

## 머지 전 실행

1. [PR_READINESS.md](PR_READINESS.md)의 검증 명령을 실행한다.
2. 변경 파일·비밀·대용량 파일·중복 산출물을 점검한다.
3. `scripts/create-merge-ready-archive.ps1`로 리뷰용 ZIP을 만들고 목록을 확인한다.
4. 결과가 모두 통과한 뒤에만 commit/push/PR을 별도 승인받아 수행한다.

이번 PR은 기존 Raspberry Pi 3B+ 기반 구조만 대상으로 한다. Jetson/VMware 이식은
후속 설계 및 별도 PR로 분리한다.
