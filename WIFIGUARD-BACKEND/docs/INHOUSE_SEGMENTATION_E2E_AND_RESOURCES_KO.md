# In-house temporal segmentation E2E·MLOps·리소스 검증

검증 기준 시각: 2026-09-23 (KST)
대상 모델: `InhouseSegmentationRealtime_AB_Triggers.zip`
체크포인트 SHA-256: `7cb0b7436f0bc7f93b69e6c39678ebc5a31c707e2777244ec7973a9ecfa68a41`

## 결론

하드웨어를 제외한 소프트웨어 경로는 실제 모델을 사용해 관통했다.

1. 모델 패키지와 데이터셋을 검증하고 등록했다.
2. MQTT 입력을 Kafka로 전달하고, 실제 temporal segmentation 모델이 추론했다.
3. 추론 결과를 다시 Kafka로 발행했다.
4. 인제스트가 낙상 상태를 확정해 PostgreSQL에 저장했고 인증 API에서 조회했다.
5. CUDA 학습과 파인튜닝의 forward/backward, 체크포인트 저장을 실행했다.
6. 파인튜닝 결과를 MLflow 실행 이력과 모델 레지스트리 버전 2로 등록했다.
7. 호스트와 컨테이너의 CPU·RAM·GPU·VRAM·디스크를 실시간 수집하도록 구성했다.

아직 증명하지 않은 것은 두 가지다.

- ESP32·Raspberry Pi·SPI/GPIO를 포함한 물리 전송 경로
- 학습에 사용되지 않은 독립 녹화 데이터에서의 정확도와 일반화

## 전체 흐름

```text
ESP/Pi 또는 mock/replay
        │ MQTT SignalMsg: (960, 30), 320 Hz, 3초
        ▼
Mosquitto → MQTT bridge → Kafka csi-feature-stream
                               │
                               ▼
                      Inference worker
                 320 Hz → 166.6667 Hz 리샘플
                 S3 (224×224) + PCA-ACF (128×64)
                 Dual ResNet18 → 64-bin probability
                 exact-center 보간 → causal trigger B
                               │
                               ▼
                    Kafka csi-inference-result
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
             Ingest state machine     WebSocket/cache
                    │
                    ▼
            PostgreSQL fall_events → 인증 REST API

오프라인 MLOps:
feature NPZ + label NPZ → train/finetune → checkpoint
        → MLflow run/artifact → registered model version
```

실시간 서비스 경로와 오프라인 학습 경로는 같은 모델 정의와 피처 구현을 사용한다. 체크포인트는
Git에 넣지 않고 실행 시 read-only 볼륨 또는 아티팩트 저장소로 주입한다.

## 모델·데이터 계약

| 항목 | 값 |
|---|---:|
| 모델 | Dual ResNet18 temporal segmentation |
| 파라미터 | 23,357,481 |
| 원시 입력 | `(960, 30)` @ 320 Hz, 3초 |
| 모델 입력 | 500 samples @ 166.6667 Hz |
| S3 입력 | `(224, 224)` |
| PCA-ACF 입력 | `(1, 128, 64)` |
| 출력 | 64 sigmoid bins |
| 창 stride | 0.25초 |
| 고정 지연 | 1.5초 |
| 판정 임계값 | 0.5 |
| 기본 후처리 | causal trigger B |
| feature recordings | 77 / 15,320 windows / 781,472,228 bytes |
| label recordings | 77 / 54,563 bytes |
| checkpoint | 93,539,875 bytes |

패키지에 포함된 77개 녹화는 모델 학습에 사용됐다. 따라서 이 데이터로 재현한 결과는 구현 호환성과
실행 가능성을 검증하지만, 외부 대상에 대한 정확도를 증명하지 않는다.

## 실제로 실행한 검증

| 검증 | 결과 | 핵심 근거 |
|---|---|---|
| 패키지 가져오기 | PASS | 체크포인트 해시, 77 feature/label 쌍, 15,320 window |
| 원본 구현 parity | PASS | S3·ACF·확률·center 결과 최대 절대오차 모두 0.0003 이하 |
| 전체 백엔드 회귀 | PASS | 87개 테스트 통과 |
| 모델·추론 단위 테스트 | PASS | segmentation+inference 12개 통과 |
| Raspberry edge 회귀 | PASS | 89개 통과, 하드웨어 의존 2개 skip |
| 프런트엔드 빌드 | PASS | Vite production build 완료 |
| CUDA 학습 smoke | PASS | 4 examples, 1 optimizer step, 체크포인트 생성 |
| CUDA 파인튜닝 smoke | PASS | encoder freeze, 1 optimizer step, 체크포인트 생성 |
| MLflow | PASS | run `18022a5b0e954246be0827c26b41f506`, registered model version 2 |
| 실제 모델 software E2E | PASS | MQTT→Kafka→모델→Kafka→DB→API |
| 물리 하드웨어 E2E | 미실행 | 이번 검증 범위에서 제외 |
| 독립 데이터 일반화 | 미확인 | 별도 hold-out/현장 데이터 필요 |

Software E2E는 모델 경로에서 4개 메시지를 처리했고 모델 버전
`inhouse-segmentation:v1:7cb0b7436f0b`을 확인했다. 이어서 계약에 맞는 양성 추론 5개를 보내
`fall_events` 저장과 API 조회를 확인했다. 이 두 검증을 분리한 이유는 임의 입력으로 낙상 확률을
강제로 만들어 모델 정확도를 가장하지 않기 위해서다.

## 관측 리소스

아래 수치는 이 개발 PC에서 실행한 bounded smoke/benchmark의 피크다. CPU/RSS는 자식 프로세스를
포함한 프로세스 트리, GPU/VRAM은 호스트 단위 샘플이다.

이 값들은 시간당 사용량이 아니다. 추론 profile은 13.17초, 학습 smoke는 14.11초,
파인튜닝 smoke는 13.79초 동안 측정했다. 별도의 호스트 실시간 모니터는 37.21분 동안
1,067개 표본을 수집했으며 평균 표본 간격은 2.09초였다. 따라서 아래 피크값은 기능 검증과
초기 용량 산정을 위한 단기 관측치이며, 24시간·7일 soak 또는 운영 부하 결과로 해석하면 안 된다.

| 작업 | CPU 피크 | RAM 피크 | VRAM 증가 | GPU 사용률 피크 | 디스크 증가 |
|---|---:|---:|---:|---:|---:|
| GPU 추론 benchmark | 1.062 cores | 1.452 GiB | 0.599 GiB | 40% | 0.010 GiB |
| CUDA 학습 smoke | 1.002 cores | 1.482 GiB | 0.762 GiB | 40% | 0.087 GiB |
| CUDA 파인튜닝 smoke | 1.480 cores | 1.465 GiB | 0.422 GiB | 38% | 0.087 GiB |

GPU 추론의 steady-state 중앙값은 피처 34.535 ms/window, 모델 추론 2.720 ms/window였고 피처
P95는 37.242 ms/window였다. 첫 4-window 피처 배치는 CUDA/JIT warm-up을 포함해 5,479 ms였다.
따라서 서비스 준비 상태를 판단할 때 첫 배치와 steady-state를 섞으면 안 된다.

현재 호스트 스냅샷에서는 디스크 사용률이 약 92.5%였다. 장시간 Kafka·Prometheus·MLflow 아티팩트
보존을 시작하기 전에 로컬 디스크 정리 또는 증설이 필요하다.

## AWS 용량 산정

관측 피크에 1.5배 여유를 적용한 시작점이다.
`3 vCPU / RAM 4 GiB / VRAM 2 GiB / storage 10 GiB`는 시간당 소비량이나 비용이 아니라
한 시점에 확보할 권장 시작 용량이다. 시간당·월간 비용은 배포 리전, 인스턴스 가동 시간,
EBS 용량과 IOPS, 로그·Kafka 보존량을 정한 뒤 별도로 계산해야 한다.

| 리소스 | 관측 피크 | 시작 용량 envelope |
|---|---:|---:|
| vCPU | 1.48 cores | 3 vCPU |
| RAM | 1.482 GiB | 4 GiB |
| GPU VRAM | 0.762 GiB 증가 | 2 GiB |
| 영구 저장공간 | dataset+10 checkpoints 약 1.60 GiB | 10 GiB |

10 GiB에는 로그, 백업, Kafka retention, Prometheus 장기 보존, 이후 데이터셋 증가가 포함되지 않는다.
AWS 배포 전에는 목표 동시 기기 수, 보존 기간, 리전, 가용성 요구, CPU/GPU 추론 선택을 넣어 다시
부하 시험해야 한다. 비용은 배포 리전의 최신 가격표로 계산해야 한다.

## AWS 월 비용 예상

2026-09-23 기준 서울 리전(`ap-northeast-2`)의 AWS Price List 단가와 월 730시간 상시 운영을
결합한 계획값이다. 로컬 실측 envelope를 넘는 최소 GPU 후보로 `g4dn.xlarge`
(4 vCPU, RAM 16 GiB, T4 16 GiB)를 사용했다. 모든 원화 표시는 실시간 환율이 아니라
`1 USD = 1,400 KRW` 계획 환율이고, 최종값에는 불확실성 예비비 15%를 더했다.

계산식, 평균 일간 환산, 각 AWS 구성요소의 금액과 비중, MQTT→Kafka→추론→DB/API 및
학습·MLOps 경로별 비용 대응은
[`AWS_DAILY_COST_AND_PIPELINE_BREAKDOWN_KO.md`](AWS_DAILY_COST_AND_PIPELINE_BREAKDOWN_KO.md)에
별도로 정리했다. 단가와 가정의 기계 판독 가능한 근거는
`artifacts/validation/aws-cost-estimate.json`이다.

| 시나리오 | 구성 | 기본 월 비용 | 예비비 15% 포함 | 계획 원화 |
|---|---|---:|---:|---:|
| Pilot all-in-one | GPU EC2 1대에 Kafka·PostgreSQL·MLflow·모니터링 함께 운영, HA 없음 | $512.73 | $589.64 | 약 82.5만원 |
| Managed baseline | GPU EC2 1대 + RDS Single-AZ + MSK Serverless | $1,312.17 | $1,509.00 | 약 211.3만원 |
| Managed HA | GPU EC2 2대 + RDS Multi-AZ + MSK Serverless | $2,011.97 | $2,313.77 | 약 323.9만원 |

Pilot 기본값 $512.73 중 GPU EC2가 $472.31로 약 92%다. Managed baseline에서는 MSK
Serverless의 클러스터 시간비만 월 $671.60이므로, 편의성 대신 고정비가 크게 증가한다.
따라서 첫 운영은 all-in-one으로 실제 트래픽·보존량을 계측한 뒤 RDS/MSK 분리를 결정하는 것이
비용 측면에서 가장 합리적이다. 다만 Pilot은 단일 장애 지점이며 Kafka·PostgreSQL 운영 책임이
팀에 남는다.

민감도는 다음과 같다.

- `g4dn.xlarge` On-Demand는 $0.647/시간, 1년 No Upfront 예약형은 $0.407/시간이다.
  부하가 안정된 뒤 예약형을 선택하면 GPU 컴퓨트만 월 $175.20(37.1%) 절감할 수 있다.
- 별도 GPU 노드에서 월 10시간 학습/파인튜닝만 실행하면 EC2 추가분은 약 $6.47이다.
  학습 데이터 저장·전송·오케스트레이션 비용은 별도다.
- 모델 추론을 24×7 제공하지 않고 GPU 노드를 중지할 수 있다면 EC2 비용은 가동 시간에 거의
  비례해 감소한다. 실시간 안전 서비스라면 이 절감 가정을 사용하면 안 된다.

공식 단가 출처는 [EC2/EBS Price List](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/ap-northeast-2/index.csv),
[RDS Price List](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonRDS/current/ap-northeast-2/index.csv),
[MSK Price List](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonMSK/current/ap-northeast-2/index.csv),
[S3 Price List](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/ap-northeast-2/index.csv),
[CloudWatch Price List](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/ap-northeast-2/index.csv)다.
네트워크·로드밸런서·DNS·요청 비용은 최종 토폴로지와 트래픽이 아직 없으므로 각각 월 $25,
$50, $100의 명시적 계획 allowance로 두었다. 세금, Support 플랜, 실제 환율, 인터넷 egress,
동시 기기 증가, 장기 보존 증가는 확정 견적 전에 다시 계산해야 한다.

재계산 명령은 다음과 같다.

```powershell
py -3 tools/estimate_aws_cost.py `
  --hours-per-month 730 `
  --usd-krw 1400 `
  --contingency-percent 15 `
  --output artifacts/validation/aws-cost-estimate.json
```

## 실행·재현

### 1. 모델 가져오기

```powershell
uv run python tools/import_segmentation_package.py `
  --archive "C:\path\InhouseSegmentationRealtime_AB_Triggers.zip" `
  --copy-checkpoint `
  --output artifacts/validation/dataset-registry.json
```

### 2. 서비스와 실제 모델 E2E

```powershell
docker compose --env-file compose/.env -f compose/docker-compose.dev.yml --profile model up -d
uv run python tools/validate_model_software_e2e.py `
  --mqtt-port 1884 `
  --timeout 180 `
  --output artifacts/validation/software-e2e.json
```

포트는 로컬 충돌에 맞게 `compose/.env`의 `MQTT_HOST_PORT`를 사용한다.

### 3. 학습·파인튜닝과 MLflow

```powershell
py -3 -m csi_fall_segmentation.training `
  --mode finetune `
  --feature-dir C:\path\InhouseSegmentationRealtime\data\features `
  --base-checkpoint weights\temporal_segmentation_fixed_delay.pt `
  --output-checkpoint artifacts\checkpoints\segmentation-finetuned.pt `
  --summary artifacts\validation\finetune.json `
  --device cuda --epochs 1 --batch-size 2 --freeze-encoders `
  --mlflow-uri http://127.0.0.1:5000 `
  --experiment wifiguard-segmentation `
  --registered-model wifiguard-inhouse-segmentation
```

전체 학습은 `--mode train`을 쓰고 `--freeze-encoders`를 제거한다. `--max-recordings`,
`--max-windows`, `--max-batches`는 smoke 범위를 제한하는 옵션이다.

### 4. 실시간 모니터링

```powershell
py -3 tools/resource_monitor.py `
  --host 0.0.0.0 --port 9108 --interval 1 `
  --output artifacts/resource-profiles/live-monitor.jsonl

docker compose --env-file compose/.env `
  -f compose/docker-compose.dev.yml `
  -f compose/docker-compose.obs.yml up -d
```

| 화면 | 주소 | 용도 |
|---|---|---|
| Live host monitor | `http://127.0.0.1:9108` | CPU·RAM·GPU·VRAM·전력·온도·디스크 즉시 확인 |
| Prometheus | `http://127.0.0.1:9090` | 지표 조회와 알람 평가 |
| Grafana | `http://127.0.0.1:3000/d/wifiguard-resources/wifi-guard-resources` | 시계열 운영 화면 |
| Alertmanager | `http://127.0.0.1:9093` | 용량/장애 알람 상태 |
| MLflow | `http://127.0.0.1:5000` | 실행·아티팩트·모델 버전 |

`resource_monitor.py`는 `/api/snapshot`, `/api/history`, `/metrics`를 제공한다. Prometheus가
`/metrics`를 2초마다 수집하고, Grafana의 `WIFI-GUARD Resources` 대시보드가 2초마다 갱신된다.

### 5. 개별 명령 리소스 측정과 AWS envelope 재계산

```powershell
py -3 tools/profile_command.py `
  --output artifacts/resource-profiles/my-run.json `
  --cwd . -- py -3 your_command.py

py -3 tools/estimate_aws_resources.py `
  --profiles artifacts/resource-profiles/inference-gpu.json `
             artifacts/resource-profiles/training-smoke.json `
             artifacts/resource-profiles/finetune-smoke.json `
  --dataset-registry artifacts/validation/dataset-registry.json `
  --headroom 1.5 --retained-checkpoints 10 `
  --output artifacts/validation/aws-resource-envelope.json
```

## 결과 파일

- `artifacts/validation/dataset-registry.json`
- `artifacts/validation/segmentation-reference-cpu.json`
- `artifacts/validation/training-smoke.json`
- `artifacts/validation/finetune-smoke.json`
- `artifacts/validation/finetune-mlflow.json`
- `artifacts/validation/software-e2e.json`
- `artifacts/validation/aws-resource-envelope.json`
- `artifacts/validation/aws-cost-estimate.json`
- `artifacts/resource-profiles/*.json`
- `monitoring/capacity-reviewed.json`

대용량 체크포인트, 학습 데이터, JSONL 원시 시계열은 Git에 커밋하지 않는다. 작은 검증 요약은
재현 근거로 보존할 수 있지만, 운영 배포의 최종 성능 인증으로 사용해서는 안 된다.
