# WIFI-GUARD 모델 E2E 준비 결과

작성일: 2026-09-16

## 결론

실제 학습 가중치를 제외한 모델 E2E 실행 경로를 채웠다. 승인 체크포인트를 전달받아
`WIFIGUARD-BACKEND/weights/model.pt`에 배치하면, 별도 애플리케이션 코드 수정 없이
체크포인트 검증 → Kafka 추론 → 상태판정 → DB/WS → ntfy 알림을 실행할 수 있다.

가중치가 없거나 현재 대표신호 계약으로 재현할 수 없는 체크포인트면 기동 단계에서 명확히 실패한다.
임의 확률이나 더미 모델로 운영 판정을 만들지 않는다.

## 데이터 흐름

```text
ESP32 TX → ESP32 RX → Batch8 SPI → Raspberry Pi
  → MQTT SignalMsg → Kafka csi-feature-stream
  → exact ACF/CWT feature builder → model checkpoint
  → Kafka csi-inference-result
  → causal mode5 state machine
  ├─ LiveCache → REST / WebSocket
  ├─ PostgreSQL fall_events(source=EDGE, idempotent)
  └─ configured Recipient → asynchronous ntfy push
```

## 구현된 경계

1. `services/model-serving`: 기존 체크포인트와 새 `csi-fall-pipeline` 체크포인트를 읽는 어댑터.
2. `services/inference`: Kafka consumer/producer, 정확히 같은 실시간 피처 변환, 모델 추론, 중복 억제.
3. `services/ingest`: per-device causal 5-window 투표, 쿨다운, 실시간 캐시와 WS 갱신.
4. `packages/db`: EDGE 낙상 이벤트의 모델 버전과 원천 키 저장, 재전달 멱등성.
5. `services/notification`: DB 수신자 설정을 읽어 ntfy를 비동기 발송하는 큐와 재시도.
6. `compose`: API/DB/MQTT/Kafka 기본 스택과 선택형 `model` 프로필.
7. `tools`: 체크포인트 단독 검증과 합성 Kafka 왕복 검증.

## 체크포인트 인수 기준

- 권장 형식: `services/csi-fall-pipeline`의 `model/model.pt`.
- 현재 라이브 입력과 일치하려면 `config.feature=legacy_map`, `config.cwt=true`여야 한다.
- 파일과 SHA-256은 서로 다른 채널로 받아 검증한다.
- 체크포인트는 Git/ZIP에 넣지 않고 read-only로 마운트한다.
- 세부 절차: `WIFIGUARD-BACKEND/docs/MODEL_HANDOFF.md`.

## 가중치 수령 후 실행

```bash
cd WIFIGUARD-BACKEND
sha256sum -c weights/model.pt.sha256
make model-check
cp compose/.env.example compose/.env
# compose/.env의 비밀번호/JWT_SECRET을 실제 개발값으로 변경
make model-up
python tools/validate_live_inference_kafka.py \
  --bootstrap 127.0.0.1:9092 --tenant <tenant> --device <device-uuid>
```

그 뒤 실 CSI 30초와 10분 soak에서 모델 처리율, 추론 지연, Kafka 결과, EDGE DB 이벤트,
WebSocket 상태, 실제 ntfy 수신을 함께 확인한다.

## 현재 검증 한계

- 실제 가중치가 없어 모델 정확도와 체크포인트 런타임은 아직 검증 전이다.
- Docker Desktop 데몬이 꺼진 환경에서는 compose 정적 구성만 검증할 수 있다.
- SMS/ARS 사업자 연동은 미선정이고 자동 알림 채널은 현재 ntfy다.
- 합성 Kafka 검증은 계약·연결성 검증이지 낙상 정확도 검증이 아니다.
