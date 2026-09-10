# WIFI-GUARD ESP (펌웨어)

ESP32-C5 두 대로 CSI(Channel State Information)를 만들어 호스트에 흘려보내는 ESP-IDF 펌웨어.
**보드가 두 대여야 한다** — 한 대만으로는 CSI가 나오지 않는다.

```
[ESP32-C5 TX]  ──ESP-NOW 200pps, 5GHz ch48──►  [ESP32-C5 RX]  ──UART 2 Mbaud──►  [Raspberry Pi]
   csi_send                                       csi_recv                        WIFIGUARD-RASPBERRY
                                            csi_recv_calibrate
```

> ## ⚠ 이 레포는 아직 `git init` 전이다
>
> 원본 `csi_fall/esp32c5`(공개 GitHub)의 미커밋 변경 12건 + 미추적 8건을 정리해야 정본이
> 확정된다. 그리고 **`wifi_sensing_demo_router/sdkconfig`에 실제 Wi-Fi 자격증명이 공개
> 이력에 남아 있다** — 해당 비밀번호 회전이 선행되어야 한다. 자세한 내용은
> [PORTING.md](PORTING.md) §3, 근거는 `../REVIEW_20260907.md` P6·P7.

---

## 책임 경계

| 하는 것 | 하지 않는 것 |
|---|---|
| ESP-NOW 브로드캐스트 송신 (200pps) | **재실감지·낙상 판정** → Pi / 클라우드 |
| CSI 수집 · MAC 필터 · AGC/FFT 게인 보상 | 신호 전처리(서브캐리어 선택·PCA) → Pi |
| 바이너리 프레임 조립 + 체크섬 → UART | 네트워크 업링크 → Pi |
| AGC 게인 캘리브레이션 (`csi_recv_calibrate`) | 임계값 산출 → Pi (`calibration/onboarding.py`) |

---

## 구조

```
csi_send/                송신기. ESP-NOW 200pps, MAC 위장 1a:00:00:00:00:00
csi_recv/                수신기(기본). CSI → UART 바이너리 스트림
csi_recv_calibrate/      수신기 변형. "train" 수신 후 ~1초 AGC 창을 거쳐 게인 고정
wifi_sensing_demo_router/  ★ 무관한 Espressif 예제 (esp_wifi_sensing). 낙상 파이프라인과 별개
tools/
  fall_detect/           ★ 재실 신호체인의 원조 — WIFIGUARD-RASPBERRY presence/ 의 이식 원본
  stride/  lagacy/       수집·실험 스크립트 (데이터 CSV는 .gitignore)
```

`wifi_sensing_demo_router/`를 낙상 파이프라인으로 착각하지 말 것. 자체 managed component와
`HMS:` 라인 프로토콜을 쓰는 완전히 별개의 데모다.

## 빌드 · 플래시

각 펌웨어 디렉토리가 독립 ESP-IDF 프로젝트다.

```bash
cd csi_recv                       # 또는 csi_send / csi_recv_calibrate
idf.py set-target esp32c5
idf.py build
idf.py -p <PORT> flash monitor
```

밴드/채널은 Kconfig가 아니라 **`main/app_main.c` 상단의 `USE_5G_BAND`** 로 정한다
(1 = 5GHz ch48, 0 = 2.4GHz ch11). 바꾸려면 **송신기와 수신기를 둘 다 고쳐 다시 플래시**해야 한다.
`managed_components/`는 컴포넌트 매니저 산출물이니 손대지 말 것.

---

## UART 프레임 계약 — Pi의 `csi/protocol.py`와 일치해야 한다

`csi_recv/main/app_main.c:73-120` 기준. 이 값이 바뀌면 Pi 파서도 함께 바꿔야 한다.

| 항목 | 값 | 위치 |
|---|---|---|
| 물리 | UART0, **2,000,000** 8N1 | `sdkconfig.defaults:12` |
| 매직 | **`0xA55A`** (uint16 LE) | `app_main.c:73` |
| version / frame_type | 1 / 1 | `app_main.c:74-75` |
| 헤더 | **46 B** (`__attribute__((packed))`, 메타 24필드) | `app_main.c:78-110` |
| CSI 페이로드 | 최대 **612 B = 306 서브캐리어**, int8 (허수,실수) 인터리브 | `app_main.c:76` |
| `csi_len` | **uint16** | `app_main.c:107` |
| 체크섬 | 2 B, 앞 전체 바이트 합의 하위 16비트 | `app_main.c:112-120` |
| 전체 | `46 + csi_len + 2` → 최대 **660 B** | `app_main.c:190` |
| MAC 필터 | `1a:00:00:00:00:00` (송신기 위장 MAC) | `app_main.c:70`, 필터 `:303-306` |

페이로드는 AGC/FFT 게인 보상 후 int8로 클램프된다(`app_main.c:193-205`). Pi 쪽 진폭 변환은
`csi/protocol.py:99-101`이 `sqrt(imag² + real²)`로 받는다.

### ⚠ baud 921600 → 2,000,000 (2026-09-10)

**왜 올렸나** — 921600은 이 펌웨어의 출력을 감당하지 못했다.

| | 921600 | 2,000,000 |
|---|---:|---:|
| 660B 프레임 전송 시간 (8N1 = 10bit/byte) | 7.16 ms | **3.30 ms** |
| 프레임 상한 | 약 **140 fps** | 약 **303 fps** |
| 실측 수신율 약 167 Hz 대비 | **초과 (불가)** | 여유 1.8배 |

`csi_recv_calibrate/optimization_plan.md`의 실측이 같은 결론이다 — UART TX 바이트 루프가
**패킷당 7.16ms**로 부동소수 루프(0.3ms)보다 20배 크고, Wi-Fi 콜백을 그만큼 막고 있었다.
167Hz면 초당 1.2초가 필요해 이미 100%를 넘는다. 2 Mbaud에서 3.3ms로 내려간다.

**바꾼 파일** (`csi_recv`·`csi_recv_calibrate` 두 곳 모두):
`sdkconfig.defaults`의 `CONFIG_ESP_CONSOLE_UART_BAUDRATE`,
그리고 생성물 `sdkconfig`·`sdkconfig.old`의 같은 키 + `ESPTOOLPY_MONITOR_BAUD` ·
`MONITOR_BAUD` · `CONSOLE_UART_BAUDRATE`(구 별칭). 모니터 baud를 같이 올리지 않으면
`idf.py monitor`에 깨진 문자가 나온다.

**플래시 후 반드시 같이 바꿔야 하는 것** — 안 바꾸면 프레임이 하나도 파싱되지 않는다
(매직 `0xA55A` 미검출 → resync 무한 반복):

| 대상 | 상태 |
|---|---|
| `WIFIGUARD-RASPBERRY` `csi/serial_reader.py` `DEFAULT_BAUD` | **반영됨** |
| `WIFIGUARD-RASPBERRY` `config/device.toml.example` `baudrate` | **반영됨** |
| `tools/fall_detect/main.py:416` · `tools/stride/main.py:227` · `tools/lagacy/main.py:331` | **미반영** — 참조 전용 도구라 손대지 않았다. 쓰려면 각자 921600을 고칠 것 |
| 원본 로컬 데모 `Guardian Angel Alert/backend/csi/serial_reader.py` | **미반영** — 이 작업장 밖이다. 새 펌웨어를 플래시하면 그 데모는 baud를 맞추기 전까지 동작하지 않는다 |

`wifi_sensing_demo_router/`는 무관하므로 921600 그대로 두었다.

### `csi_recv_calibrate`의 "train"

호스트가 USB 시리얼 stdin으로 `train` 한 줄을 보내면(`app_main.c:85`), 약 1초간
AGC 게인 샘플을 모아 `esp_csi_gain_ctrl_set_rx_force_gain`으로 고정한 뒤 정상 스트리밍으로
전환한다(`CSI_TRAIN_DURATION_US = 1000000`, `app_main.c:86`). Pi의
`calibration/onboarding.py`가 `send_line("train")`으로 이 절차를 구동한다.

대시보드의 "Train" 탭(`tools/fall_detect/FEATURE_SPEC.md` F-TRN-001)과는 **무관하다** —
그쪽은 DNN 학습 자리표시자 스텁이다.

---

## ⚠ SPI는 구현되어 있지 않다

명세와 Pi의 `transport/protocol.py`는 ESP↔Pi 링크를 **SPI**로 규정하지만,
**이 레포의 펌웨어 3종 어디에도 SPI slave 코드가 없다**(`spi_slave`·`driver/spi` grep 0건).
그리고 두 정의는 모든 축에서 어긋난다.

| 축 | UART (여기, 실제 동작) | Pi `transport/protocol.py` (SPI) | 명세 목표 |
|---|---|---|---|
| 매직 | `0xA55A` | `0xABCD` | `0xA55A` |
| 헤더 | 46 B | 8 B | 46 B |
| `csi_len` | uint16 | **uint8** | uint16 |
| CSI 최대 | **612 B (306 서브캐리어)** | **128 B (64 서브캐리어)** | 612 B |
| 프레임 | 가변 최대 660 B | 140 B 고정 / 256·4096 B | 626 B / 8192 B |
| 체크섬 | 있음 | **없음** | 있음 |

즉 현재 SPI 정의로는 이 펌웨어가 실제로 만드는 CSI를 **담을 수 없다**(128 B < 612 B).
부분 수정이 아니라 양쪽 재작성이 필요하며, 그때까지 유효한 경로는 UART뿐이다.
실시간 파이프라인 1차 구현은 UART로 진행한다(계획서 D2·R5).

---

## 문서

- [PORTING.md](PORTING.md) — 원본 대비 정리 내역 · 남은 작업 · `git init` 선행 조건
- [CLAUDE.md](CLAUDE.md) — 하드웨어 데이터 흐름 · 빌드 · 도구 상세
- [../WIFIGUARD-RASPBERRY/docs/WIFI-GUARD_레포명세_esp_v1.0_20260804.md](../WIFIGUARD-RASPBERRY/docs/WIFI-GUARD_레포명세_esp_v1.0_20260804.md) — 이 레포의 명세
- `csi_recv_calibrate/optimization_plan.md` · `tools/fall_detect/{SPEC,FEATURE_SPEC,migration,occupation_pipline}.md`
