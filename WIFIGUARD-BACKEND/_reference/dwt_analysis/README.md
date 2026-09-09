# DWT 기반 낙상감지 분석 (Fall Detection Analysis using DWT)

## 프로젝트 개요 (Project Overview)

본 프로젝트는 **Mendeley 오픈 데이터셋(CSI Dataset)**을 활용하여 **DWT(Discrete Wavelet Transform)** 에너지 계수 기반 낙상감지 시스템을 구현하고 분석합니다. 
amfall 논문의 전처리 기법을 따르며, 다양한 환경(environment)과 피실험자(subject)에 따른 낙상 탐지 성능을 비교 분석합니다.

**Keywords:** Fall Detection, DWT (Discrete Wavelet Transform), CSI (Channel State Information), Signal Processing, Feature Extraction

---

## 연구 목표 (Research Goals)

본 프로젝트는 다음 세 가지 비교 실험을 통해 DWT 기반 낙상감지의 효과성을 검증합니다:

### 1. 환경별 낙상 특성 분석 (Env1 Baseline Analysis)
- **목표**: 단일 환경(env1)에서 낙상(fall)과 다른 일상 동작(ADL: Activities of Daily Living)의 DWT 특성 비교
- **분석 항목**:
  - DWT 스칼로그램(scalogram) 에너지 계수 시각화
  - 낙상과 정상 동작의 신호 패턴 차이점 파악
  - 에너지 분포, 주파수 특성 등 정량적 특성 비교

### 2. 환경 간 낙상 특성 비교 (Multi-Environment Analysis)
- **목표**: 세 가지 환경(env1, env2, env3)에서의 낙상 DWT 특성 비교
- **분석 항목**:
  - 환경 변화에 따른 낙상 신호의 안정성 검증
  - 환경별 에너지 계수 패턴 차이 분석
  - 환경-독립적 특징 추출 가능성 검토

### 3. 피실험자별 낙상 특성 비교 (Subject-Specific Analysis)
- **목표**: 서로 다른 피실험자(subject)들의 낙상 DWT 특성 비교
- **분석 항목**:
  - 개인별 신체 특성에 따른 낙상 신호 차이
  - 피실험자-독립적 모델 개발의 도전 과제 파악
  - 개인화된 낙상감지 시스템의 필요성 검증

---

## 디렉토리 구조 (Project Structure)

```
dwt_analysis/
├── README.md                    # 프로젝트 설명 문서
├── config/                      # 실험 설정 파일 (YAML)
│   ├── preprocess.yaml         # 전처리 파라미터
│   └── feature_extraction.yaml # 특징추출(DWT) 파라미터
├── data/                        # 데이터 디렉토리 (DVC 관리)
│   ├── raw/                     # 원본 CSI 데이터 및 train/test 데이터셋
│   ├── preprocessed/            # 전처리된 신호 데이터 (시각화용)
│   ├── features/                # 추출된 DWT 특징 데이터 (시각화용)
│   └── results/                 # 실험 결과 및 시각화
├── Mendeley/                    # 오픈소스 데이터 및 참고 자료
│   ├── manuscript.pdf           # 논문: 데이터 설명 및 참고
│   └── dataset_info.md          # 데이터셋 상세 정보
├── notebooks/                   # Jupyter 노트북 (파이프라인 단계별 분석)
│   ├── 01_window.ipynb                       # Raw 데이터 탐색: 기본 통계, 동작/환경별 진폭, 세그먼트 분포
│   ├── 02_preprocess.ipynb                   # 전처리 분석: 필터링, 스트림 선택, PC 분포, q값
│   └── 03_feature.ipynb                      # 특징 분석: DWT 에너지 분포 비교
├── scripts/                     # 실행 스크립트
│   ├── preprocess.py            # 전처리 실행 스크립트
│   ├── extract_features.py      # DWT 특징추출 스크립트
│   ├── train_model.py           # 모델 학습 스크립트
│   ├── inference.py             # 모델 추론 스크립트
│   └── run_experiments.sh       # 전체 파이프라인 실행 스크립트
├── src/                         # 핵심 모듈
│   └── dwt_coef/               # CSI 신호 처리 및 DWT 특징 추출 모듈
│       ├── __init__.py
│       ├── data_loader.py      # CSV 파일 로드 및 메타데이터 파싱
│       ├── preprocessing.py    # 신호 전처리 (resampling, filtering, stream selection, PCA)
│       └── dwt_features.py     # DWT 에너지 계수 추출
├── requirements.txt            # Python 의존성
├── .dvc/                       # DVC 설정 (데이터/결과 버전 관리)
└── .gitignore                  # Git 무시 설정

```

---

## 설치 및 환경 설정 (Installation & Setup)

### 1. Conda 환경 활성화

본 프로젝트는 `wifisense` 환경을 사용합니다.

```bash
# Conda 환경 확인
conda info --envs

# wifisense 환경 활성화
conda activate wifisense

# 환경 정보 확인
conda list
```

### 2. 프로젝트 의존성 설치

```bash
# 필수 패키지 설치 (필요시)
# pip install -r requirements.txt
```

**주요 패키지 (wifisense 환경에 포함):**
- `numpy` - 수치 계산
- `scipy` - 신호 처리 (wavelet, signal)
- `pandas` - 데이터 처리
- `matplotlib`, `seaborn` - 시각화
- `pytorch` / `tensorflow` - 딥러닝 프레임워크
- `pywt` - PyWavelets (DWT 구현)
- `jupyter` - Jupyter 노트북
- `dvc` - 데이터 버전 관리

### 3. 데이터셋 준비

```bash
# DVC 저장소에서 시각화용 데이터 다운로드
dvc pull

# 또는 특정 경로만 다운로드
dvc pull data/preprocessed
dvc pull data/features
```

**데이터 구조:**
- `data/raw/` - 원본 CSI 데이터 및 train/test 데이터셋 (DVC 관리)
- `data/preprocessed/` - 전처리된 신호 데이터 (시각화용, DVC 관리)
- `data/features/` - 추출된 DWT 특징 데이터 (시각화용, DVC 관리)
- `data/results/` - 실험 결과 및 모델 출력

---

## 사용 방법 (Usage Guide)

### 단계별 실행

#### 1단계: 데이터 전처리
```bash
# 전처리 실행 (config/preprocess.yaml 사용)
python scripts/preprocess.py --config config/preprocess.yaml

# 결과: data/preprocessed/ 에 전처리된 데이터 저장
```

**전처리 내용:**
- 원본 CSI 데이터 로드
- 노이즈 제거 및 필터링 (Butterworth filter)
- 신호 정규화 (z-score or min-max)
- amfall 논문의 전처리 절차 따름

#### 2단계: DWT 특징 추출
```bash
# DWT 에너지 계수 추출 (config/feature_extraction.yaml 사용)
python scripts/extract_features.py \
  --input data/preprocessed/ \
  --output data/features/ \
  --config config/feature_extraction.yaml

# 결과: DWT 계수, 에너지 특징, 스칼로그램 저장
```

**추출 특징:**
- DWT 에너지 계수 (다중 분해 레벨)
- 다중 해상도 분석 (MRA) 계수
- 통계적 특징 (mean, std, energy, entropy)
- 스칼로그램 이미지 (시각화용)

#### 3단계: 딥러닝 모델 학습
```bash
# 딥러닝 모델 학습 (amfall 기반 아키텍처)
python scripts/train_model.py \
  --train_data data/raw/train/ \
  --output models/ \
  --config config/experiment_config.yaml
```

**모델 구조:**
- amfall 논문의 딥러닝 모델 아키텍처 차용
- 구체적 구현은 추후 정의 예정
- 현재는 시각화/특징분석에 초점

#### 4단계: 추론 및 평가
```bash
# 모델 추론
python scripts/inference.py \
  --model models/best_model.pt \
  --test_data data/raw/test/ \
  --output results/predictions.csv
```

### Jupyter 노트북을 이용한 분석

세 단계의 파이프라인 결과를 탐색하는 노트북:

```bash
# Jupyter 시작
jupyter notebook

# 단계별 노트북 실행 (순서 필수)
# 1. notebooks/01_window.ipynb          - 원본 데이터: 기본 형태, 동작별/환경별 진폭 통계, 세그먼트 분포
# 2. notebooks/02_preprocess.ipynb      - 전처리 결과: 필터링, 스트림 선택, PC 분포, q값 분석
# 3. notebooks/03_feature.ipynb         - 특징 추출: DWT 에너지 분포, 동작/환경/사람별 비교
```

### 전체 파이프라인 실행

```bash
# 모든 단계를 순차적으로 실행 (전처리→특징추출→모델학습→추론)
bash scripts/run_experiments.sh
```

---

## 실험 설정 (Experiment Configuration)

### `config/preprocess.yaml` 예시

전처리 파라미터 설정:

```yaml
# 데이터 설정
data:
  raw_path: "data/raw"
  output_path: "data/preprocessed"
  sample_rate: 1000  # Hz
  
# 전처리 설정
preprocessing:
  window_size: 2048
  overlap: 0.5
  filter_type: "butterworth"
  filter_order: 4
  cutoff_freq: [1, 200]  # Hz
  
# 신호 정규화
normalization:
  method: "zscore"  # or "minmax"
  axis: 0
```

### `config/feature_extraction.yaml` 예시

DWT 특징추출 파라미터 설정:

```yaml
# DWT 설정
dwt:
  wavelet: "db4"  # Daubechies-4
  decomposition_level: 5
  energy_bands:
    - "cA"      # Approximation coefficients
    - "cD1"     # Detail coefficients level 1
    - "cD2"
    - "cD3"
    - "cD4"
    - "cD5"
    
# 특징추출 설정
feature_extraction:
  statistical_features:
    - "mean"
    - "std"
    - "energy"
    - "entropy"
  window_duration: 2  # seconds
  
# 실험 설정
experiments:
  - name: "exp1_env1_fall_vs_adl"
    environment: 1
    activities: ["fall", "sit", "walk", "stand", "bend"]
    
  - name: "exp2_multi_env"
    environments: [1, 2, 3]
    activities: ["fall"]
    
  - name: "exp3_subject_specific"
    subjects: [1, 2, 3, 4, 5]
    activities: ["fall"]
```

---

## 주요 모듈 설명 (Module Documentation)

### `src/dwt_coef/data_loader.py`

```python
from src.dwt_coef.data_loader import load_csi_file, get_file_index

# 단일 파일 로드
sample = load_csi_file("Mendeley/Environment 1/Subject 1/E1_S01_C01_A01_T01.csv")
# 반환: {
#   'csi': (n_packets, 3, 30) complex128,
#   'timestamps': (n_packets,) uint64,
#   'env': int, 'subject': int, 'class_id': int, 'activity': int, 'trial': int, 'is_fall': bool
# }

# 모든 파일 인덱스 생성
df_index = get_file_index("Mendeley")
# columns: filepath, env, subject, class_id, activity, trial, is_fall
```

### `src/dwt_coef/preprocessing.py`

```python
from src.dwt_coef.preprocessing import (
    sliding_window_raw,
    extract_amplitude,
    resample_signal,
    bandpass_filter,
    select_streams,
    select_pcs,
    preprocess_segment
)

# 세그먼트 분할 (3초, 0.25초 stride)
segments = sliding_window_raw(csi, timestamps, window_sec=2.5, stride_sec=0.25)

# 단일 세그먼트 전처리
rep_signal, stats = preprocess_segment(
    csi_seg, ts_seg,
    fs_hz=320.0,
    low_hz=0.5, high_hz=150.0,
    n_per_antenna=10,  # 30개 서브캐리어 중 10개 선택
)
# rep_signal: (window_size,) 1D representative signal
```

### `src/dwt_coef/dwt_features.py`

```python
from src.dwt_coef.dwt_features import compute_energy_entropy

# DWT 에너지 및 엔트로피 추출
result = compute_energy_entropy(signal, wavelet='db4', level=5)
# 반환: {
#   'energy': {'E_cA': ..., 'E_cD1': ..., ..., 'E_cD5': ...},
#   'entropy': float
# }
```

---

## 파이프라인 출력 (Pipeline Output)

### 1단계: Windowing (`notebooks/01_window.ipynb`)
- 원본 CSI 데이터의 기본 정보 및 통계
- 동작별 진폭 분포 (평균, 표준편차)
- 환경별 진폭 분포
- 동작별 세그먼트 개수 분석
- 동작별/환경별 리샘플링 샘플 수

### 2단계: Preprocessing (`notebooks/02_preprocess.ipynb`)
- 버터워스 필터 적용 전/후 신호 비교
- 동작별/환경별 q값(신호 품질 지표) 분포
- 선택된 스트림 인덱스 분석
- PC(주성분) 선택 분포
- 낙상 샘플의 세그먼트별 시계열 q값 분석

### 3단계: Feature Extraction (`notebooks/03_feature.ipynb`)
- DWT 에너지 계수 분포 (7개 밴드: E_cA, E_cD1~5, entropy)
- 동작별 DWT 에너지 비교
- 동작×환경 조합별 에너지 분포
- 사람별 DWT 에너지 특성

---

## 데이터 버전 관리 (Data Version Control with DVC)

본 프로젝트는 **DVC(Data Version Control)**를 사용하여 데이터와 실험 결과를 버전 관리합니다.

### DVC 구조

```
data/
├── raw/          # 원본 데이터 + train/test 데이터셋 (DVC 추적)
├── preprocessed/ # 시각화용 전처리 데이터 (DVC 추적)
├── features/     # 시각화용 특징 데이터 (DVC 추적)
└── results/      # 실험 결과
```

### DVC 명령어

```bash
# 시각화용 데이터 다운로드
dvc pull data/preprocessed
dvc pull data/features

# 모든 DVC 추적 데이터 다운로드
dvc pull

# 데이터 변경 사항 추적
dvc add data/preprocessed
dvc add data/features

# 리모트 저장소에 푸시
dvc push
```

---

## 참고 자료 (References)

### 핵심 논문
- **amfall**: 본 프로젝트의 전처리 및 딥러닝 모델 기반 논문
  - 전처리 알고리즘 상세 설명
  - 신호 처리 파라미터 기준값 제공
  - 딥러닝 아키텍처 기본 구조 차용 (구체화 예정)

### 데이터셋
- **Mendeley CSI Dataset** 
  - 오픈 접근 가능한 Channel State Information (CSI) 데이터
  - 다양한 환경(3가지)과 피실험자(5명 이상)에서 수집
  - 낙상 및 일상동작 포함
  - 데이터 상세 정보: `Mendeley/dataset_info.md`

### 관련 기술
- **Discrete Wavelet Transform (DWT)**
  - PyWavelets 문서: https://pywt.readthedocs.io/
  - 신호 분석 및 특징추출의 기본 기법
  
- **Scalogram Analysis**
  - 연속 웨이블릿 변환(CWT)의 시각화
  - 시간-주파수 특성 분석에 효과적

- **CSI (Channel State Information)**
  - 무선 신호 처리를 통한 행동 인식
  - 낙상 감지의 비침해적(non-intrusive) 해결책

### 추가 학습 자료
- Signal Processing: Oppenheim & Schafer (신호처리 기초)
- Time-Frequency Analysis: Cohen (웨이블릿 심화)
- Feature Engineering for Machine Learning

---

## 문제 해결 (Troubleshooting)

### 1. DVC 데이터 다운로드 실패
```bash
# DVC 원격 저장소 확인
dvc remote list

# 저장소 설정 (필요시)
dvc remote add -d myremote /path/to/remote
```

### 2. 메모리 부족 (데이터 처리 시)
```python
# config에서 window_size 줄이기
# 또는 배치 처리로 변경
batch_size = 100
```

### 3. Wavelet 선택 오류
```python
# 지원하는 wavelet 확인
import pywt
print(pywt.families())  # 사용 가능한 wavelet 족 확인
```

### 4. 시각화 폰트 오류 (한글)
```python
import matplotlib.pyplot as plt
plt.rcParams['font.family'] = 'DejaVu Sans'  # 또는 시스템 폰트 설정
```

---

## 개발 로드맵 (Development Roadmap)

현재 진행 중인 작업:

- [x] **Phase 1**: Windowing + 전처리 파이프라인 구현 (완료)
  - Sliding window 세그먼트 분할 (2.5초, 0.25초 stride)
  - Resampling, Bandpass filtering, Stream selection, PCA
  - Per-segment 처리 및 representative signal 생성
  
- [ ] **Phase 2**: 데이터 분석 및 시각화 (진행중)
  - 3개 노트북 (window, preprocess, feature)으로 단계별 분석
  - 동작/환경/사람별 특성 비교
  
- [ ] **Phase 3**: 딥러닝 모델 및 분류 (예정)
  - 분류 모델 학습 (낙상 vs 비낙상)
  - 성능 평가 및 최적화

---

## 라이선스 (License)

본 프로젝트는 CSI 오픈 데이터셋(Mendeley)의 라이선스 정책을 따릅니다.

**데이터셋 라이선스:** 
- Mendeley CSI Dataset: CC-BY 또는 해당 공개 라이선스 확인 필요

**코드 라이선스:**
- 추후 명시 예정

---

## 저자 및 연락처 (Authors & Contact)

**프로젝트 담당:** jspark9703@gmail.com

**마지막 업데이트:** 2026-06-28

---

## 체크리스트 (Setup Checklist)

프로젝트 시작 전 다음을 확인하세요:

- [ ] Conda wifisense 환경 활성화 완료
- [ ] `conda activate wifisense` 확인
- [ ] `data/raw/` 에 원본 CSI 데이터 배치
- [ ] DVC 설정 및 `dvc pull` 완료 (data/preprocessed, data/features)
- [ ] `config/preprocess.yaml` 설정 확인
- [ ] `config/feature_extraction.yaml` 설정 확인
- [ ] Jupyter 설치 확인
- [ ] 첫 번째 노트북 실행 테스트 완료
- [ ] 전처리 스크립트 실행 테스트

---

