설계하신 아키텍처는 **엣지-클라우드 분리형 파이프라인과 MLOps 자동화 루프(감지→알람→재학습→배포)가 완벽하게 결합된 모범적인 구조**입니다.

라즈베리파이에서 고주파 데이터 전처리를 1차 수행(엣지 게이팅)하여 대역폭 및 클라우드 비용을 절감하고, 클라우드 단에서는 Kafka를 메시지 버스로 두어 모니터링 DB 저장, 딥러닝 추론, Drift 분석을 **독립적인 Pub/Sub 파이프라인**으로 완벽히 분리한 점이 매우 뛰어납니다.

---

## 1. 전체 시스템 아키텍처 (System Architecture)

```
[ 엣지 (라즈베리파이) ]
   ESP32-C5 (5GHz CSI) ──► pyserial 수신 ──► Presence/Motion 감지 (Gating) 
   ──► 피처 추출 (PCA/S3 등) ──► MQTT over TLS (AWS IoT Core)
                                           │
===========================================│===========================================
[ 클라우드 백엔드 & MLOps 파이프라인 ]      ▼
                                  [ AWS IoT Core ]
                                           │ (IoT Rule Engine Direct Routing)
                                           ▼
                                   [ Apache Kafka ]
                                           │
         ┌─────────────────────────────────┼─────────────────────────────────┐
         ▼                                 ▼                                 ▼
[ Consumer 1: DB Ingestion ]   [ Consumer 2: Inference ]      [ Consumer 3: MLOps ]
 (Faust / Python Worker)        (TorchServe / ONNX Worker)     (Evidently AI Monitor)
         │                                 │                                 │
         ▼                                 ▼                                 ▼
  [ TimescaleDB ]                 [ Kafka Result Topic ]           [ Prometheus Exporter ]
  (재실/피처 시계열)                       │                                 │
         │                         ┌───────┴───────┐                         │
         ▼                         ▼               ▼                         ▼
  [ Grafana 대시보드 ] ◄──── [ WebSocket ]   [ ntfy / SMS ] ◄─────── [ Prometheus / AlertManager ]
   (통합 실시간 관제)        (앱 실시간알람)   (낙상 응급알림)        (Drift 알림 / Airflow Webhook)
                                                                             │
   ┌─────────────────────────────────────────────────────────────────────────┘
   ▼
[ Airflow DAG ] ──► [ Few-shot/LoRA 재학습 ] ──► [ MLflow Registry ] ──► [ 모델 Hot-Reload 배포 ]

```

---

## 2. 시나리오별 데이터 & 시스템 진행 플로우

### 시나리오 A. 평시 모니터링 및 실시간 추론 플로우 (정상 상태)

> **상시 재실 감지 및 0.25초 단위의 실시간 낙상 추론이 이뤄지는 핵심 경로입니다.**

1. **[엣지 수신 및 게이팅]** 라즈베리파이가 ESP32-C5로부터 100~200Hz CSI 바이너리를 시리얼로 수신합니다.


2. **[1차 재실 판정 & 피처 추출]**
* 엣지의 `PresenceLoop`가 상시 동작하여 재실 및 움직임(MV)을 판정합니다.


* 사람이 없는(ABSENT) 상태에서는 헬스체크 메트릭만 경량 전송하고, 사람이 있거나 활동이 감지된 구간(Window)에만 CSI 위상/진폭 피처(S3 스칼로그램, PCA-ACF 등)를 추출하여 **MQTT over TLS**로 퍼블리시합니다.




3. **[Kafka 메시지 라우팅]** AWS IoT Core의 IoT Rule이 수신된 메시지를 즉시 **Apache Kafka**의 `csi-feature-stream` 토픽으로 수신 및 분배합니다.
4. **[병렬 Consumer 동작]**
* **Consumer 1 (DB 저장)**: 피처 및 재실 상태 데이터를 **TimescaleDB**에 저장하여 Grafana로 실시간 관제 지표를 시각화합니다.
* **Consumer 2 (Inference Worker)**: 0.25초 Stride 윈도우 피처를 딥러닝 추론 워커(TorchServe/ONNX)에 입력하여 낙상 여부를 인과적 다수결(causal voting)로 판정합니다.




5. **[이벤트 알림]**
* 정상 결과는 WebSocket을 통해 **Grafana/프론트 대시보드**로 전송됩니다.


* **낙상(FALL) 확정 시**: 즉시 **ntfy 푸시 알림 및 전체화면 알람**을 이중 발송합니다.





---

### 시나리오 B. Data Shift / Drift 감지 및 Few-shot 재학습 플로우 (MLOps)

> **환경 변화(가구 배치 변경, RF 간섭 등)로 CSI 신호 분포가 바뀌었을 때 시스템이 스스로를 적응시키는 플로우입니다.**

1. **[Drift 감지 (Evidently AI)]**
* Consumer 3 (Evidently AI)가 Kafka의 `csi-feature-stream`을 모니터링하며, 초기 캘리브레이션/학습 데이터(Baseline)와 현재 입력 데이터 간의 통계적 거리(KS-Test, PSI 등)를 계산합니다.




2. **[메트릭 수집 및 알람 (Prometheus & AlertManager)]**
* Evidently AI가 계산한 Drift Score를 **Prometheus**가 Scraping합니다.
* Drift 점수가 설정한 임계값(Threshold)을 초과하면 **AlertManager**가 알람을 발생시키고 **Airflow Webhook**을 호출합니다.


3. **[오케스트레이션 및 데이터 추출 (Airflow)]**
* Airflow의 `model_retrain_dag`가 실행됩니다.
* TimescaleDB에서 해당 공간의 최근 소량 데이터(Few-shot 샘플) 및 ABSENT baseline 데이터를 자동으로 파이프라인으로 가져옵니다.




4. **[Few-shot / LoRA 적응 학습 및 MLOps 등록]**
* Pre-trained 백본(ResNet 등)을 동결하고, 소량의 데이터로 분류 헤드(MLP) 또는 LoRA 어댑터만 미세조정(Fine-tuning)합니다.


* 학습 과정에서의 Loss, Macro F1, 하이퍼파라미터 및 새로 생성된 모델 아티팩트를 **MLflow Model Registry**에 신규 버전(`v1.2.0-fewshot-roomA`)으로 등록합니다.




5. **[안전 검증 및 Hot-Reload 배포]**
* Validation 데이터셋 기준 성능 평가 게이트를 통과하면, Airflow가 Consumer 2(Inference Worker)에 API/Signal을 보냅니다.
* 추론 워커는 서비스 중단 없이 MLflow에서 새 어댑터/가중치를 로드(Hot-Reload)하여 추론을 지속합니다.



---

### 시나리오 C. 사용자 오탐 피드백 및 재캘리브레이션 플로우 (피드백 루프)

> **사용자가 직접 시스템의 오류를 정정하거나 장치를 재설정할 때의 흐름입니다.**

1. **[사용자 피드백 수집]**
* 보호자/사용자가 앱에서 낙상 알림에 대해 **"오탐지"** 버튼을 누르면, 이 이벤트가 DB의 `history` 및 이벤트 로그에 수집됩니다.




2. **[수동/주기적 캘리브레이션 트리거]**
* 사용자가 대시보드에서 "장치 재설정"을 누르면 MQTT 커맨드(`csiguard/{device}/cmd`)가 엣지로 전송되어 4단계 캘리브레이션(퇴실대기 → AGC안정화 → Baseline 재측정)이 수행됩니다.




3. **[재학습 데이터 라벨링]**
* ~~사용자가 "오탐지"로 응답했던 시점의 CSI 윈도우 데이터는 Airflow 재학습 파이프라인에서 Negative Sample(비낙상)로 라벨링되어 모델의 False Positive 오탐율을 낮추는 학습 자료로 활용됩니다.~~
* **[2026-09-08 대체]** 위 문장은 백엔드 명세 §6-3 **G4 원칙**(사용자 응답 라벨을 학습 데이터로 자동 반영하지 않음 — 편향·오라벨 유입 방지)과 충돌하여 폐기한다. "오탐지" 응답은 `fall_events.response` + `event_logs` 감사 이력으로만 보존하며, 재학습 데이터 편입은 별도 검수 절차를 거친다(명세 §6-3 참조).





---

## 3. 요약 및 권장 사항

* **데이터 흐름의 안정성**: 엣지에서의 1차 필터링(Gating)과 Kafka의 디커플링 덕분에 클라우드 서버가 잠시 내려가도 **엣지 재실 감지는 유지되며, 데이터 유실 없이 MLOps 루프가 안전하게 구동**될 수 있습니다.


* **추가 고려사항 (Tip)**: Airflow 파이프라인에서 Few-shot 적응 완료 후 새 모델을 반영하기 전, "이전 모델 대비 Performance Drop이 없는지" 확인하는 안전 검증 단계(Validation Gate)를 반드시 거치도록 DAG 내부 코드를 설계하시는 것을 권장합니다.




