# 실시간 모델 가중치 인수 계약

실제 학습 가중치는 이 저장소에 포함하지 않는다. 전달받은 체크포인트는 읽기 전용 파일로
주입하며, 아래 조건을 만족해야 라이브 파이프라인이 기동된다.

## 허용 형식

권장 형식은 `services/csi-fall-pipeline`이 생성한 `model/model.pt`다.

- 최상위 키: `model`, `config`, `normalization`, `threshold`, `version`
- `config.feature`: 반드시 `legacy_map`
- `config.cwt`: 반드시 `true`
- `config.backbone`: `compact` 또는 `resnet18`
- `normalization.secondary`: `{mean, std}`
- `normalization.cwt`: `{mean, std}`

`legacy_map + cwt`만 허용하는 이유는 Raspberry Pi가 30채널 원시 윈도우가 아니라
`select_pc_signal()` 이후의 1차원 대표 신호를 올리기 때문이다. `channel_mean` 등
30채널 입력이 필요한 체크포인트는 현재 와이어 계약으로 복원할 수 없으며, 억지로 실행하면
학습·실시간 피처가 달라진다. 로더는 이런 체크포인트를 명시적으로 거부한다.

이전 `best_model.pt`의 `model_config/model_state_dict/normalization` 형식도 호환한다.

## 배치 위치와 환경변수

```text
WIFIGUARD-BACKEND/weights/model.pt   # 예시, Git 제외
MODEL_CHECKPOINT=/models/model.pt    # 컨테이너 내부 경로
MODEL_DEVICE=cpu                     # cpu | cuda | mps | auto
MODEL_THRESHOLD=                     # 비우면 체크포인트 threshold 사용
```

가중치 파일은 신뢰한 출처에서만 받아야 한다. PyTorch 체크포인트는 역직렬화 시 코드 실행이
가능하므로 해시를 별도 채널로 전달받아 대조한다.

## 인수 순서

1. 전달자에게 파일 SHA-256과 학습 설정을 별도 채널로 받는다.
2. SHA-256을 확인하고 `weights/model.pt`에 둔다.
3. `python tools/validate_model_checkpoint.py weights/model.pt`를 실행한다.
4. Kafka·API·인제스트·추론 서비스를 기동한다.
5. `python tools/validate_live_inference_kafka.py --bootstrap ... --tenant ... --device ...`로
   실제 Kafka 왕복을 확인한다.
6. 실 CSI로 30초, 10분 soak를 실행해 처리율·지연·오류·DB·WebSocket을 확인한다.

합성 신호 검증은 연결성과 텐서 호환성만 증명하며 낙상 정확도를 증명하지 않는다.
