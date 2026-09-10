# CSI-Guard 구현 현황

**4-레포 분리 이후 실제로 동작하는 것 · 확정된 아키텍처 결정 · 남은 작업**

작성일 2026-09-10 · v2.0 · 대상 `csi_fall/MVP_WIFI_GUARD/`

> 이 문서가 **현재 구현 상태의 정본**이다. `CSI-Guard_구현현황_및_완성목표_v1.0_20260729.md`
> (v1.1)는 단일 로컬 프로세스 시절을 기술한 것이라 아키텍처 서술이 더 이상 맞지 않는다.
> 다만 그 문서의 **완성 목표 G1~G7과 우선순위는 여전히 유효**하다 — §9 참조.

---

## 1. 요약

**재실감지 결과가 라즈베리파이에서 브라우저 화면까지 관통한다.** 엣지가 CSI를 받아 재실을
판정하고 1-D 합성 대표신호를 만들어 MQTT로 올리면, 클라우드가 Kafka를 거쳐 시계열 DB에
적재하고 WebSocket으로 화면에 밀어 준다. 전 구간을 로컬에서 실측 검증했다.

**낙상은 아직 흐르지 않는다.** 모델 서빙 컨슈머가 없어 `fall` 필드가 항상 비어 있고,
화면은 "낙상 감지 미동작"을 표시한다. **그것이 "이상 없음"이 아니라는 점이 이 시스템의
안전 계약이다.**

| 축 | 상태 |
|---|---|
| 재실감지 (엣지 → 화면) | **동작** — 실측 검증 완료 |
| 서비스 API · 인증 · 테넌시 | **동작** — `/api/v1` 27경로, pytest 43 |
| 계약(MQTT·Kafka·WS·REST) | **확정** — 5개 모듈, 파리티 테스트로 고정 |
| 낙상 추론 | 미착수 — 엔진만 있고 컨슈머 없음 |
| 알림 발송 | 미착수 — `POST /recipients/{id}/test` 는 501 |
| 클라우드 배포 | **인스턴스 없음** — §8 참조 |
| ESP↔Pi SPI | 미착수 — 펌웨어에 SPI slave 0건, UART로 동작 중 |

테스트: 백엔드 **77** · 엣지 **77** · 프론트 **34**.

---

## 2. 지금 동작하는 파이프라인

```
[ESP32-C5 TX] ──ESP-NOW 5GHz ch48, 200pps──► [ESP32-C5 RX]
                                                   │ UART 2 Mbaud (magic 0xA55A, 46B 헤더, CSI 최대 612B)
                                                   ▼
                                          [Raspberry Pi · wifiguard_edge]
                                    RingBuffer ─┬─► PresenceLoop  0.25s 상시 ──► presence 4Hz
                                                └─► FeatureLoop   0.25s 게이트 ─► signal   4Hz (약 3.2KB)
                                                                  telemetry 1Hz (퇴실 0.2Hz)
                                                   │ MQTT (개발 1883 평문 / 운영 8883 TLS)
                                                   ▼
                                          [Cloud · wifiguard_ingest]
                                    mqtt_bridge ──► Kafka ──► consumers ─┬─► presence_samples (TimescaleDB)
                                    (토픽 신원 검증)  3토픽             ├─► devices.online / last_seen_at
                                                                        └─► LiveCache ─┬─► REST 런타임 필드
                                                                                       └─► LiveHub ─► /ws/live
                                                                                                        │
                                                                                          [Web · TanStack Start]
```

### 실측 (2026-09-10, 로컬 전 스택 · 재생 30초)

| 지점 | 값 |
|---|---|
| 브리지 전달 | **248건**, 거부 0, 오류 0 |
| 컨슈머 처리 | presence 118 · signal 104 · telemetry 26 |
| `presence_samples` 적재 | **118행** (퇴실 15 → 재실 103) |
| REST `/residents` | presence PRESENT · mv · wander · lastActivityAt · online=true |
| WebSocket | hello → live(재실 11필드 전부) → pong · **파싱 실패 0건** |
| 엣지 업링크 | signal 평균 **3,213B** × 4Hz ≈ **103 kbps/기기** |
| 엣지 연산 | `signal` p90 **13.7ms** (250ms 예산의 1/18) |
| 클라우드 연산 | `tensor` p90 **818ms** ← 처리량 제약 (§7 R1) |

프론트의 실제 파서를 살아 있는 서버에 물려 `hello(protocol=1)` → `live(online=true,
presence, mv, seconds_since_activity)` 수신을 확인했다.

---

## 3. 레포별 상태

| 레포 | 동작하는 것 | 없는 것 |
|---|---|---|
| **WIFIGUARD-ESP** | `csi_send`/`csi_recv`/`csi_recv_calibrate` 펌웨어. UART 2 Mbaud 바이너리 프레임. `train` 명령 기반 AGC 캘리브레이션 | **SPI slave 0건**. `tools/fall_detect` 등 참조 도구는 옛 baud(921600) 가정 |
| **WIFIGUARD-RASPBERRY** | 엔트리포인트·설정 로더·게이팅·피처 루프·MQTT 3종·재생 전송. `python -m wifiguard_edge` 로 기동. pytest 77 | 캘리브레이션 `cmd` 처리(등록된 명령은 `ping`뿐) · 단절 스풀 · SPI 전송 · **실기 미검증** |
| **WIFIGUARD-BACKEND** | `/api/v1` 27경로 + `/ws/live` + 인제스트(브리지·컨슈머·싱크·캐시·허브). 계약 5종. pytest 77 | 모델서버 컨슈머/핸들러 · 낙상 상태머신 배선 · `fall_events` `source="EDGE"` · 알림 발송 · 드리프트/MLOps |
| **WIFIGUARD-FRONTEND** | 12라우트 실 API + 실시간 구독(다기기·지수 백오프·런타임 파서) + 낙상 알람 경로 + 링크 진단. Vitest 34 | 낙상 표시(백엔드 대기) · 캘리브레이션 실경로 · 클라우드 배포 |

4개 폴더 모두 아직 `git init` 전이다. 선행 조건은 ESP 원본 레포의 Wi-Fi 비밀번호 회전과
미커밋 변경 정리다 (`WIFIGUARD-ESP/PORTING.md` §3).

---

## 4. 확정된 아키텍처 결정

2026-09-10에 사용자 승인으로 확정했다. 이전 명세와 다른 부분이 있으므로 여기가 정본이다.

### D1. 엣지는 **1-D 합성 대표신호**까지만 만든다

절단점은 `features/common.py` 의 `select_pc_signal()` 반환값이다. S3 스칼로그램과 PCA-ACF
변환은 **클라우드 모델서버**가 한다.

기존 설계(엣지가 233KB 텐서 생성)를 폐기한 이유는 두 가지다. 4Hz 기준 **7.5Mbps/기기**로
업링크가 성립하지 않았고, 개발 PC 벤치에서 전체 p90이 **489ms**로 250ms 스트라이드 예산을
넘었다. 분해 후 실측은 엣지 몫 p90 13.7ms, 업링크 **1/117**이다.

신호는 **float32 무손실**로 보낸다. 양자화하면 클라우드의 `q_metric`이 바뀌어 S3 디노이즈
강도가 달라지고, 학습 때와 다른 피처가 만들어진다. zstd는 절약분이 25kbps/기기라 기기 100대
전에는 의미가 없다.

> 모델 입력 계약 (224,224) + (1,128,64)과 임계값 0.468은 **바뀌지 않았다.** 변환 위치만 옮겼다.

### D2. ESP↔Pi는 UART, **921600 → 2,000,000**

펌웨어에 SPI slave 코드가 없고, Pi의 SPI 프레임 정의(CSI 128B/64서브캐리어)가 펌웨어가 실제로
내보내는 612B/306서브캐리어를 담지 못한다. 모든 축이 어긋나 부분 수정이 아닌 재작성 사안이라
후속으로 미뤘다.

baud를 올린 것은 **921600이 이미 병목**이었기 때문이다.

| | 921600 | 2,000,000 |
|---|---:|---:|
| 660B 프레임 전송 | 7.16 ms | 3.30 ms |
| 프레임 상한 | 약 140 fps | 약 303 fps |
| 실측 수신율 167Hz 대비 | 초과 | 여유 1.8배 |

펌웨어 자체의 최적화 문서가 UART TX 루프를 패킷당 7.16ms로 측정해 두었고, 이는 Wi-Fi 콜백을
그만큼 막는다. 167Hz면 초당 1.2초가 필요해 100%를 넘는다.

> **새 펌웨어를 플래시하면 921600을 가정한 기존 도구는 멈춘다.** 영향 범위는
> `WIFIGUARD-ESP/README.md` 의 baud 절에 표로 정리했다.

### D3. 추론 결과는 Kafka `csi-inference-result` 경유

모델서버는 **확률까지만** 낸다. 상태 판정과 `fall_events` 생성은 백엔드 서비스 영역이다.
"클라우드 백엔드가 유저 상태관리·낙상 알림을 맡는다"는 설계 의도에 맞춘 경계다.

### D4. 재실을 먼저 관통시키고 낙상은 그 배선을 재사용

재실은 엣지 코드가 이미 동작하므로 가장 빨리 화면에 실데이터가 뜨고, 그 배선(MQTT→Kafka→
캐시→WS)을 낙상이 그대로 쓴다.

### D5. 인제스트는 라이브러리, 실행 주체는 API 프로세스

`ResidentOut` 런타임 필드를 채우려면 REST 핸들러가 최신값 캐시를 **동기로 읽어야** 하고,
WebSocket 팬아웃도 같은 주소 공간이어야 한다.

> **uvicorn 워커는 1개여야 한다.** 워커가 여럿이면 각자 별도 Kafka 컨슈머 그룹 멤버가 되어
> 메시지가 분산되고 캐시가 쪼개진다. systemd 유닛에 이유를 주석으로 박았다.

---

## 5. 계약 — `WIFIGUARD-BACKEND/packages/contracts/`

4개 레포가 공유하는 유일한 코드 자산이다.

| 모듈 | 내용 | 케이스 |
|---|---|---|
| `api.py` | REST 요청·응답 → `openapi.json` → 프론트 코드젠 | **camelCase** |
| `mqtt.py` | `PresenceMsg` · `SignalMsg` · `TelemetryMsg` · `CmdMsg` · `AckMsg` | snake_case |
| `kafka.py` | `FeatureRecord` · `StatusRecord` · `InferenceResult` | snake_case |
| `realtime.py` | `/ws/live` 프레임 → `realtime.schema.json` → 프론트 코드젠 | snake_case |
| `topics.py` | MQTT·Kafka 토픽 조립·파싱 | — |

### 이름 규칙 — 전 구간에서 한 번도 바뀌지 않는다

```
PresenceStatus (엣지 dataclass) = presence_samples (DB 컬럼) = PresenceMsg (MQTT) = PresenceBlock (WS)
   state · mv_current · wander_current · mv_threshold · wander_baseline
   wander_ratio_threshold · wander_ratio · wander_confirmed
   last_activity_at · seconds_since_activity · just_changed
```

양 끝은 원래 일치했는데 중간의 `presence_loop._payload()` 하나가 3개를 `presence_*`로
개명하고 `seconds_since_activity`를 통째로 버리고 있었다. 그 탓에 DB 컬럼 하나가 영원히
비어 있을 운명이었고 아무 테스트도 잡지 못했다. 그 리네임을 제거했고, 이제
`test_contract_parity.py`(백엔드)·`test_presence_payload.py`(엣지)·
`realtime-contract.test.ts`(프론트)가 세 지점에서 일치를 강제한다.

`api.py`만 camelCase인 이유는 프론트가 쓰던 이름을 유지해야 하기 때문이다.

### MQTT 토픽

```
wifiguard/{tenant}/{device}/{presence | telemetry | signal | cmd | ack}
```

`{tenant}`는 FACILITY면 시설 UUID, HOME이면 `home-{userId}`. 토픽은 **서버가 발급**한다.
`window` leaf는 D1으로 폐기됐다.

브리지는 **페이로드의 신원 주장을 믿지 않는다.** 토픽에서 파싱한 `device_id`/`tenant_id`가
정본이고, 페이로드가 다르게 주장하면 버린다. 그러지 않으면 자격증명이 샌 기기 하나가 다른
테넌트의 재실 이력을 위조할 수 있다.

### `/ws/live`

```
클라이언트 → 서버   auth(첫 프레임, 5초 안) → subscribe / unsubscribe / ping
서버 → 클라이언트   hello → live* → pong / error
```

`{link, presence, fall}` **중첩** 구조다. 구 계약은 평탄한 dict라 재실의 `mv_threshold`와
낙상의 `threshold`가 충돌해 `presence_*` 접두가 필요했는데, 중첩이 그 문제를 구조로 없앴다.

인증을 프레임으로 받는 이유: 브라우저 WebSocket API는 커스텀 헤더를 붙일 수 없고, 토큰을
URL에 넣으면 프록시·서버 접근 로그에 남는다.

---

## 6. 안전 계약 — 세 가지 3상태

무증상 침묵을 금지하는 것이 이 시스템의 핵심 요구다. **"모름"과 "이상 없음"은 다르다.**

| 대상 | null | false / ABSENT | true / PRESENT |
|---|---|---|---|
| `DeviceLive.fall` | **낙상 감지 미동작** | — | 판정값 있음 |
| `DeviceLive.presence` | **재실 감지 미동작** | 퇴실 | 재실 |
| `Device.online` | **모름**(텔레메트리 받은 적 없음) | 연결 끊김 | 수신 중 |

`Device.online`은 원래 필수 boolean이라 "모름"을 담지 못했고, 등록만 하고 연결된 적 없는
기기가 "연결 끊김"으로 **단정** 표시됐다. DB 컬럼은 NOT NULL 그대로 두고 응답 스키마만
nullable로 낮춰 해결했다(마이그레이션 불필요).

게이트가 닫혀 있었다는 사실도 `TelemetryMsg.signals_gated`로 **항상** 보고한다.

---

## 7. 남은 작업

### M4 — 클라우드 배포
Kafka·MQTT 인스턴스 기동, 보안그룹 정비, systemd 환경변수. `user-data-kafka.sh`는 완성돼
있어 기동만 하면 된다. 비용은 인스턴스 4대 기준 월 약 62달러 추정.

### M5 — 낙상 추론
1. **[첫 작업]** `causal_mode5` vs `centered_mode5` 정량 비교 (R2)
2. 모델서버 `handler.py`/`consumer.py`, 체크포인트 경로 버그 수정
3. `state_machine.py` 축소 — 링버퍼·피처 의존 제거, "확률 시퀀스 → 상태"
4. `fall_sink.py` → `fall_events` INSERT(`source="EDGE"`) + `duration_s` UPDATE
5. `NotificationRouter` + `POST /recipients/{id}/test` 501 해소

### M6 — 백로그
디노이즈 벡터화(R1 완화) · presence 히스토리 API + 연속집계 · per-device MQTT 자격 ·
단절 스풀 · **SPI 전환** · Redis pub/sub · 캘리브레이션 실경로

### 리스크

**R1 클라우드 CWT 처리량 (최대 리스크).** 개발 PC 실측 p90 818ms → 1코어당 약 1.2 win/s인데
기기당 4 win/s가 필요하다. 게이팅이 재실률 30% 가정 시 3.3배를 벌어 주지만, 다기기는
`vertical_denoise`/`horizontal_denoise`의 224×224 파이썬 중첩 루프를 벡터화해야 한다.
골든 픽스처(npz 13개)가 있어 비트 동등성을 검증할 수 있다. **추정으로 최적화하지 말고
`cProfile`로 스테이지별 측정을 먼저 할 것.**

**R2 mode5 후처리 파리티.** 검증 실측상 임계값 0.468 단독은 오탐 13건, centered mode5를
얹으면 3건이다. 즉 0.468은 mode5를 전제로 고른 값이며, 실시간 인과 다수결이 이를 재현하지
못하면 **오탐이 4배**가 된다. `validation_predictions.csv` 375행으로 즉시 정량화할 수 있다.

**R3 SPI 전면 불일치.** magic·헤더·길이 필드 타입·최대 서브캐리어가 모두 어긋난다.
부분 수정이 아니라 양쪽 재작성이다.

**R4 실기 미검증.** 엣지는 재생 모드로만 검증했다. 라즈베리파이 실기, 실제 수신기,
ARM64 휠(엣지에서 numba·ssqueezepy를 뺐으므로 리스크는 크게 줄었다)은 미확인이다.

---

## 8. AWS 현황 — **인스턴스가 없다** (2026-09-10 확인)

2026-09-08에 EC2 2대(db·api)를 기동해 검증까지 마쳤고 2026-09-09까지 running이었으나,
**현재 ap-northeast-2에 인스턴스가 0대다.** EBS 볼륨도 없다(종료 시 함께 삭제).

| 자원 | 상태 |
|---|---|
| EC2 인스턴스 | **없음** |
| EBS 볼륨 | **없음** — DB 데이터도 함께 사라졌다 |
| 보안그룹 `wifiguard-dev-sg` | 남아 있음 (`sg-003bb85701153ccd2`) |
| 키페어 `wifiguard-dev` | 남아 있음 (ed25519) |
| SSM 파라미터 4종 | 남아 있음 (`pg_password`·`tsdb_password`·`jwt_secret`·`db_host`) |

**현재 상시 비용은 사실상 0이다.** 재구축하려면 `deploy/aws/README.md` §2부터 인스턴스를
새로 만들고 마이그레이션·시드를 다시 적용해야 한다. `db_host` 파라미터는 옛 사설 IP를
가리키므로 갱신이 필요하다.

개발은 로컬 컨테이너 4개(`wg-pg`·`wg-ts`·`wg-kafka`·`wg-mosq`)로 전 구간이 돌아가므로
클라우드 없이도 진행할 수 있다.

---

## 9. 기존 문서와의 관계

| 문서 | 상태 |
|---|---|
| `CSI-Guard_구현현황_및_완성목표_v1.0_20260729.md` | **아키텍처 서술은 폐기.** 단일 로컬 프로세스 시절 기준이다. **완성 목표 G1~G7과 우선순위는 유효** |
| `CSI-Guard_완성목표_실행계획_v1.0_20260729.md` | G1~G7 실행 계획. 유효하되 일정·전제는 재산정 필요 |
| `WIFI-GUARD_레포명세_*_v1.0_20260804.md` 4종 | 대체로 유효. **단 엣지 업링크(D1)·baud(D2)·SPI 전제는 이 문서가 우선** |
| `CSI-Guard_데이터모델_ERD_v1.0.docx` | 엔터티·무결성 규칙은 구현됨. **PipelineConfig 16필드는 5필드로 축소**, Resident 런타임 7필드는 DB 컬럼이 아님. 구현 정본은 Alembic `0001_initial.py` |
| `REVIEW_20260907.md` | 4-레포 검토 보고서. §7 수정 제안 대부분 반영됨 |

### G1~G7 진척

| 목표 | 진척 |
|---|---|
| G1 클라우드 영속 저장소 | **완료** — PostgreSQL 11테이블 + TimescaleDB hypertable |
| G2 MQTT 다기기 관리 | **완료** — 토픽 서버 발급, 브리지 신원 검증 |
| G3 FACILITY 실서비스 | **부분** — 테넌시·다기기 관제 동작. 에스컬레이션·ZONE/GATEWAY 미착수 |
| G4 낙상 모델 공간 적응 | 미착수 |
| G5 드리프트 감지 | 미착수 |
| G6 모델 배포·버전 관리 | 미착수 — 체크포인트 직접 로드 |
| G7 재실 확장·엣지 이관 | **엣지 이관 완료.** 커버리지 확장(ML 전환)은 미착수 |

---

## 10. 재현 방법

```bash
# 1) 로컬 미들웨어
docker start wg-pg wg-ts wg-kafka wg-mosq

# 2) 백엔드 (실시간 경로 포함) — 워커 1 고정
cd WIFIGUARD-BACKEND
export DATABASE_URL="postgresql+psycopg://wifiguard:devpass@127.0.0.1:5432/wifiguard"
export TSDB_DSN="postgresql://wifiguard:devpass@127.0.0.1:5433/wifiguard_ts"
export MQTT_HOST=127.0.0.1 MQTT_PORT=1883 MQTT_TLS=0
export KAFKA_BOOTSTRAP=127.0.0.1:9092
export JWT_SECRET="개발용-32바이트-이상-비밀값"
uv run uvicorn wifiguard_api.app:app --port 8000 --workers 1
curl -s localhost:8000/health | jq .ingest        # 브리지·컨슈머·싱크 실측 상태

# 3) 기기 등록 후 그 tenant/device 를 엣지 config/device.toml 에 넣는다
cd ../WIFIGUARD-RASPBERRY
python -m wifiguard_edge --transport replay        # Pi·수신기 없이 전 배선 구동

# 4) 프론트
cd ../WIFIGUARD-FRONTEND && bun run dev            # http://localhost:8080

# 5) 검사
cd ../WIFIGUARD-BACKEND  && uv run pytest -q       # 77
cd ../WIFIGUARD-RASPBERRY && python -m pytest -q   # 77
cd ../WIFIGUARD-FRONTEND && bun run typecheck && bun run lint && bun run test   # 34
```

상세는 각 레포 `README.md`, 남은 작업은 각 `PORTING.md`, 마일스톤은
`~/.claude/plans/rosy-wobbling-balloon.md`.
