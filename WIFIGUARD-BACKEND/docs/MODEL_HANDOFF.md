# In-house temporal segmentation 모델 인수 계약

운영 가중치는 저장소에 커밋하지 않고 읽기 전용으로 주입한다. 현재 연동 모델은
`services/csi-fall-segmentation`의 `S3 + PCA-ACF + dual ResNet18 temporal segmentation`이다.

## 파일과 해시

```text
weights/temporal_segmentation_fixed_delay.pt
SHA-256 7cb0b7436f0bc7f93b69e6c39678ebc5a31c707e2777244ec7973a9ecfa68a41
size 93,539,875 bytes
```

PyTorch 체크포인트는 `weights_only=True`로 읽으며, 신뢰한 출처와 독립된 채널의 해시를 함께
확인한다. 원본 ZIP과 데이터셋 provenance는 `model-manifests/inhouse-segmentation.json`과
`artifacts/validation/dataset-registry.json`에 기록한다.

## 입력·출력 계약

- 엣지 입력: 선택된 30채널의 `float32 (960, 30)` 진폭창, 3초, 320 Hz
- 모델 입력: anti-aliasing 리샘플 후 `(500, 30)`, 166.6667 Hz
- 피처: S3 `(224, 224)`와 PCA-ACF `(1, 128, 64)`
- 출력: 64개 sigmoid probability bin과 exact-center 보간 probability
- 추론 stride: 0.25초, 고정 지연: 1.5초, 임계값: 0.5
- 운영 후처리: causal trigger B가 기본이며 `MODEL_POSTPROCESS=a|b`로 선택 가능
- 메시지: `SignalMsg.amplitude_b64`, `amplitude_rows`, `amplitude_cols`를 함께 보낸다.
- 결과: `segmentation_a`, `segmentation_b`, 선택된 `decision`을 Kafka 결과에 기록한다.

30채널의 선정은 현재 엣지 계약을 그대로 사용한다. Q-value는 245개 전체 서브캐리어에서
상위 30개를 고르는 기준이 아니며, 선택된 30채널에서 PCA 대표 신호를 만드는 모델 내부 단계다.

## 학습·파인튜닝·MLOps

`csi_fall_segmentation.training`이 동일한 모델과 피처 정의를 사용해 `train`과 `finetune`을
수행한다. 파인튜닝은 `--freeze-encoders`로 encoder를 고정할 수 있으며, 체크포인트·파라미터·
metric은 MLflow 실행과 모델 레지스트리에 기록한다. 원본 패키지의 77개 녹화는 이미 학습에
사용됐으므로 실행 재현용이지 독립 정확도 평가용 hold-out이 아니다.

## 인수 순서

1. `tools/import_segmentation_package.py`로 ZIP·데이터·체크포인트 해시를 검증한다.
2. `make model-check`로 원본 parity와 합성 추론을 확인한다.
3. `make model-up`으로 MQTT·Kafka·DB·API·실제 모델을 기동한다.
4. `make model-e2e`로 하드웨어 없는 software E2E를 관통한다.
5. `make obs`와 `make monitor-host`로 컨테이너와 호스트 리소스를 관측한다.
6. CUDA train/finetune smoke를 실행하고 MLflow run·artifact·registered model을 확인한다.
7. 마지막으로 실제 ESP32·Pi·SPI/GPIO와 독립 현장 데이터 정확도를 별도 검증한다.

Software E2E는 계약·연결·저장을 증명하지만 물리 전송 안정성과 실제 낙상 정확도를 증명하지
않는다. 전체 근거와 AWS 용량 envelope는 `docs/INHOUSE_SEGMENTATION_E2E_AND_RESOURCES_KO.md`를
참조한다.
