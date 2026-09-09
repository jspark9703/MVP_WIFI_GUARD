# WIFI-GUARD Edge (라즈베리파이)

CSI를 SPI로 받아 **재실을 판정하고**, 활동 구간의 **피처를 추출해 클라우드로 올린다.**

```
[ESP32-C5 RX] ──SPI 8192B/50ms──► [Raspberry Pi]
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
              PresenceLoop        FeatureLoop        Calibration
              (상시, 0.25s)      (게이팅 시, 0.25s)   (61초 4단계)
                    │                  │                  │
                    └──────────────────┴──────────────────┘
                                       ▼
                          MQTT over TLS 8883 (아웃바운드 전용)
                                       ▼
                      [AWS IoT Core] ──IoT Rule──► [Kafka]
```

> ## ⚠ 현재 상태: 이식 배치만 완료. 실행되지 않는다.
>
> 재실감지(`presence/`)·피처추출(`features/`)·링버퍼(`csi/`)는 **이식이 끝났고 import 된다.**
> 엔트리포인트(`__main__.py`), 설정 로더(`config.py`), 게이팅, MQTT, 스풀은 **아직 없다.**
> 무엇이 남았는지는 [PORTING.md](PORTING.md) §2를 볼 것.

---

## 책임 경계

| 하는 것 | 하지 않는 것 |
|---|---|
| CSI 수신 · 링버퍼 적재 | **낙상 판정** → 클라우드 (피처까지만 만든다) |
| **재실/움직임 감지 (이 레포가 유일한 권위)** | 신호 수집 하드웨어 제어 → ESP32-C5 펌웨어 |
| 게이팅 — 활동 구간에만 업로드 | 영속 저장 → 클라우드 DB (여기 SQLite는 단절 버퍼 전용) |
| S3 스칼로그램 + PCA-ACF 피처 추출 | 사용자 인증·대시보드 → 백엔드/프론트엔드 |
| MQTT/TLS 퍼블리시, 4단계 캘리브레이션 | |

**네트워크가 끊기면**: 재실감지는 계속 동작한다. 낙상 감지는 **중단된다.** 안전 기능이므로 무증상으로 넘기면 안 되고, `telemetry`로 명시 보고해야 한다.

---

## 구조

```
src/wifiguard_edge/
  presence/        재실감지 신호체인 + 상태머신   [이식 완료 · 무변경]
  features/        S3 스칼로그램 + PCA-ACF        [이식 완료 · 무변경 · 계약 고정]
  csi/             링버퍼 · UART 프레임 파서       [이식 완료 · 무변경]
  presence_loop.py PresenceLoop 스레드 (0.25s)     [이식 완료 · import만 수정]
  calibration/     4단계 61초 캘리브레이션         [이식 완료 · import만 수정]
  transport/       SPI 마스터 + 개발 PC 목        [이식 완료 · 계약 미구현]
  mqtt/  spool/    클라우드 업링크 · 단절 버퍼      [비어 있음]
config/            default.toml · device.toml.example
deploy/            systemd unit · install.sh      [뼈대 · 실기 미검증]
tools/             bench_pipeline.py              [이식 완료 · 추론부 제거 필요]
tests/             test_protocol.py
docs/              명세 문서
_reference/        승계하지 않는 참조 코드
```

## 설치

```bash
# 개발 PC
python -m venv .venv && .venv/bin/pip install -r requirements.txt

# 라즈베리파이
sudo bash deploy/install.sh          # SPI 활성화 · gpio 그룹 · tmpfs 로그 · systemd
```

## 지금 돌려볼 수 있는 것

```bash
# 이식된 패키지가 import 되는지
PYTHONPATH=src python -c "from wifiguard_edge.presence import PresenceConfig, PresenceDetector; print('OK')"
PYTHONPATH=src python -c "from wifiguard_edge.features import FeatureConfig, extract_window_features; print('OK')"

# 피처 추출 지연 벤치 — ★ 먼저 tools/bench_pipeline.py 의 inference import 를 걷어내야 한다
PYTHONPATH=src python tools/bench_pipeline.py --fs 166.67
```

`python -m wifiguard_edge`는 `__main__.py`가 없어 아직 동작하지 않는다.

---

## 세 축의 임계값 — 절대 섞지 말 것

이름이 비슷하지만 **척도가 전혀 다르다.**

| 값 | 출처 | 척도 | UI 라벨 |
|---|---|---|---|
| `presence_mv_threshold` | 캘리브레이션 (여기) | MV 스케일, 기본 2.0 | **"움직임 임계값"** |
| `wander_baseline` | 캘리브레이션 (여기) | Welch PSD 스케일, 기본 0.5 | **"재실 baseline"** |
| `wander_ratio_threshold` | 설정 (여기) | **baseline 대비 배수** 1~5, 기본 1.8 | "WANDER 비율 임계값" |
| `threshold` = **0.468** | 모델 (**클라우드**) | **확률** 0~1 | **"판정 임계값"** |

`wander`라는 이름도 세 군데서 다른 뜻으로 쓰인다 — 여기의 `wander`(0.1–0.5Hz Welch band energy의 baseline 대비 **비율**), ESP `occupancy_fsm.c`의 `wander`(진폭 평균의 **절대 편차**, 폴백 전용·무관), 프론트 목업의 `wander_threshold`(절대값). 명세 §5.3 참조.

## 핵심 파라미터

| 재실 (MV) | 값 | | Wander | 값 |
|---|---:|---|---|---:|
| 관측 윈도우 | 3.0초 | | 관측 윈도우 | 10.0초 |
| 이동분산 창 | 0.5초 → ω=24 | | 사전필터 대역 | 0.05–5Hz |
| 리샘플 | 100Hz | | 측정 대역 | 0.1–0.5Hz |
| 밴드패스 | 0.5–50Hz, 4차 | | 디바운스 | 2.0초 |
| 선택 서브캐리어 | 10개 | | 이동분산 창 | 1.0초 → ω=50 |
| stride | 0.25초 | | 타임아웃 | 10초 |

> ω는 파생값이다: `max(1, round((mv_window_sec * fs_hz - 1) / 2))`. 파이썬 `round()`는 banker's rounding이라 **24.5 → 24, 49.5 → 50**이다. 언어를 바꾸거나 `math.floor`/`np.round`로 갈아끼우면 값이 달라진다.

**상태머신은 비대칭이다**: MV는 ABSENT→PRESENT 전이를 일으킬 수 있지만, wander는 **이미 PRESENT일 때만** 유지에 기여한다. 입실에는 반드시 몸통 동작이 따르며, 이 비대칭이 문 열림·바람 같은 순간 노이즈의 오탐을 막는다.

## 문서

- [PORTING.md](PORTING.md) — 이식 매핑 · 남은 작업 · 검증 상태
- [docs/WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md) — 이 레포의 명세
- [docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md) §7 — **4레포 공통 계약 SSOT** (MQTT 토픽·페이로드)
- [docs/CSI-Guard_재실감지_알고리즘_상태머신_보고서_v2.0_20260720.md](docs/CSI-Guard_재실감지_알고리즘_상태머신_보고서_v2.0_20260720.md) — 재실 알고리즘 원본
- [deploy/SETUP_BY_PLATFORM.md](deploy/SETUP_BY_PLATFORM.md) — 시제품 플랫폼별 설정 (install.sh 근거)
