# WIFI-GUARD 전체 파이프라인과 검증 범위

기준일: 2026-09-29

## 한 문장 결론

하드웨어 수집, 클라우드 적재, 실제 temporal segmentation 모델 추론, 저장·화면·외부 이메일
알림, 학습·파인튜닝·MLflow까지 각 운영 경로는 연결해 검증했다. 다만 실제 사람의 낙상 데이터로
정확도와 오탐·미탐을 판정한 것은 아니다.

## 데이터 흐름

```text
송신 ESP32-C5
  → UDP CSI
수신 ESP32-C5
  → WGSP Batch8 SPI + READY
Raspberry Pi
  → 3초 × 320 Hz × 30채널 진폭창
  → MQTT (유선 또는 Wi-Fi)
MQTT bridge
  → Kafka csi-feature-stream
Inference worker
  → S3 + PCA-ACF → temporal segmentation → 64-bin probability/causal trigger
  → Kafka csi-inference-result
Ingest/API
  → TimescaleDB + PostgreSQL + Redis
  → REST + WebSocket + Frontend + email/ntfy
```

Pi가 선택하는 30채널은 Q-value 상위 30개가 아니다. 학습과 같은 고정 규칙으로 245 complex
pairs에서 0-based 121·122를 제외한 뒤 균등하게 고른다. Q-value는 선택 후 대표 신호를 만드는
내부 단계에 사용한다.

## 확인한 결과

| 범위 | 결과 |
|---|---|
| 실물 SPI 수집 | 19,268 frames, 320.014 Hz, sequence gap 5, transport error 0 |
| 실물 E2E 관찰 | feature 1,246, inference 154, missing/unmatched/duplicate 0, Kafka lag 0 |
| 모델 기준 구현 일치 | CPU reference 비교의 S3/ACF/확률 오차 기준 모두 통과 |
| 소프트웨어 E2E | MQTT/Kafka 실제 모델 4건, 상태 판정 양성 5건, DB/API 조회 통과 |
| 외부 이메일 | Gmail SMTP 직접 테스트와 합성 낙상 알림 모두 실제 수신함 도착 확인 |
| 학습/MLOps | 최소 학습·파인튜닝, MLflow 등록, `/train` 화면, 리소스 측정 통과 |
| 운영 모드 | Pi 유선·Wi-Fi 연결, GUI 설정 경로, 프론트 production build/preview 검증 |

소프트웨어 E2E의 합성 진폭창은 실제 모델에서 음성이었다. DB/API 양성 경로는 계약에 맞는 양성
추론 결과를 모델 뒤 Kafka 토픽에 주입해 검증했다. 따라서 이를 "모델이 실제 낙상을 맞혔다"고
해석하면 안 된다.

## 남은 acceptance

- 실제 낙상·비낙상 CSI의 정확도, 민감도, 특이도, 오탐·미탐
- 장시간 하드웨어 soak와 전원·네트워크·브로커 장애 복구
- 실제 AWS 배포 환경의 장기 부하·비용 실측
- SMS/ARS 사업자 어댑터

AWS 리소스와 일간 비용 산정은
[AWS_DAILY_COST_AND_PIPELINE_BREAKDOWN_KO.md](AWS_DAILY_COST_AND_PIPELINE_BREAKDOWN_KO.md),
세그멘테이션·학습·리소스 검증 상세는
[INHOUSE_SEGMENTATION_E2E_AND_RESOURCES_KO.md](INHOUSE_SEGMENTATION_E2E_AND_RESOURCES_KO.md)를 참고한다.
