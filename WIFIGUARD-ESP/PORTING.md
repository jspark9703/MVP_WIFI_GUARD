# PORTING — WIFIGUARD-ESP

이 폴더는 `csi_fall/esp32c5`(공개 GitHub 레포)의 **사본**이다. 다른 세 레포와 달리
"이식"이 아니라 통째 복사이며, 원본이 아직 정본이다.

- 원본: `csi_fall/esp32c5` — `https://github.com/jspark9703/esp32c5.git`
- 배치 작업일: 2026-09-03 · 정리: 2026-09-08 · 문서 작성: 2026-09-10
- 명세: [../WIFIGUARD-RASPBERRY/docs/WIFI-GUARD_레포명세_esp_v1.0_20260804.md](../WIFIGUARD-RASPBERRY/docs/WIFI-GUARD_레포명세_esp_v1.0_20260804.md)

---

## 1. 원본 대비 달라진 것

**코드는 한 줄도 바꾸지 않았다.** 용량 정리와 `.gitignore` 정비만 했다 (2026-09-08).

| 삭제한 것 | 크기 | 비고 |
|---|---:|---|
| `*/build/` 4개 | 865 MB | ESP-IDF 빌드 산출물 |
| `tools/lagacy/env/` | 564 MB | 벤더링된 파이썬 venv |
| `tools/stride/data/` (CSV 14개) | 176 MB | 원본 `esp32c5/tools/stride/data`와 경로·크기 동일 확인 후 삭제 |
| `__pycache__` · `.pytest_cache` 8개 | — | |
| **합계** | **1.6 GB → 23 MB** | |

`.gitignore`에 `build/`, `managed_components/`, `.cache/`, `sdkconfig`, `sdkconfig.old`,
`__pycache__/`, `.pytest_cache/`, `tools/lagacy/env`, `tools/stride/data`를 추가했다.

`sdkconfig`를 무시 대상으로 넣은 것은 ESP-IDF 관례이자 **보안 조치**다 — §3 참조.
`sdkconfig.defaults`만 추적한다.

### `tools/fall_detect` numpy 2 호환 (2026-09-08)

| 파일 | 변경 |
|---|---|
| `src/streaming_features.py:170` | `np.trapz` → `getattr(np, "trapezoid", None) or np.trapz` (numpy 1.26 핀과 2.x 양쪽 동작) |
| `tests/test_pipeline.py:255` | `raw_ts.tolist()`로 Python int 언랩 (numpy 2 정수 캐스팅 OverflowError 회피) |

결과: numpy 2.5.3에서 **26 passed / 2 failed** (이전 23/5).
남은 실패 2건(`test_fall_state_machine`, `test_synthetic_burst`)은 **원본에서도 실패하던 로직 문제**다
(원본 `.pytest_cache/v/cache/lastfailed`에 동일하게 기록돼 있었다). §3의 원본 정리 후 재검토한다.

---

## 2. 이 레포가 다른 레포에 준 것

| 여기 | 이식처 | 비고 |
|---|---|---|
| `tools/fall_detect/src/presence_state_machine.py` | `WIFIGUARD-RASPBERRY/.../presence/state_machine.py` | 재실 상태머신 원조. 외부 의존 없는 순수 로직이라 무수정 복사 |
| `tools/fall_detect/src/streaming_features.py` | `.../presence/streaming_features.py` | MV·wander 신호체인 |
| `tools/fall_detect/src/preprocessing.py` | `.../presence/preprocessing.py` | 리샘플·밴드패스 |
| `tools/fall_detect/src/onboarding.py` | `.../calibration/onboarding.py` | 4단계 61초 캘리브레이션 |
| `csi_recv/main/app_main.c` 프레임 정의 | `.../csi/protocol.py` | 46B 헤더 · 매직 `0xA55A` · 체크섬. **양쪽이 일치함을 확인했다** |

포팅 근거는 `tools/fall_detect/migration.md`와 `occupation_pipline.md`에 있다.

> `tools/fall_detect/`는 **로컬 전용 참조**로 남긴다. 승계 대상이 아니며, 재실감지의 권위는
> 이제 `WIFIGUARD-RASPBERRY`다. 양쪽이 갈라지면 Pi 쪽이 정본이다.

---

## 3. `git init` 선행 조건 — 사용자 조치 대기

원본 `csi_fall/esp32c5`가 정리되기 전에는 이 사본에 `git init`을 하지 않는다.
사본과 원본 중 어느 쪽이 정본인지 불명확해지기 때문이다.

### 3-1. Wi-Fi 자격증명 (REVIEW P6) — **보안**

`wifi_sensing_demo_router/sdkconfig`와 `sdkconfig.old`에 **실제 Wi-Fi SSID/비밀번호가
공개 레포 이력에 추적되어 있다.** ESP-IDF 기본값이 아니라 실제 값이다.

| 항목 | 상태 |
|---|---|
| ② 추적 해제 + `.gitignore` 등록 | **완료(2026-09-08)** — `sdkconfig` 4개 + `sdkconfig.old` 4개를 `git rm --cached`. **staged 상태이며 커밋·푸시하지 않았다** |
| ① 해당 Wi-Fi **비밀번호 회전** (라우터 측) | **사용자 조치 대기** |
| ③ 공개 이력 정리 (`git filter-repo`) 여부 | **사용자 판단** — ①이 되면 선택 사항 |

추적을 해제해도 **과거 커밋에는 그대로 남아 있다.** 비밀번호 회전이 유일한 실질적 조치다.

### 3-2. 원본 미커밋 변경 (REVIEW P7)

2026-09-10 기준 원본 `git status`:

```
 M  .gitignore  CLAUDE.md
 M  csi_recv_calibrate/main/app_main.c   csi_send/main/app_main.c
 M  tools/fall_detect/{API.md, README.md, SPEC.md, main.py,
                       src/pipeline.py, src/streaming_features.py,
                       static/index.html, tests/test_pipeline.py}
 D  (staged) sdkconfig ×4, sdkconfig.old ×4          ← 3-1 ②
 ?? csi_recv_calibrate/{AGENTS.md, optimization_plan.md}
 ?? tools/fall_detect/{migration.md, occupation_pipline.md,
                       src/onboarding.py, src/presence_state_machine.py,
                       tests/test_onboarding.py, tests/test_presence_state_machine.py}
```

미추적 6개는 **재실감지 이식의 원본**이라 유실되면 곤란하다.

### 3-3. 순서

1. 비밀번호 회전 (3-1 ①)
2. 원본에서 미커밋 변경 커밋 또는 폐기 결정 (3-2)
3. 이 사본을 원본과 재동기화
4. `git init` → 독립 레포

---

## 4. 남은 작업

| # | 내용 | 우선순위 |
|---|---|---|
| E1 | **SPI slave 펌웨어 신규 작성** — `spi_slave` 드라이버, 8192B 프레임, CSI 612B, `csi_len` uint16. Pi `transport/protocol.py`도 같이 재작성 | 후속 (계획서 D2·R5) |
| E2 | `tools/fall_detect` 로직 실패 2건 재검토 | §3 이후 |
| E3 | `csi_recv`/`csi_recv_calibrate` 통합 여부 결정 — `app_main.c` 외에는 동일하다 | 낮음 |
| E4 | 밴드/채널을 `USE_5G_BAND` 매크로에서 Kconfig로 이관 (지금은 재빌드가 필요하다) | 낮음 |

**E1이 왜 후속인가**: 실시간 파이프라인(MQTT→Kafka→추론→백엔드→프론트)은 UART로도 전 구간
검증이 가능하고, SPI는 보드 2대 실기가 있어야 검증된다. 링크 계층 교체는 Pi 쪽
`transport/base.py` 추상화 뒤에서 이루어지므로 상위 파이프라인에 영향이 없다.

---

## 5. 알아둘 것

- **송신기와 수신기의 밴드/채널이 같아야 한다.** `USE_5G_BAND`는 소스 매크로라 바꾸면 양쪽 재빌드다.
- **`wifi_sensing_demo_router/`는 무관한 예제다.** 자체 managed component와 `HMS:` 라인 프로토콜을
  쓰며 낙상 파이프라인과 아무 관계가 없다. 여기의 `wander`도 Pi의 `wander`와 다른 뜻이다
  (진폭 평균의 절대 편차 vs Welch 밴드 에너지의 baseline 대비 비율).
- **`managed_components/`는 `.gitignore` 대상**이다. `idf.py build`가 `main/idf_component.yml`에서
  다시 받아온다. 손으로 고치지 말 것.
- `csi_recv`와 `csi_recv_calibrate`는 `main/app_main.c` 외에 CMake·컴포넌트 의존이 동일하다.
