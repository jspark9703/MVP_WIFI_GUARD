# WIFI-GUARD Backend (클라우드)

MQTT로 들어온 엣지 데이터를 Kafka로 흘려 **저장·추론·드리프트 감시 세 갈래로 병렬 처리**하고, 낙상 확정 시 알림을 보내며, 드리프트 발생 시 스스로 재학습·배포하는 클라우드 스택. **docker-compose 모노레포**로 로컬에서 전 구간을 재현한다.

```
                              [ AWS IoT Core ]
                                     │ IoT Rule Engine Direct Routing
                                     ▼
                              [ Apache Kafka ]  csi-feature-stream
                                     │
      ┌──────────────────────────────┼──────────────────────────────┐
      ▼                              ▼                              ▼
 Consumer 1: Ingest           Consumer 2: Inference         Consumer 3: Drift
 (aiokafka Worker)            (TorchServe / ONNX)           (Evidently Monitor)
      │                              │                              │
      ▼                              ▼                              ▼
 [ TimescaleDB ]              csi-inference-result          [ Prometheus Exporter ]
 (재실/피처 시계열)                   │                              │
      │                    ┌─────────┴─────────┐                    ▼
      ▼                    ▼                   ▼           [ Prometheus / AlertManager ]
 [ Grafana ]        [ API · WebSocket ]  [ SMS / ntfy ]              │ Airflow Webhook
 (통합 관제)          (앱 실시간 알람)    (낙상 응급알림)              ▼
                                                            [ Airflow DAG ]
                                                       model_retrain_dag
```

> ## 현재 상태 (2026-09-10)
>
> **재실 경로가 관통한다.** 엣지 MQTT → 브리지 → Kafka → 인제스트 → TimescaleDB · 최신값 캐시
> → REST(`ResidentOut` 런타임 필드) · WebSocket(`/ws/live`)까지 실측 검증했다.
> 30초 재생에서 브리지 248건 전달·거부 0, `presence_samples` 118행 적재, 오류 0.
>
> **동작한다**: 서비스 API `/api/v1` 27경로 + `/ws/live` + JWT + 테넌시,
> PostgreSQL(Alembic `0001`, 테이블 11개) + TimescaleDB(`presence_samples`),
> 계약 5종(`packages/contracts/`), pytest **77개**.
> AWS EC2 2대(db·api) 배포 — [deploy/aws/README.md](deploy/aws/README.md).
> **AWS 에는 아직 Kafka·MQTT 인스턴스가 없다**(M4). 설정이 없으면 인제스트는 꺼진 채로 뜨고
> CRUD 는 정상 동작한다.
>
> **모델 실행 경로까지 구현됐다**: `services/csi-fall-pipeline`의 학습 체크포인트를
> `services/inference`가 로드하고, Kafka `csi-feature-stream`의 1-D 대표신호를 학습과 동일한
> ACF/CWT 피처로 변환해 `csi-inference-result`에 발행한다. 인제스트는 causal mode5 상태머신,
> WebSocket 캐시, `fall_events(source="EDGE")` idempotent 저장, ntfy 알림 큐까지 연결한다.
> 저장소에 없는 것은 실제 학습 가중치뿐이며, 인수 계약은 [docs/MODEL_HANDOFF.md](docs/MODEL_HANDOFF.md)다.
>
> **실행 대상이 아닌 파일**: `services/api/.../main.py`는 구 로컬 백엔드의 REST 19종 + `/ws/live`
> 계약 원본으로 보존한 것이며, 엣지로 넘어간 모듈을 import하므로 import 자체가 불가능하다.
> `app.py`가 마운트하지 않는다. 운영 추론 진입점은 `python -m wifiguard_inference`다.
>
> 남은 작업은 [PORTING.md](PORTING.md) §2, 계획은 `~/.claude/plans/rosy-wobbling-balloon.md`.

---

## 책임 경계

| 하는 것 | 하지 않는 것 |
|---|---|
| MQTT 수신 → Kafka 라우팅 | CSI 수집·**재실감지** → 라즈베리파이 (네트워크와 무관하게 끊기지 않아야 함) |
| 시계열·관계형 영속 저장 | **서브캐리어 선택·PCA 합성** → 라즈베리파이. 백엔드는 **1-D 대표신호를 받는다** |
| **S3 스칼로그램 + PCA-ACF 변환** — 대표신호 → 텐서 | 펌웨어·하드웨어 제어 → ESP / Pi |
| **낙상 DL 추론 서빙** — 텐서 → 확률 | UI 렌더링 → 프론트엔드 |
| 낙상 상태 판정 · `fall_events` 생성 · 알림 라우팅 | |
| 서비스 API (REST + WS 팬아웃) · 인증 · 테넌시 | |
| 드리프트 감지 · 재학습 오케스트레이션 · 모델 레지스트리 | |

> **경계가 2026-09-10에 바뀌었다.** 이전 설계는 Pi가 S3(224,224)+PCA-ACF(1,128,64) 텐서
> **233KB**를 만들어 올리는 것이었으나, 4Hz 기준 7.5Mbps/기기라 업링크로 성립하지 않았고
> Pi 벤치 p90이 489ms로 250ms 스트라이드 예산도 넘겼다. 이제 절단점은
> `select_pc_signal()`이며 Pi는 약 2KB만 올린다(117배 감소). CWT 변환 비용이 클라우드로
> 옮겨왔으므로 **처리량이 이 서비스의 제약**이 된다 — 계획서 R1 참조.

---

## 구조

```
deploy/aws/         EC2 부팅 스크립트 · SSM · systemd — 1차 클라우드 토폴로지  [db·api 기동됨]
compose/            docker-compose.dev.yml (API·인제스트·선택 모델 포함) · obs.yml
infra/              Mosquitto · PostgreSQL · TimescaleDB · 관측 설정
services/
  api/              FastAPI 서비스 계층 — app.py · routers/ 9종 · auth/        [동작]
    .../realtime/   /ws/live 팬아웃 + GET /realtime/schema                     [동작]
    .../main.py     구 로컬 백엔드 REST 19종 + /ws/live 계약 원본       [참조 전용 · import 불가]
  ingest/           MQTT→Kafka 브리지 · 컨슈머 · 최신값 캐시 · WS 허브          [동작]
                    라이브러리다 — 실행 주체는 api 의 lifespan (워커 1 고정)
  model-serving/    구/신 체크포인트 호환 로더·추론 엔진                         [동작]
  csi-fall-pipeline/ raw CSI→피처→학습→오프라인 예측 독립 패키지       [21 pass, 2 skip]
  inference/        Kafka 신호→동일 피처→모델→InferenceResult                    [동작]
  notification/     ntfy 비동기 큐·백오프; 확정 FALL 뒤 수신자별 호출             [동작]
  mqtt-bridge/      → ingest 로 통합됨 (README 참조)
  drift/ provisioning/                                                         [비어 있음]
dags/               model_retrain_dag                                          [비어 있음]
packages/
  contracts/        ★ 4레포 스키마 SSOT                                        [동작]
    api.py          REST (camelCase) → openapi.json → 프론트 코드젠
    mqtt.py         presence · signal · telemetry · cmd · ack (snake_case)
    kafka.py        FeatureRecord · StatusRecord · InferenceResult
    realtime.py     /ws/live 프레임
    topics.py       MQTT·Kafka 토픽 조립·파싱
  db/               SQLAlchemy 2 모델 11개 + Alembic 0001                      [동작]
tools/              seed.py · export_openapi.py · smoke.sh · infer_validation.py
docs/               명세 문서
_reference/         Window3BestModelInference (109MB) · dwt_analysis · collector-stride
```

## 기동 — 서비스 API (CRUD only)

```bash
uv sync                                  # uv workspace → 루트 .venv
docker start wg-pg                       # 로컬 PostgreSQL 16 (5432, wifiguard/devpass)
make migrate                             # alembic upgrade head
make seed                                # 목업 시드 (root@demo.io / member@demo.io / home@demo.io · 비밀번호 demo)
make api                                 # http://127.0.0.1:8000/docs
make test                                # pytest 77개 (wifiguard_test DB 자동 생성)
make openapi                             # openapi.json + realtime.schema.json → 프론트 코드젠 입력
bash tools/smoke.sh                      # curl 스모크
```

## 기동 — 실시간 경로까지 (재실 엔드투엔드)

인제스트는 **환경변수가 있을 때만** 켜진다. 없으면 조용히 꺼진 채로 뜨고 CRUD 는 정상이다.

```bash
docker start wg-pg wg-ts wg-kafka wg-mosq       # postgres · timescale · kafka · mosquitto

export DATABASE_URL="postgresql+psycopg://wifiguard:devpass@127.0.0.1:5432/wifiguard"
export TSDB_DSN="postgresql://wifiguard:devpass@127.0.0.1:5433/wifiguard_ts"
export MQTT_HOST=127.0.0.1 MQTT_PORT=1883 MQTT_TLS=0
export KAFKA_BOOTSTRAP=127.0.0.1:9092
export JWT_SECRET="개발용-32바이트-이상-비밀값"

uv run uvicorn wifiguard_api.app:app --port 8000 --workers 1   # ★ 워커 1 (아래 참조)
curl -s localhost:8000/health | jq .ingest                     # 브리지·컨슈머·싱크 실측 상태
```

기기를 하나 등록해 그 `mqttTopic` 의 tenant/device 를 엣지 `config/device.toml` 에 넣고
`python -m wifiguard_edge --transport replay` 를 돌리면 재실이 화면까지 흐른다.

통합 작업장 루트에서는 하드웨어 없이 실제 MQTT→Kafka 적재를 반복 검증할 수 있다.

```powershell
.\scripts\validate-mock-storage.ps1 -NetworkName wifiguard-dev_default
```

이 검증은 먼저 계약 메시지 3종을 직접 발행하고, 이어서 Raspberry 합성 replay를 구동한다.
각 실행은 새 UUID를 사용하며 Kafka에서 해당 레코드를 다시 읽어 Pydantic 계약으로 검증한다.

> **워커는 1개여야 한다.** `wifiguard_ingest` 는 라이브러리이고 실행 주체가 이 앱의
> lifespan 이다. Kafka 컨슈머와 최신값 캐시가 **이 프로세스의 인메모리 상태**라, 워커를
> 늘리면 각자 별도 컨슈머 그룹 멤버가 되어 메시지가 분산되고 캐시가 쪼개진다.
> 늘리려면 먼저 Redis pub/sub 을 도입해야 한다.

### 실시간 데이터가 흐르는 길

```
엣지 MQTT ──► mqtt_bridge ──► Kafka ──► consumers ──┬─► presence_sink ─► presence_samples (TSDB)
 (presence 4Hz                (토픽 검증)            ├─► telemetry_sink ─► devices.online
  signal 4Hz                                        └─► LiveCache ─┬─► REST ResidentOut 런타임 필드
  telemetry 1Hz)                                                   └─► LiveHub ─► /ws/live
```

브리지는 **페이로드의 신원 주장을 믿지 않는다** — 토픽에서 파싱한 `device_id`/`tenant_id` 가
정본이고, 페이로드가 다르게 주장하면 버린다. 그러지 않으면 자격증명이 샌 기기 하나가
다른 테넌트의 재실 이력을 위조할 수 있다.

구조: `packages/db`(모델 11개 + Alembic) · `packages/contracts`(계약 5종) ·
`services/api/{app.py, deps.py, auth/, routers/, realtime/}` · `services/ingest`(브리지·컨슈머·캐시·허브).
설계·결과는 `../REVIEW_20260907.md` §9 와 계획서 참조.

## 기동 — 로컬 E2E compose

```bash
cp compose/.env.example compose/.env    # 비밀번호를 실제 값으로 채울 것
make up                                  # API·브로커·DB 전 스택(모델 제외)
make model-up                            # weights/model.pt를 포함한 추론 E2E
make obs                                 # + 관측 스택
make ps / make logs / make down
```

기존 `make replay` 대상은 없으며, 대신 루트 `scripts/validate-mock-storage.ps1`을 사용한다.

compose 정적 해석(`docker compose ... config --quiet`)은 검증했다. 현재 작업 환경에서는 Docker
Desktop 데몬이 꺼져 있어 컨테이너 기동 회귀는 수행하지 못했다. 가중치 인수 뒤에는
`make model-check`, `make model-up`, `tools/validate_live_inference_kafka.py` 순서로 검증한다.

---

## 모델 계약

운영 권장 체크포인트는 `csi-fall-pipeline` 형식이며, 현재 1-D 대표신호 와이어 계약에서는
`feature=legacy_map`, `cwt=true`만 정확히 재현할 수 있다. 상세 인수 조건은
[docs/MODEL_HANDOFF.md](docs/MODEL_HANDOFF.md)를 따른다. 아래 형식은 기존 체크포인트 호환 계약이다.

```
입력 A: S3 스칼로그램   (224, 224)   float32
입력 B: PCA-ACF        (1, 128, 64) float32
정규화: 체크포인트 내 normalization{feature_a{mean,std}, feature_b{mean,std}}
전처리: prepare_resnet_image — 1ch → 3ch 복제 → bilinear (224,224)
모델:   DualBranchResNet(backbone=resnet18, embedding_dim=512,
                         fusion_hidden_dim=512, dropout=0.3)
        인코더 2개 독립(가중치 비공유) → concat 1024 → LayerNorm
        → Linear(512) → ReLU → Dropout → Linear(2)
출력:   softmax(logits, dim=1)[0, 1]     # class 1 = fall
```

`DualBranchResNet`은 **torchvision이 아니라 자체 구현 ResNet18**(BasicBlock 2-2-2-2)이다. 체크포인트 `model_config`에서 구조를 읽어 재구성한다.

**검증 성능** (375 윈도우 / 낙상 24):

| 설정 | Macro F1 | 낙상 Recall | TN/FP/FN/TP |
|---|---:|---:|---|
| 임계값 0.5 | 0.7926 | 0.6250 | 341/10/9/15 |
| **임계값 0.468** | **0.8004** | **0.7083** | 338/13/7/17 |
| 0.468 + mode5 | 0.8936 | 0.7500 | 348/3/6/18 |

> 이 검증셋은 모델·임계값·후처리 선택에 사용되었으므로 **독립 테스트셋이 아니다.**

## 세 축의 임계값 — 절대 섞지 말 것

| 값 | 출처 | 척도 | UI 라벨 |
|---|---|---|---|
| `threshold` = **0.468** | 모델 (**여기**) | **확률** 0~1 | **"판정 임계값"** / "낙상 확률 임계값" |
| `presence_mv_threshold` | 캘리브레이션 (**Pi**) | MV 스케일, 기본 2.0 | "움직임 임계값" |
| `wander_baseline` | 캘리브레이션 (**Pi**) | Welch PSD 스케일, 기본 0.5 | "재실 baseline" |

## 지연 예산

현행 실측 **약 42ms/window** (피처 27ms + MPS 추론 15ms, 개발 PC). 백엔드에서는 피처를 Pi가 만들어 오므로 **추론만** 남지만 **엣지→클라우드 왕복이 더해진다.** 0.25초 stride 예산을 왕복 포함으로 다시 계산해야 한다 (명세 R3). CPU로 처리 가능한 수준이므로 **GPU는 동시 기기 수가 커진 뒤에 검토**한다.

---

## 문서

- [PORTING.md](PORTING.md) — 이식 매핑 · 남은 작업 · 지켜야 할 계약 · 대용량 자산 방침
- [docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md) — 이 레포의 명세. **§7이 4레포 공통 계약 SSOT다**
- [docs/CSI-Guard_인프라구축.md](docs/CSI-Guard_인프라구축.md) — 전체 아키텍처, 시나리오 A/B/C
- [docs/CSI-Guard_완성목표_실행계획_v1.0_20260729.md](docs/CSI-Guard_완성목표_실행계획_v1.0_20260729.md) — G1~G7 실행 계획
- [docs/FACILITY 구상도.md](docs/FACILITY%20구상도.md) — 5계층 · 규모 산정 · 온프레미스 옵션
- [docs/CSI-Guard_데이터모델_ERD_v1.0.docx](docs/CSI-Guard_데이터모델_ERD_v1.0.docx) — 후보 ERD
