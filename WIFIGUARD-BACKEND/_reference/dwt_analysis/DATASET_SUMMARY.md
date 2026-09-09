# Wi-Fi CSI 기반 인간 활동 인식 데이터셋

**출처:** Alsaify et al., "A dataset for Wi-Fi-based human activity recognition in line-of-sight and non-line-of-sight indoor environments", Data in Brief 33 (2020)

---

## 1. 개요

### 목적
Wi-Fi 신호(CSI: Channel State Information)를 이용한 **낙상 감지 및 인간 활동 인식** 데이터셋

### 데이터 수집
- **피험자:** 30명 (남성 28명, 여성 2명)
- **환경:** 3개 (LOS 2개, NLOS 1개)
- **활동:** 5개 실험 유형 (12개 세부 활동)
- **샘플링 속도:** 320 packets/second

---

## 2. 활동(Activity) 정의

### C1: 앉은 상태에서 낙상 (Falling from sitting position)
| ID | 활동 | 설명 |
|---|---|---|
| A01 | Sit still | 의자에 앉아있기 |
| **A02** | **Falling down** | **낙상** |
| A03 | Lie down | 누워있기 |

### C2: 서 있는 상태에서 낙상 (Falling from standing position)
| ID | 활동 | 설명 |
|---|---|---|
| A04 | Stand still | 서 있기 |
| **A05** | **Falling down** | **낙상** |
| A03 | Lie down | 누워있기 |

### C3: 보행 (Walking)
| ID | 활동 | 설명 |
|---|---|---|
| A06 | Walking (TX→RX) | 송신자 → 수신자 보행 |
| A07 | Turning | 회전 |
| A08 | Walking (RX→TX) | 수신자 → 송신자 보행 |
| A09 | Turning | 회전 |

### C4: 앉기/일어서기 (Sit down and stand up)
| ID | 활동 | 설명 |
|---|---|---|
| A01 | Sit still | 의자에 앉아있기 |
| A10 | Standing up | 일어서기 |
| A04 | Stand still | 서 있기 |
| A11 | Sitting down | 앉기 |

### C5: 땅에서 펜 집기 (Pick a pen from ground)
| ID | 활동 | 설명 |
|---|---|---|
| A12 | Pick up pen | 땅에서 펜 집기 |

**낙상 관련 활동:** A02 (앉은 상태), A05 (서 있는 상태)

---

## 3. 데이터셋 구조

### 파일명 규칙
```
E{env}_S{subject:02d}_C{class}_A{activity:02d}_T{trial:02d}.csv
```

예: `E1_S01_C1_A02_T01.csv`
- E1: Environment 1
- S01: Subject 1
- C1: Class 1 (Falling from sitting)
- A02: Activity 2 (Falling down)
- T01: Trial 1

### CSI 파일 구조
각 CSV 파일에 포함된 필드:

| 필드 | 설명 |
|---|---|
| `timestamp_low` | 패킷 도착 시간 (1MHz NIC clock) |
| `bfee_count` | 빔포밍 측정 횟수 |
| `Nrx` | 수신 안테나 수 (3개) |
| `Ntx` | 송신 안테나 수 (1개) |
| `RSSI_a, b, c` | 각 수신 안테나별 RSSI (dB) |
| `csi_1_{rx}_{subcarrier}` | CSI 값 (90개: 3 RX × 30 subcarriers) |

---

## 4. 실험 환경

### 환경 1: 연구실 (LOS - Line of Sight)
- **위치:** 대학 연구실
- **크기:** 4.7m × 4.7m
- **거리:** TX-RX 간 3.7m
- **피험자:** S1~S10 (10명)
- **특징:** 직접 신호 경로

### 환경 2: 복도 (LOS)
- **위치:** 대학 복도
- **크기:** 7.95m × 3.6m
- **거리:** TX-RX 간 약 6.5m
- **피험자:** S11~S20 (10명)
- **특징:** 직접 신호 경로

### 환경 3: 사무실 (NLOS - Non-Line of Sight)
- **위치:** 일반 사무실
- **크기:** 약 5m × 5m
- **거리:** TX-RX 간 약 4.5m
- **피험자:** S21~S30 (10명)
- **특징:** 간접 신호 경로 (벽/가구 장애물)

---

## 5. 데이터 수집 하드웨어/소프트웨어

### 하드웨어
- **NIC:** Intel 5300 WiFi Card (TX/RX용 각각 1개)
- **대역:** 2.4 GHz
- **채널:** 3번
- **대역폭:** 20 MHz

### 소프트웨어
- **CSI 추출 도구:** CSI Tool (Halperin et al.)
- **채널:** 20 MHz
- **샘플링 속도:** 320 packets/second

---

## 6. 데이터셋 규모

### 파일 수
- **총 파일:** 약 4,500개 (각 환경 1,500개)
- **구성:**
  - 피험자 30명 × 환경별 수집
  - 각 활동별 여러 시도(Trial)

### 피험자 정보
- **총 30명:** 28 남성, 2 여성
- **연령:** 20~35세
- **체중:** 57~130 kg
- **신장:** 153~192 cm
- **분포:** 각 환경마다 10명

---

## 7. 실험 절차

### 타이밍
- 각 실험은 **음성 신호(beep)**로 시작/종료 표시
- 실험별 지속시간: 약 3~5초

### 참가자 지침
- 활동 수행 위치: TX-RX 중간 지점
- 모든 활동은 사전 교육 후 수행
- 각 활동마다 여러 시도(Trial) 반복

---

## 8. 윤리 및 승인

- **따른 기준:** Declaration of Helsinki
- **승인:** Jordan University of Science and Technology의 IRB (Institutional Review Board)
- **펀딩:** Research-Grant program (Grant No. 20180032)

---

## 9. 주요 특징

### 장점
✓ **다중 환경:** LOS/NLOS 모두 포함 (현실적 상황)  
✓ **큰 피험자 수:** 30명 (다양성)  
✓ **높은 샘플링 속도:** 320 packets/sec (정교한 분석 가능)  
✓ **낙상 감지 초점:** A02, A05 명확히 구분  
✓ **공개 데이터셋:** 연구 커뮤니티용 제공

### 주의사항
- 모두 남성 중심 (2명만 여성)
- 연령대 제한적 (대부분 20대)
- 실내 환경에만 해당
- 피험자 간 신체 특성 편차 큼 (체중: 57~130 kg)

---

## 10. 활용 분야

1. **낙상 감지:** A02, A05 vs 다른 활동 분류
2. **인간 활동 인식:** 5가지 실험 분류
3. **환경 영향 분석:** LOS vs NLOS 비교
4. **신호 처리 연구:** CSI 신호 분석
5. **머신러닝:** WiFi 기반 HAR 모델 개발

---

## 참고문헌

[1] Halperin, D., Hu, W., Sheth, A., & Wetherall, D. "Tool release: Gathering 802.11n traces with channel state information." ACM SIGCOMM (2011)

---

**작성일:** 2026-06-29  
**데이터셋 원문 인용:**  
Alsaify, B.A., Almazari, M.M., Alazrai, R., & Daoud, M.I. (2020). "A dataset for Wi-Fi-based human activity recognition in line-of-sight and non-line-of-sight indoor environments." Data in Brief, 33, 106534.
