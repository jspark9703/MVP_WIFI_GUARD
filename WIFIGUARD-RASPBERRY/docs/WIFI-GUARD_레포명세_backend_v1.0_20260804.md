# WIFI-GUARD-backend 레포 개발 명세서

**클라우드 서비스 백엔드 · 모델 서빙 · MLOps 인프라 (docker 모노레포)**

v1.0 · 작성일 2026-08-04

> 4-레포 분리 중 **클라우드** 레포의 명세다. MQTT 수신 이후의 모든 것을 담는다.
> 전제 문서: `CSI-Guard_인프라구축.md`(타깃 아키텍처), `CSI-Guard_완성목표_실행계획_v1.0_20260729.md`(G1·G3·G4·G5·G6), `FACILITY 구상도.md`
> 인접 레포: [raspberry](WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md) · [frontend](WIFI-GUARD_레포명세_frontend_v1.0_20260804.md)
>
> **이 문서의 §7(인터페이스 계약)이 4개 레포 공통 계약의 SSOT다.**

---

## 1. 레포 개요 · 책임 경계

### 1.1 한 줄 정의

AWS IoT Core로 들어온 엣지 데이터를 Kafka로 흘려 **저장·추론·드리프트 감시 세 갈래로 병렬 처리**하고, 낙상 확정 시 알림을 보내며, 드리프트 발생 시 스스로 재학습·배포하는 클라우드 스택. **docker-compose 기반 모노레포**로 로컬에서 전 구간을 재현할 수 있다.

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
                                                              │
                                          Few-shot/LoRA → MLflow Registry
                                          → Validation Gate → Hot-Reload
```

### 1.2 하는 것

| # | 책임 | 목표 |
|---|---|---|
| B1 | MQTT 수신 → Kafka 라우팅 (운영: IoT Rule / 개발: mqtt-bridge) | G2 |
| B2 | 시계열·관계형 영속 저장 (TimescaleDB + PostgreSQL) | G1 |
| B3 | **낙상 DL 추론 서빙** — 피처 → 확률 → 상태머신 | G3 |
| B4 | 서비스 API (REST + WebSocket 팬아웃) · 인증 · 테넌시 | G3 |
| B5 | 알림 라우팅 (**SMS + ntfy 병행**) + FACILITY 에스컬레이션 | G3 |
| B6 | 드리프트 감지 (Evidently → Prometheus → AlertManager) | G5 |
| B7 | 재학습 오케스트레이션 (Airflow `model_retrain_dag`) | G4 |
| B8 | 모델 레지스트리·버전 관리·Hot-Reload (MLflow) | G6 |
| B9 | 관측 (Prometheus / Grafana / Loki / OpenTelemetry) | G3·G5 |

### 1.3 하지 않는 것

| 항목 | 어디서 | 근거 |
|---|---|---|
| CSI 수집·재실감지 | 라즈베리파이 | G7 — 네트워크와 무관하게 끊기지 않아야 함 |
| 피처 추출 (S3/PCA-ACF) | 라즈베리파이 | 확정 결정. 백엔드는 완성된 텐서를 받는다 |
| 펌웨어·하드웨어 제어 | ESP / Pi | |
| UI 렌더링 | 프론트엔드 | 백엔드는 API·WS만 |

---

## 2. 핵심 기능 목록

| ID | 기능 | 상태 | 목표 |
|---|---|---|---|
| F-B01 | MQTT → Kafka 브리지 (로컬 개발용 Mosquitto 경로) | 신규 | G2 |
| F-B02 | Kafka 토픽 관리 (`csi-feature-stream` 외) | 신규 | G2 |
| F-B03 | Consumer 1: TimescaleDB 인제스트 | 신규 | G1 |
| F-B04 | Consumer 2: 낙상 추론 워커 | **이식+신규** | G3 |
| F-B05 | 모델 서빙 (TorchServe / ONNX Runtime) | **이식** | G3·G6 |
| F-B06 | 낙상 상태머신 (IDLE/SUSPECT/FALL/COOLDOWN) | **이식** | |
| F-B07 | Consumer 3: Evidently 드리프트 모니터 | 신규 | G5 |
| F-B08 | REST API (현행 19종 계약 승계) | **이식** | G3 |
| F-B09 | WebSocket 실시간 팬아웃 (Redis pub/sub) | **이식+확장** | G3 |
| F-B10 | 실인증 (JWT) · RBAC · 테넌시 스코핑 | 신규 | G3 |
| F-B11 | 관계형 스키마 + 마이그레이션 (SQLAlchemy/Alembic) | 신규 | G1 |
| F-B12 | 알림: **SMS 어댑터** | 신규 | G3 |
| F-B13 | 알림: **ntfy 어댑터** | **이식** | G3 |
| F-B14 | 알림 라우터 + FACILITY 에스컬레이션 + 감사 이력 | 신규 | G3 |
| F-B15 | 기기 프로비저닝 (인증서 발급·회수) | 신규 | G2 |
| F-B16 | Airflow `model_retrain_dag` | 신규 | G4 |
| F-B17 | MLflow 레지스트리 + Validation Gate + Hot-Reload | 신규 | G6 |
| F-B18 | Prometheus / Grafana / AlertManager / Loki | 신규 | G3·G5 |
| F-B19 | 오브젝트 스토리지 (모델 아티팩트·CSI 윈도우) | 신규 | G1·G6 |
| F-B20 | `packages/contracts` — 레포 간 스키마 SSOT | 신규 | |

---

## 3. 디렉토리 구조

```
WIFI-GUARD-backend/
├── README.md
├── Makefile                        up / down / logs / migrate / seed
├── compose/
│   ├── docker-compose.dev.yml      전 스택 로컬 재현 (Mosquitto 포함)
│   ├── docker-compose.prod.yml     운영 (MQTT는 AWS IoT Core로 외부화)
│   ├── docker-compose.obs.yml      관측 스택 (선택 기동)
│   └── .env.example
├── infra/                          ── 미들웨어 설정 (코드 아님)
│   ├── kafka/                      토픽 정의, 파티션·보존 정책
│   ├── timescaledb/                init.sql, hypertable·retention·연속집계
│   ├── postgres/                   init.sql
│   ├── redis/
│   ├── mosquitto/                  로컬 개발 브로커 (mosquitto.conf, ACL)
│   ├── prometheus/                 prometheus.yml, rules/
│   ├── alertmanager/               alertmanager.yml (Airflow webhook 라우트)
│   ├── grafana/                    provisioning/, dashboards/
│   ├── loki/
│   ├── mlflow/
│   └── airflow/
├── services/
│   ├── api/                        ── FastAPI 서비스 계층
│   │   ├── Dockerfile
│   │   └── src/wifiguard_api/
│   │       ├── main.py
│   │       ├── routers/            monitor, devices, residents, falls,
│   │       │                       calibration, config, notify, ws
│   │       ├── auth/               JWT, RBAC, 테넌시 의존성
│   │       ├── realtime/           Redis pub/sub → WS 팬아웃
│   │       └── deps.py
│   ├── ingest/                     ── Consumer 1
│   │   └── src/wifiguard_ingest/
│   │       ├── consumer.py         aiokafka → TimescaleDB 배치 삽입
│   │       └── schema.py
│   ├── inference/                  ── Consumer 2
│   │   └── src/wifiguard_inference/
│   │       ├── consumer.py         Kafka → 서빙 호출 → result topic
│   │       ├── state_machine.py    ★ backend/detector.py에서 이식
│   │       ├── decoder.py          역양자화 + zstd 해제 (엣지 codec의 짝)
│   │       └── hot_reload.py       MLflow 신 버전 로드
│   ├── model-serving/              ── 모델 서빙
│   │   └── src/wifiguard_serving/
│   │       ├── engine.py           ★ backend/inference/engine.py 이식
│   │       ├── model.py            ★ backend/inference/model.py 이식
│   │       └── handler.py          TorchServe 핸들러 / ONNX 러너
│   ├── drift/                      ── Consumer 3
│   │   └── src/wifiguard_drift/
│   │       ├── monitor.py          Evidently 리포트 (KS-Test, PSI)
│   │       ├── exporter.py         Prometheus Exporter
│   │       └── baseline.py         ABSENT baseline 관리
│   ├── notification/               ── 알림
│   │   └── src/wifiguard_notify/
│   │       ├── router.py           수신자별 채널 라우팅
│   │       ├── escalation.py       FACILITY 무응답 에스컬레이션
│   │       ├── audit.py            감사 이력 (법적 증빙)
│   │       └── adapters/
│   │           ├── sms.py          ★ 신규
│   │           ├── ntfy.py         ★ backend/notifier.py 이식
│   │           └── ars.py          스텁
│   ├── provisioning/               기기 등록·인증서 발급/회수
│   └── mqtt-bridge/                로컬 개발: Mosquitto → Kafka
│                                   (운영에서는 IoT Rule이 대체)
├── dags/
│   ├── model_retrain_dag.py        G4·G6 재학습 파이프라인
│   └── tasks/                      추출 / 적응학습 / 검증게이트 / 등록 / 배포
├── packages/
│   ├── contracts/                  ★ MQTT·Kafka·API 스키마 SSOT (Pydantic v2)
│   │   └── src/wifiguard_contracts/
│   │       ├── mqtt.py             PresenceMsg, TelemetryMsg, WindowMsg, CmdMsg, AckMsg
│   │       ├── kafka.py            FeatureRecord, InferenceResult
│   │       └── api.py              REST/WS 스키마 → OpenAPI → 프론트 코드젠
│   └── db/
│       └── src/wifiguard_db/
│           ├── models/             SQLAlchemy 2.x
│           └── migrations/         Alembic
└── tools/
    ├── seed.py                     개발용 시드 데이터
    └── replay_edge.py              엣지 페이로드 재생 (Pi 없이 개발)
```

---

## 4. 구현 단계

### Phase 0 — 계약 고정 + 로컬 스택 기동

**먼저 `packages/contracts`를 만든다.** 4개 레포가 이 스키마 하나를 참조해야 이후 병렬 개발이 가능하다.

- MQTT 페이로드 5종 (§7.1)
- Kafka 레코드 2종
- REST/WS 스키마 → OpenAPI 생성 → 프론트 타입 코드젠

그다음 `docker-compose.dev.yml`로 전 스택이 로컬에서 뜨는 것을 확인한다. **Mosquitto를 로컬 개발 브로커로** 두어 AWS 계정 없이도 전 구간을 재현할 수 있게 한다. 운영에서는 이 자리를 AWS IoT Core가 대체하며, **토픽·페이로드 계약은 동일**하다.

```
docker compose -f compose/docker-compose.dev.yml up
  mosquitto  kafka  zookeeper/kraft  timescaledb  postgres  redis
  prometheus  grafana  alertmanager  loki  mlflow  airflow
```

**완료 기준**
- [ ] `make up` 한 번으로 전 스택 기동
- [ ] `tools/replay_edge.py`가 실제 Pi 페이로드 포맷으로 Mosquitto에 퍼블리시 → Kafka 토픽에 도달
- [ ] `packages/contracts`에서 생성한 OpenAPI가 프론트 코드젠에 사용 가능

---

### Phase 1 — G1 영속 저장

#### 1-1. 저장 계층 이원화

| 성격 | 저장소 | 이유 |
|---|---|---|
| 이벤트·응답 이력, 계정·시설·기기·거주자 (저빈도, 관계형) | **PostgreSQL** | 시설·입소자·권한 모델에 적합 |
| 재실/움직임 지표, 피처 통계 (고빈도 시계열) | **TimescaleDB** | 압축·자동 retention·연속집계 |
| 모델 아티팩트, 업로드된 CSI/피처 윈도우 | **S3 / GCS** | |

고빈도 테이블에는 **다운샘플링 + 자동 만료(retention)**를 건다.

#### 1-2. ERD 확정

`CSI-Guard_데이터모델_ERD_v1.0.docx`의 후보 스키마를 현행 도메인 모델과 대조해 확정한다. 반드시 반영할 것:

- `Resident.deviceIds` **다대다 매핑** (한 거주자가 화장실+샤워실 등 복수 기기)
- `facilityId` **시설 스코핑** — DB 쿼리 레벨에서 강제
- FACILITY 추가 엔티티: **ZONE**, **GATEWAY**, 교대 스케줄, 담당 배정, 에스컬레이션 규칙, 감사 로그
- 서비스 XOR 스코프: `facilityId` (FACILITY) vs `ownerUserId` (HOME)

#### 1-3. 리포지토리 계층

현행 백엔드는 전부 인메모리다. 읽기 지점마다 리포지토리를 끼우고 Alembic으로 스키마를 버전 관리한다.

**완료 기준**
- [ ] Alembic 마이그레이션으로 스키마 생성·롤백 가능
- [ ] TimescaleDB hypertable + retention 정책 적용 확인
- [ ] FACILITY/HOME 스코핑이 쿼리 레벨에서 강제되는지 테스트

---

### Phase 2 — Consumer 1 (인제스트) + 관측 기반

`aiokafka` 컨슈머가 `csi-feature-stream`의 `presence`/`telemetry` 레코드를 배치로 TimescaleDB에 넣는다. Grafana 대시보드로 실시간 지표를 본다.

**주의**: `window`(피처 텐서)는 TimescaleDB에 넣지 않는다 — 232KB/윈도우다. 오브젝트 스토리지에 두고 DB에는 포인터만 저장하며, **드리프트 분석·재학습에 필요한 구간만 보존**한다.

**완료 기준**
- [ ] 4Hz × N기기 인제스트가 지연 없이 처리
- [ ] Grafana에 재실 상태·MV·RSSI 시계열 표시
- [ ] retention 정책으로 오래된 고빈도 데이터 자동 만료 확인

---

### Phase 3 — Consumer 2 (추론) + 모델 서빙 ★ 핵심

#### 3-1. 모델 계약 (변경 금지)

```
입력 A: S3 스칼로그램   (224, 224)   float32
입력 B: PCA-ACF        (1, 128, 64) float32
정규화: 체크포인트 내 normalization{feature_a{mean,std}, feature_b{mean,std}}
전처리: prepare_resnet_image — 1ch → 3ch 복제 → bilinear (224,224)
        (ACF 128×64도 이 단계에서 224×224로 확대됨)
모델:   DualBranchResNet(backbone=resnet18, embedding_dim=512,
                         fusion_hidden_dim=512, dropout=0.3)
        인코더 2개 독립(가중치 비공유) → concat 1024 → LayerNorm
        → Linear(512) → ReLU → Dropout → Linear(2)
출력:   softmax(logits, dim=1)[0, 1]     # class 1 = fall
```

> `DualBranchResNet`은 **torchvision이 아니라 자체 구현 ResNet18**(BasicBlock 2-2-2-2)이다. 체크포인트 `model_config`에서 구조를 읽어 재구성한다.

#### 3-2. 판정 로직 (`services/inference/state_machine.py`)

```
STRIDE_SEC        = 0.25
DEFAULT_THRESHOLD = 0.468
MODE_SIZE         = 5
COOLDOWN_SECONDS  = 10.0

raw_pred = int(proba >= threshold)
recent   = deque(maxlen=5)
majority = int(sum(recent)*2 > len(recent))   # deque가 5개 찼을 때만, 아니면 0

COOLDOWN 중이면 전이 차단 (cooldown_until)
majority → FALL (fall_count++, 알림 발행)
FALL 이탈 → COOLDOWN 10초
그 외 → raw면 SUSPECT, 아니면 IDLE
```

> **인과(causal) 다수결이다.** 오프라인 검증에서 쓴 `mode5`는 중심 윈도우 기준이라 미래 윈도우 2개가 필요해 실시간에 쓸 수 없다. 이 대체가 원안과 동등한 성능인지는 **연구단 확인 대기 중인 오픈 아이템**이다(§9-2).

**검증 성능** (`Window3BestModelInference` 기준, 375 윈도우 / 낙상 24):

| 설정 | Macro F1 | 낙상 Recall | TN/FP/FN/TP |
|---|---:|---:|---|
| 임계값 0.5 | 0.7926 | 0.6250 | 341/10/9/15 |
| **임계값 0.468** | **0.8004** | **0.7083** | 338/13/7/17 |
| 0.468 + mode5 | 0.8936 | 0.7500 | 348/3/6/18 |

> 이 검증셋은 모델·임계값·후처리 선택에 사용되었으므로 **독립 테스트셋이 아니다.**

#### 3-3. 지연 예산

현행 실측 **약 42ms/window**(피처 27ms + MPS 추론 15ms, 개발 PC). 백엔드에서는 피처를 Pi가 만들어 오므로 **추론만** 남지만, **엣지→클라우드 왕복이 더해진다.** 0.25초 stride 예산을 왕복 포함으로 다시 계산해야 한다.

CPU로도 처리 가능한 수준이므로 **GPU는 동시 기기 수가 커진 뒤에 검토**한다. 초기에는 CPU 오토스케일이 경제적이다.

#### 3-4. 서빙 선택

| 옵션 | 비고 |
|---|---|
| **TorchServe** | 배치·동시성 처리. 인프라구축 원안 |
| **Triton** | 더 강력하나 운영 부담 큼 |
| **FastAPI + ONNX Runtime** | 경량. G6의 ONNX 변환 경로와 공유되어 엣지 폴백까지 재사용 가능 |

→ 초기에는 **경량 옵션으로 시작하고, 동시 기기 수가 늘면 TorchServe로 전환**을 권장한다. 요청 계약을 "윈도우 피처 → 낙상 확률" 단일 형태로 좁혀 두면 교체 비용이 낮다.

**완료 기준**
- [ ] Pi 페이로드 → 역양자화 → 추론 → `csi-inference-result` 발행 E2E
- [ ] 양자화 전후 `proba_fall` 차이가 허용 오차 이내 (엣지 codec과 짝을 이뤄 검증)
- [ ] `POST /detection/config`로 임계값·쿨다운 실시간 변경이 적용
- [ ] 왕복 포함 지연이 250ms 예산 이내

---

### Phase 4 — G3 서비스 API + 알림

#### 4-1. API 계약 승계

현행 백엔드의 **REST 19종 + WS 1종**을 그대로 승계한다. 프론트엔드 마이그레이션 비용을 최소화하기 위해서다.

| Method | Path | 비고 |
|---|---|---|
| GET | `/` | 서비스 식별 |
| GET | `/ports` | (엣지 이관 후 의미 변경 — 기기 목록으로) |
| GET | `/monitor/status` | 시리얼+버퍼+감지+재실+알림 통합 상태 |
| POST | `/monitor/start` · `/monitor/stop` | (MQTT cmd로 대체) |
| GET | `/monitor/detect` | 감지기 상태 + 최근 60초 확률 히스토리 |
| GET | `/monitor/window` | 최근 N초 원시 CSI 요약 |
| POST | `/onboarding/calibrate/start` | → MQTT `cmd` 발행 |
| GET | `/onboarding/calibrate/status` | ← MQTT `ack` 집계 |
| GET/POST | `/presence/config` | → MQTT `cmd` 발행 (엣지 설정) |
| GET/POST | `/detection/config` | 클라우드 추론 파라미터 (모델 미로드 시 409) |
| GET/POST | `/notify/recipients` | |
| PATCH/DELETE | `/notify/recipients/{id}` | |
| POST | `/notify/recipients/{id}/test` · `/notify/test` | |
| WS | `/ws/live` | 10Hz 통합 텔레메트리 |

**신규**: 인증(`/auth/*`), 기기 프로비저닝(`/devices/{id}/provision`), 이력(`/falls`, `/events`), 시설 관리.

**`/ws/live` 계약 원칙 (반드시 유지)**: 재실 필드(`presence_state`, `mv_current`, `wander_current` …)는 **엣지가 연결되어 있으면 항상** 존재하고, 낙상 필드(`proba_fall`, `detect_state` …)는 **모델이 로드된 경우에만** 추가된다. 재실과 낙상의 독립성이 이 계약으로 표현된다.

#### 4-2. 실시간 팬아웃

단일 프로세스 브로드캐스트를 **Redis pub/sub**로 옮긴다. 다수 클라이언트가 다수 기기를 구독하는 구조가 되기 때문이다.

#### 4-3. 알림 — SMS + ntfy 병행

```
services/notification/
  router.py           수신자별 채널 라우팅. 채널 실패가 다른 채널을 막지 않음
  escalation.py       FACILITY: 1차 담당 요양보호사 → (T1 무응답) → 2차 당직 간호사
                                → (T2 무응답) → 3차 시설장/관리자
  audit.py            모든 발송·응답을 감사 이력에. 법적 증빙 가능해야 함(변조 방지)
  adapters/
    sms.py            ★ 신규. 국내 사업자(NHN Toast / 알리고) 또는 Twilio
    ntfy.py           ★ backend/notifier.py 이식
    ars.py            스텁 (외부 음성 API 연동 필요)
```

**ntfy 어댑터 이식 시 유지할 특성**: 수신자마다 독립 스레드+큐(최대 32건)를 둬서 발송 지연이 감지 루프를 막지 않게 한다. 실패 시 최대 3회, 백오프 1초/2초, 최종 실패는 카운트만 남기고 포기.

**중요한 구조적 사실 (현행 유지)**: 휴대폰 푸시는 `추론 → notifier → ntfy/SMS → 단말` 경로로 가고 **프론트/백엔드의 HTTP·WebSocket을 거치지 않는다.** 앱 내부의 전체화면 알람은 `/ws/live`의 `detect_state`를 구독하는 **완전히 별개 경로**다. 두 경로는 "상태머신이 FALL로 전이한다"는 트리거만 공유한다. **이 이중화를 유지**해야 한 쪽이 죽어도 알림이 전달된다.

#### 4-4. 테넌시 · 인증

- `facilityId` 스코핑을 **DB 쿼리 레벨에서 강제** (현재는 프론트 목업 수준)
- ROOT / MEMBER / USER 역할을 실제 인가로 구현
- `localStorage` mock 세션 → **JWT** (Authlib + JWT, 시설 다계정이 복잡해지면 Keycloak)

#### 4-5. 가용성 설계

클라우드 장애·네트워크 단절 시:
- 재실감지는 엣지에서 **유지** (G7)
- 낙상 감지는 **중단**
- → **이 상태를 사용자에게 명확히 표시해야 한다.** 안전 기능이므로 무증상 중단은 허용되지 않는다.

**완료 기준**
- [ ] 프론트가 기존 API 계약으로 클라우드에 붙어 동작
- [ ] SMS·ntfy 양 채널 발송 + 실패 격리 확인
- [ ] FACILITY 에스컬레이션 시나리오 (1차 무응답 → 2차) 동작
- [ ] 엣지 단절 시 낙상 감지 중단이 UI에 표시

---

### Phase 5 — G5 드리프트 감지

#### 5-1. Consumer 3 (Evidently)

`csi-feature-stream`을 모니터링하며, 초기 캘리브레이션/학습 데이터(baseline)와 현재 입력의 통계적 거리를 계산한다 — **KS-Test, PSI** 등.

**드리프트 지표 후보**: baseline 대비 MV 분포 이동, wander 비율 추세, RSSI·노이즈플로어 장기 변화, AGC 게인 변동 흔적.

**오탐 억제 (중요)**: 각 지표는 "사람이 있어서 생긴 변화"와 구분되어야 한다. → **ABSENT 구간만 비교**하는 규칙을 둔다.

스트리밍 변화점 검출에는 **River**(ADWIN, Page-Hinkley)를 병용할 수 있다.

#### 5-2. 알람 체인

```
Evidently Drift Score → Prometheus Exporter → Prometheus scrape
  → AlertManager (임계 초과) → Airflow Webhook → model_retrain_dag
```

#### 5-3. 행동 정의

감지 시 (a) 사용자에게 재캘리브레이션 **제안**, (b) 자동 재적응 **실행** 중 무엇을 할지. **안전 기능이므로 초기에는 (a) 제안까지만** 두는 편이 안전하다(§9-4).

`devices` 화면에 드리프트 추세를 시계열로 노출한다.

**완료 기준**
- [ ] Drift Score가 Prometheus에 노출되고 Grafana에 표시
- [ ] AlertManager → Airflow webhook 호출 성립
- [ ] ABSENT 구간 필터로 정상 활동이 드리프트로 오탐되지 않음

---

### Phase 6 — G4·G6 재학습 · 모델 배포

#### 6-1. `model_retrain_dag`

```
① extract        TimescaleDB + S3에서 해당 공간의 최근 소량 데이터(Few-shot)
                 + ABSENT baseline 추출
② adapt          Pre-trained 백본(ResNet) 동결 → 분류 헤드(MLP) 또는
                 LoRA 어댑터만 미세조정
③ validate       ★ Validation Gate — 이전 모델 대비 성능 저하가 없는지 확인
④ register       MLflow Model Registry에 신규 버전 등록
                 (예: v1.2.0-fewshot-roomA). Loss, Macro F1, 하이퍼파라미터,
                 아티팩트 기록
⑤ deploy         게이트 통과 시 Consumer 2에 API/Signal → Hot-Reload
```

> **Validation Gate는 필수다.** 낙상 감지는 안전 기능이므로 "적응했더니 나빠졌다"가 무증상으로 지나가면 안 된다. 적응 전 파라미터로 되돌리는 **롤백 경로**도 함께 둔다.

#### 6-2. 적응 기법 후보 (실행계획 §5.2)

| 접근 | 방식 | 라벨 | 적합성 |
|---|---|---|---|
| **분류 헤드만 재학습** | 백본 동결, 512×2 결합 임베딩 위 MLP만 학습 | 소량 | 난이도 최저 — **1순위** |
| **어댑터 / LoRA** (`peft`) | 백본 동결, 소수 파라미터만 공간별 저장 | 소량 | 산출물이 작아 G6 배포 부담 최소 |
| Prototypical / metric learning | 공간별 프로토타입 임베딩 거리 | 클래스당 수 샘플 | 낙상 샘플 확보가 관건 |
| **TTA (TENT 계열)** | BatchNorm 통계·affine만 무라벨 갱신 | **불필요** | **무라벨 경로 1순위** |
| CORAL / MMD 정렬 | 소스·타깃 특징 분포 정렬 | 불필요 | 경량, TTA와 병용 가능 |

권장 조합: **무라벨 TTA를 기본으로 깔고, 설치 시 유도 동작 몇 회를 받을 수 있으면 헤드 재학습/LoRA를 얹는 2단 구조.**

#### 6-3. ⚠ 학습 데이터 원칙 — 문서 간 충돌 해소

`인프라구축.md` 시나리오 C는 사용자가 "오탐지"로 응답한 시점의 CSI 윈도우를 **Negative Sample로 라벨링해 재학습에 활용**한다고 기술한다.

그러나 `구현현황_및_완성목표` G4와 `FACILITY 구상도` §3.5는 명시적으로 반대다:

> "적응 신호는 **설치 공간에서 수집한 CSI 자체**다. 사용자·보호자가 앱에서 남기는 응답(확인/출동/오탐지)은 **학습에 반영하지 않는다** — 응답 이력은 운영 지표와 사후 분석용으로만 유지한다."

→ **G4 원칙을 채택한다.** 사용자 응답은 학습에 반영하지 않고, 운영 지표·사후 분석 전용으로만 보존한다. 이유: 응답의 신뢰도를 검증할 방법이 없고(보호자가 상황을 정확히 알지 못할 수 있음), 안전 기능의 학습 신호로 쓰기에는 노이즈가 크다. 향후 이 원칙을 바꾸려면 **응답 신뢰도 검증 절차**를 먼저 설계해야 한다.

#### 6-4. 모델 레지스트리 (G6)

배포 대상이 **두 갈래**가 된다:
- 클라우드 낙상 모델: 공간별 적응 결과(전체 체크포인트 / 어댑터 가중치 / 통계)
- 엣지 재실 모델(향후): 양자화 ONNX → 라즈베리파이로 OTA

| 용도 | 도구 |
|---|---|
| 모델 레지스트리·실험 추적 | **MLflow** (자체호스팅) |
| 데이터·모델 버저닝 | **DVC** (역할 분담: DVC=데이터, MLflow=모델) |
| 아티팩트 저장소 | S3 / GCS |
| 추론 이식성 | ONNX Runtime |
| 엣지 OTA | AWS IoT Jobs 또는 자체 구현 |

무결성 검증(서명·체크섬)과 롤백·회귀 추적은 필수다. **모델 교체는 안전 기능 변경이다.**

> **현황 주의**: MLflow는 현재 이 프로젝트 어디에서도 실제로 쓰이고 있지 않다. `apps/research/*/train.py`에 `MLFLOW_TRACKING_URI` 환경변수를 읽는 죽은 설정 필드가 하나 있을 뿐이다. 실제 실험 기록은 **DVC params + `training_summary.json` + git tag**로 이루어진다. MLflow 도입은 **완전한 신규 작업**으로 계획해야 한다.

**완료 기준**
- [ ] DAG가 수동 트리거로 전 단계 완주
- [ ] Validation Gate가 성능 저하 모델을 실제로 차단
- [ ] Hot-Reload가 서비스 중단 없이 적용
- [ ] 직전 버전 즉시 롤백 가능

---

## 5. 핵심 구현 모듈 상세

### 5.1 `packages/contracts` — 레포 간 SSOT

4개 레포가 공유하는 유일한 코드 자산이다. Pydantic v2 모델로 정의하고, 여기서 OpenAPI를 생성해 프론트 타입을 코드젠한다.

```python
# mqtt.py
class PresenceMsg(BaseModel):
    state: Literal["present", "absent"]
    mv_current: float; wander_current: float
    mv_threshold: float; wander_baseline: float
    wander_ratio_threshold: float; wander_ratio: float
    wander_confirmed: bool
    last_activity_at: float; seconds_since_activity: float
    just_changed: bool

class WindowMsg(BaseModel):
    t_start: float; t_end: float; fs_hz: float; window_samples: int
    s3: bytes; acf: bytes                 # 양자화 + zstd
    s3_scale: float; s3_offset: float
    acf_scale: float; acf_offset: float
    feature_ms: float

class TelemetryMsg(BaseModel):
    rssi: int; noise_floor: int; agc_gain: int; hz_1s: float
    buffered_seconds: float; reconnects: int
    gating_ratio: float; spool_backlog: int
    cpu_temp_c: float | None; throttled: bool
```

### 5.2 `services/model-serving/engine.py` (이식)

`backend/inference/engine.py`에서 이식하되:

| 항목 | 변경 |
|---|---|
| 체크포인트 경로 | 로컬 파일 경로 → **MLflow Registry + S3** |
| Windows `PosixPath` 몽키패치 | **제거 가능** (Linux 컨테이너 전용) |
| 디바이스 선택 `auto` | cuda → mps → cpu 순 → 컨테이너에서는 cuda → cpu |
| `warmup()` | 유지 — 첫 요청 지연 제거 |

체크포인트 dict 키: `model_state_dict`, `model_config{backbone, embedding_dim, fusion_hidden_dim, dropout, image_size}`, `normalization{feature_a{mean,std}, feature_b{mean,std}}`, `epoch`, `val_metrics`.

### 5.3 `services/notification/router.py` (신규)

```python
class NotificationRouter:
    """수신자별로 활성 채널에 병렬 발송. 채널 실패가 다른 채널을 막지 않는다."""
    def dispatch(self, event: FallEvent, recipients: list[Recipient]) -> DispatchResult: ...
```

각 어댑터는 독립 스레드+큐를 가진다(현행 `notifier.py` 구조 승계). 발송·실패·드롭 카운트를 노출해 `/notify/recipients` 응답과 Prometheus 지표에 함께 싣는다.

---

## 6. 현재 구현 코드에서 참고·이식할 코드

### 6.1 이식 매핑

| 현재 위치 | 신규 위치 | 변경 |
|---|---|---|
| [backend/inference/engine.py](../backend/inference/engine.py) | `services/model-serving/src/wifiguard_serving/engine.py` | MLflow 로딩, PosixPath 패치 제거 |
| [backend/inference/model.py](../backend/inference/model.py) | `services/model-serving/src/wifiguard_serving/model.py` | 무변경 |
| [backend/detector.py](../backend/detector.py) (상태머신 부분) | `services/inference/src/wifiguard_inference/state_machine.py` | 링버퍼 의존 제거. "확률 시퀀스 → 상태"로 축소 |
| [backend/notifier.py](../backend/notifier.py) | `services/notification/.../adapters/ntfy.py` | 스레드+큐·재시도 구조 유지 |
| [backend/main.py](../backend/main.py) (REST 19종 + WS) | `services/api/.../routers/*` | 계약 유지, 라우터 분리, DB·MQTT 연동 |
| `Window3BestModelInference/weights/best_model.pt` | MLflow Registry + S3 | 89MB, 현재 gitignore. 초기 등록 필요 |
| `Window3BestModelInference/scripts/train_losnlos_resnet18_s3_acf_dual_end2end.py` | `dags/tasks/adapt.py` | 재학습 원본. SAM 옵티마이저·HuberizedCE·WeightedRandomSampler 포함 |
| `Window3BestModelInference/scripts/evaluate_feature_pair_domain_robustness.py` | `dags/tasks/validate.py` | Validation Gate의 원본 |
| `Window3BestModelInference/infer_validation.py` | `tools/` | mode5 후처리(`moving_mode(size=5)`) 참조 |
| `csi_fall/dwt_analysis/` (`dvc.yaml`, `dvc.lock`, `src/dwt_coef/`, `config/`) | 학습 파이프라인 참조 | **실제로 동작하는 유일한 DVC 파이프라인** |
| `csi_fall/esp32c5/tools/stride/` | `tools/collector/` (선택) | 다기기 CSI 수집 도구. 데이터 수집 세션용 |

### 6.2 참조만 하고 이식하지 않는 것

| 위치 | 이유 |
|---|---|
| `csi_fall/csi_fall_monorepo/` | **파이썬 본문이 100% `NotImplementedError` 스텁**이다. 다만 uv workspace + PEP-420 네임스페이스 레이아웃과 `ARCHITECTURE.md`/`DVC_GUIDE.md`의 문서 구성은 참고할 만하다. `pyproject.toml`의 workspace 멤버 경로가 실제 디렉토리와 어긋나 `uv sync`가 깨져 있는 것도 반면교사 |
| [backend/presence/](../backend/presence/), [backend/features/](../backend/features/), [backend/csi/](../backend/csi/), [backend/onboarding.py](../backend/onboarding.py) | **엣지(Pi) 소관.** [raspberry 명세서](WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md) 참조 |
| `csi_fall/infra/raspberry/fall_detector.py` | 규칙 기반 낙상 감지. DL 모델과 무관한 별개 계보 |
| `Guardian Angel Alert/reference/fall_detect/` | 구 MV 임계값 파이프라인 (gitignored, 로컬 전용) |

---

## 7. 인터페이스 계약 — **4개 레포 공통 SSOT**

### 7.1 엣지 → 클라우드 (MQTT)

```
[Raspberry Pi] ──MQTT over TLS 8883, mTLS, 아웃바운드 전용──► [AWS IoT Core]
                                                                    │ IoT Rule
                                                                    ▼
                                                              [ Kafka ]
```

**토픽** (구 `csiguard/` → **`wifiguard/`**로 리네임):

| 토픽 | 방향 | QoS | 주기 | 페이로드 |
|---|---|---|---|---|
| `wifiguard/{facility}/{device}/presence` | Pi→ | 1 | 4Hz | `PresenceMsg` (11개 스칼라) |
| `wifiguard/{facility}/{device}/telemetry` | Pi→ | 1 | 저빈도 (~5s) | `TelemetryMsg` |
| `wifiguard/{facility}/{device}/window` | Pi→ | 0 | **게이팅 시에만** 4Hz | `WindowMsg` (S3 + ACF, 양자화+zstd+MessagePack) |
| `wifiguard/{facility}/{device}/cmd` | →Pi | 1 | 이벤트 | `calibrate` / `set_config` / `set_mode` / `ping` |
| `wifiguard/{facility}/{device}/ack` | Pi→ | 1 | 이벤트 | 커맨드 결과, 캘리브레이션 페이즈 |

- **직렬화**: MessagePack (JSON은 고빈도에 오버헤드가 큼)
- **압축**: zstd + float16/int8 양자화
- **보안**: 기기별 인증서(mTLS), **토픽 ACL로 기기별 격리**. 아웃바운드 8883만 사용하므로 가정 공유기 NAT 뒤에서 포트포워딩·고정 IP 불필요
- HOME 계정은 `{facility}` 자리에 `home-{userId}` 형태를 쓴다

### 7.2 Kafka 토픽

| 토픽 | 생산자 | 소비자 | 레코드 |
|---|---|---|---|
| `csi-feature-stream` | IoT Rule (운영) / mqtt-bridge (개발) | Consumer 1·2·3 | `FeatureRecord` (presence + window + 메타) |
| `csi-telemetry` | 동상 | Consumer 1 | `TelemetryMsg` |
| `csi-inference-result` | Consumer 2 | API(WS 팬아웃), 알림, Consumer 1 | `InferenceResult{proba_fall, state, threshold, ts}` |

### 7.3 클라우드 → 프론트엔드

- **REST**: `packages/contracts/api.py` → OpenAPI → 프론트 타입 코드젠
- **WebSocket** `/ws/live` 10Hz: 재실 필드는 엣지 연결 시 **항상**, 낙상 필드는 모델 로드 시에만 (§4-1)

### 7.4 SPI 계약 (ESP ↔ Pi)

[esp 명세서 §7.1](WIFI-GUARD_레포명세_esp_v1.0_20260804.md)이 원본이다. 백엔드는 관여하지 않는다.

---

## 8. 리스크

| # | 리스크 | 영향 | 완화 |
|---|---|---|---|
| R1 | 스택 규모 대비 인력 부족 (Kafka+Airflow+MLflow+Evidently+Prometheus는 신규 도입만 5개) | 완주 실패 | Phase 순서 엄수. G1(저장) → 추론 → 알림까지가 최소 동작 세트. MLOps 루프(Phase 5·6)는 뒤로 |
| R2 | 월 고정비가 처음 발생하는 지점 | 비용 초과 | **비용 상한을 먼저 정할 것.** 기기 수에 따른 비용 곡선 모델링 |
| R3 | 왕복 지연이 0.25초 예산을 넘김 | 낙상 감지 지연 | Phase 3 완료 기준에 포함. 초과 시 리전 조정 또는 엣지 폴백 재검토 |
| R4 | 인과 다수결이 mode5와 동등하지 않을 수 있음 | 성능 저하 | 연구단 확인 대기 (§9-2). 대안: 250ms 지연 허용하고 준-중심 윈도우 사용 |
| R5 | 양자화 오차가 확률을 흔듦 | 오탐/미탐 | 엣지 codec과 짝지어 검증 (Phase 3) |
| R6 | 테스트 러너 부재 | G3 규모 리팩터링 위험 | pytest 도입을 Phase 0에 포함 권장 (§9-6) |
| R7 | MLflow가 실제로는 미도입 상태 | 일정 과소 추정 | 완전 신규 작업으로 계획 (§4 Phase 6-4 주석) |
| R8 | 다인실에서 "누가 낙상했는가" 판별 불가 | FACILITY 확장 제약 | 1인 공간(화장실)부터 단계적 롤아웃 (§9-7) |

---

## 9. 미결정 사항

1. **클라우드 사업자·리전·비용 상한.** G1 착수의 선행 조건. 국내 사용자 대상이라면 국내 리전 우선이며, 이 결정이 이후 모든 관리형 서비스 선택을 구속한다. 개인정보 관련 검토와 직결.

2. **인과 다수결 ↔ mode5 동등성.** 오프라인 검증의 mode5는 중심 윈도우 기준(미래 2개 필요)이라 실시간에 쓸 수 없어 인과 다수결로 대체했다. **동등 성능인지 연구단 확인 대기 중인 오픈 아이템.** 검증 결과에 따라 250ms 지연을 감수하고 준-중심 윈도우를 쓰는 절충이 가능하다.

3. **적응 데이터에 라벨을 받을 것인가.** 설치 시 유도 동작(걷기·앉기 등) 몇 회를 요구할지가 G4 기법 선택 전체를 좌우한다. 사용자 부담 vs 성능 트레이드오프. 무라벨만 → TTA 경로, 소량 라벨 → Few-shot 경로.

4. **자동 재적응 허용 범위.** 드리프트 감지 시 시스템이 스스로 모델을 바꾸게 할 것인가, 사람 승인을 받을 것인가. **초기에는 승인 방식을 권한다.**

5. **모델 서빙 스택 확정.** TorchServe / Triton / FastAPI+ONNX. 요청 계약을 좁혀 두면 교체 비용이 낮으므로, 경량으로 시작해 규모에 따라 전환하는 것을 권장.

6. **테스트 체계 도입.** 현재 프론트·백엔드 모두 러너가 없다. G3 규모 리팩터링을 테스트 없이 진행하는 것은 위험이 크다(후보: pytest, Vitest, Playwright). **별도 합의 사항.**

7. **다인실 지원.** 재실은 이진, 낙상은 윈도우 단위라 "2인실에서 누가 낙상했는가"를 현 구조로 판별할 수 없다. 로드맵은 **1인 공간(화장실)부터** 시작한다.

8. **온프레미스 하이브리드.** `FACILITY 구상도` §6은 대형 시설·회선 불안정 사이트를 위해 낙상 추론을 현장 소형 서버에 두고 클라우드는 관리·이력·모델 배포만 맡는 옵션을 제시한다. 시설별 선택지로 열어둘지 결정 필요.

9. **프라이버시 서사 개정.** 클라우드 전환으로 "로컬 완결" 주장이 무효화된다. 공모전 보고서 §5.4, 논문초록의 관련 문구를 **"필요한 구간만, 암호화해서, 규정된 리전에"**로 다시 써야 한다. 개인정보 처리방침·저장 위치·보존 기간은 별도 검토 항목.

---

## 참고 문서

- `CSI-Guard_인프라구축.md` — 전체 아키텍처, 시나리오 A/B/C (이 문서 §1.1·§4 Phase 5·6의 원본)
- `CSI-Guard_완성목표_실행계획_v1.0_20260729.md` §2(G1) §3(G2) §4(G3) §5(G4) §6(G5) §7(G6) §10(신규 스택 총정리)
- `CSI-Guard_구현현황_및_완성목표_v1.0_20260729.md` §3·§4 (완성 목표)
- `FACILITY 구상도.md` §3(5계층) §5(규모 산정) §6(온프레미스) §9.4(미결정)
- `CSI-Guard_기능명세서_v2.0_20260715.md` §2.3 (현행 API 19종), §2.4 (구현 예정)
- `CSI-Guard_데이터모델_ERD_v1.0.docx` — 후보 ERD
- `Window3BestModelInference/README_KO.md`, `PACKAGE_MANIFEST.json` — 모델 계약·검증 성능
- [raspberry 명세서](WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md) · [frontend 명세서](WIFI-GUARD_레포명세_frontend_v1.0_20260804.md) · [esp 명세서](WIFI-GUARD_레포명세_esp_v1.0_20260804.md)
