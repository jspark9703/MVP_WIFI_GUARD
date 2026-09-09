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

> ## ⚠ 현재 상태: 골격 + 이식 원본 배치만 완료. 실행되지 않는다.
>
> 미들웨어 compose 뼈대와 이식 원본은 자리를 잡았다. **애플리케이션 코드는 없다.**
> 이식본(`services/api/main.py`, `services/inference/state_machine.py`)은 엣지로 넘어간
> 모듈을 import하므로 **지금 상태로는 import되지 않는다** — 의도된 것이다.
> 무엇이 남았는지는 [PORTING.md](PORTING.md) §2를 볼 것.

---

## 책임 경계

| 하는 것 | 하지 않는 것 |
|---|---|
| MQTT 수신 → Kafka 라우팅 | CSI 수집·**재실감지** → 라즈베리파이 (네트워크와 무관하게 끊기지 않아야 함) |
| 시계열·관계형 영속 저장 | **피처 추출 (S3/PCA-ACF)** → 라즈베리파이. 백엔드는 **완성된 텐서를 받는다** |
| **낙상 DL 추론 서빙** — 피처 → 확률 → 상태머신 | 펌웨어·하드웨어 제어 → ESP / Pi |
| 서비스 API (REST + WS 팬아웃) · 인증 · 테넌시 | UI 렌더링 → 프론트엔드 |
| 알림 라우팅 (**SMS + ntfy 병행**) + 에스컬레이션 | |
| 드리프트 감지 · 재학습 오케스트레이션 · 모델 레지스트리 | |

---

## 구조

```
compose/            docker-compose.dev.yml (미들웨어) · obs.yml (관측) · .env.example   [뼈대 · 미검증]
infra/              미들웨어 설정 (코드 아님) — 전부 비어 있다
services/
  api/              FastAPI 서비스 계층    ← backend/main.py 이식 (REST 19종 계약 원본)
  inference/        Consumer 2            ← backend/detector.py 이식 (상태머신)
  model-serving/    모델 서빙             ← backend/inference/{engine,model}.py 이식
  notification/     알림                  ← backend/notifier.py → adapters/ntfy.py 이식
  ingest/ drift/ provisioning/ mqtt-bridge/                                    [비어 있음]
dags/               model_retrain_dag                                          [비어 있음]
packages/
  contracts/        ★ MQTT·Kafka·API 스키마 SSOT — 여기가 먼저다                [비어 있음]
  db/               SQLAlchemy 2.x + Alembic                                   [비어 있음]
tools/              infer_validation.py (mode5 참조)
docs/               명세 문서
_reference/         Window3BestModelInference (109MB) · dwt_analysis · collector-stride
```

## 기동 — 서비스 API (HOME/FACILITY CRUD + JWT, 2026-09-08 구현)

```bash
uv sync                                  # uv workspace → 루트 .venv (packages/db · packages/contracts · services/api)
docker start wg-pg                       # 로컬 PostgreSQL 16 (개발 PC 드라이런 컨테이너, 5432, wifiguard/devpass)
make migrate                             # alembic upgrade head  (DATABASE_URL, 기본 로컬)
make seed                                # 목업 시드 재현 (root@demo.io / member@demo.io / home@demo.io · 비밀번호 demo)
make api                                 # http://127.0.0.1:8000/docs  (/api/v1)
make test                                # pytest 32개 (wifiguard_test DB 자동 생성)
make openapi                             # packages/contracts/openapi.json → 프론트 bun run api:types
bash tools/smoke.sh                      # curl 스모크
```

구조: `packages/db`(SQLAlchemy 2 모델 + Alembic `0001_initial`, 테이블 11개) · `packages/contracts/api.py`(Pydantic camelCase 스키마 = OpenAPI SSOT) · `services/api/src/wifiguard_api/{app.py, deps.py(Scope·스코핑), auth/, routers/}`. 설계·결과는 `../REVIEW_20260907.md` §9 참조.

## 기동 — 미들웨어 compose (뼈대, 미검증)

```bash
cp compose/.env.example compose/.env    # 비밀번호를 실제 값으로 채울 것
make up                                  # 미들웨어 전 스택
make obs                                 # + 관측 스택
make ps / make logs / make down
```

`make replay`는 대상 코드가 없어 실패한다.

**⚠ compose는 미검증이다.** `infra/mosquitto/mosquitto.conf`, `infra/timescaledb/init.sql`, `infra/prometheus/prometheus.yml` 등 마운트 대상 파일이 아직 없어 그대로 올리면 일부 컨테이너가 실패한다. 현재 1차 클라우드 토폴로지는 compose 를 쓰지 않는다(`deploy/aws/README.md`).

---

## 모델 계약 (변경 금지)

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
