# CSI 낙상 탐지 파이프라인

> WIFI-GUARD 통합 위치: 이 디렉터리는 PyTorch 의존성을 루트 API 환경과 분리한 독립
> 패키지다. 여기서 만든 신뢰 가능한 `model/model.pt`가 있어야 실시간 모델 서비스에
> 연결할 수 있다. 현재 저장소에는 실제 학습 데이터와 가중치가 포함되어 있지 않으며,
> 합성 데모의 결과를 실제 낙상 정확도로 해석하면 안 된다.

원본 Wi-Fi CSI에서 **진폭 복원 → 시간 보정 → 3초 윈도우·라벨 생성 → ACF/CWT 피처 추출 → 인코더 학습 → 새 녹화 예측**까지 실행하는 Python 패키지입니다. 이전 실험 폴더, 사전 계산 피처, 로컬 절대 경로가 필요하지 않습니다.

Mendeley 사전학습과 직접 수집 데이터 미세조정을 지원합니다. ACF만 학습하거나 CWT 임베딩과 ACF 임베딩을 concat하여 전체 인코더를 함께 학습할 수 있습니다. 원본 데이터와 학습된 가중치는 포함하지 않습니다.

## 1. 설치

Python 3.11을 권장합니다. CPU에서도 실행 가능하며 전체 데이터 학습에는 CUDA GPU가 적합합니다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[cwt,resnet,test]'
csi-fall --help
```

Windows PowerShell에서는 활성화 명령을 `.venv\Scripts\Activate.ps1`로 바꿉니다. CUDA 환경은 장비에 맞는 PyTorch·torchvision 조합을 먼저 설치하세요. 설치 명령이 없으면 `python -m csi_fall.cli`로도 실행할 수 있습니다. ACF와 compact CNN만 쓰면 `pip install -e .`로 충분합니다.

실제로 확인한 직접 의존성 버전은 [requirements-tested.txt](requirements-tested.txt)에 있습니다. 모든 전이 의존성을 고정한 lock 파일은 아닙니다.

## 2. 데이터 없이 전체 동작 확인

다음 데모는 합성 **raw CSV**를 생성합니다. 피처를 미리 만들어 건너뛰는 데모가 아닙니다.

```bash
csi-fall demo data/demo
csi-fall run data/demo/config.json --device cpu
csi-fall predict data/demo/outputs/demo/model/model.pt \
  --record data/demo/predict_record.json \
  --output outputs/demo_predictions.csv --device cpu
```

`run`은 전처리와 피처 생성, source 사전학습, target 검증셋으로 epoch 선택, target train+val 재학습, test/external 평가를 순서대로 수행합니다. 데모는 각 1 epoch이며 정확도 검증용 데이터가 아닙니다.

## 3. 실제 데이터로 설정 만들기

```bash
csi-fall index \
  --mendeley-root /path/to/mendeley/raw \
  --local-root /path/to/S1/labeled \
  --external-root /path/to/S2/labeled \
  --output experiment.json
csi-fall run experiment.json --device cuda
```

없는 데이터셋의 root 인자는 생략할 수 있습니다. source만 있으면 사전학습, target만 있으면 처음부터 학습합니다. external만으로는 학습할 수 없습니다.

`index`는 **새로운 녹화/task 단위 분할**을 생성합니다. 과거 54개 녹화 실험의 분할을 자동 복원하지 않습니다. 수집 root 아래의 모든 CSV를 포함하므로 원하는 참가자·녹화만 지정된 폴더에 두거나 생성한 JSON을 편집하세요.

- Mendeley: 같은 `E*_S*_C*_T*`의 활동 CSV를 task로 묶고 A 번호순으로 연결합니다. source train/val 약 80/20입니다. 세 RX가 항상 같은 분할에 속합니다.
- S1: 파일 단위 target train/val/test 약 60/20/20입니다.
- S2: 모든 파일을 external에 넣습니다.
- 참가자를 분리하려면 같은 사람의 파일들을 수동으로 같은 split에 배치하고 `group`도 같은 참가자 ID로 지정합니다. 기본 분할은 미관측 참가자 평가가 아닙니다.
- 생성된 활동 순서, 샘플률, 분할과 클래스 구성을 확인하세요. train/val에 두 클래스가 없으면 학습은 오류로 중단합니다.

직접 설정하는 형식은 [examples/config.json](examples/config.json), [examples/record.json](examples/record.json)을 참조하세요. JSON의 상대 경로는 **JSON 파일이 있는 폴더 기준**입니다.

## 4. 원본 데이터 형식

### 직접 수집 CSI: `format: local`

CSV 필수 열은 `dev_timestamp`(마이크로초), `csi`이며 학습에는 `label`도 필요합니다.

- `csi`: `[imag0, real0, imag1, real1, ...]` 또는 반대 순서의 실수 쌍입니다. 진폭만 사용하므로 두 순서의 크기는 같습니다.
- 기본 245개 complex pair에서 0-based 인덱스 121·122를 제외하고 균등하게 30개를 선택합니다. 다른 장비는 `raw_pairs`, `drop_pairs`를 명시해야 합니다. 과거 저장소의 192-pair HT-LTF 설정과 다릅니다.
- 32-bit timestamp wrap 보정 → 안정 정렬·중복 timestamp 제거 → 진폭 선형 보간입니다. 라벨은 가장 가까운 원본 시각의 것을 사용합니다.
- 기본 `fs=166.66666666666666`, 3초는 500개 시점입니다.
- 낙상: `a10`, `a11`, `a12`, `fall`, `1`. 비낙상: `none`, `0`, `nonfall`, `non_fall`, `a1`–`a9`. 알 수 없는 라벨은 거부합니다.
- 큰 timestamp gap도 기존 실험처럼 보간합니다. 최대 gap을 audit에 남깁니다. 장시간 단절을 건너뛰어야 하는 수집 데이터는 입력 녹화를 먼저 분리하세요.

### Mendeley LOS/NLOS: `format: mendeley`

각 segment는 CSV `path`와 `activity`를 가집니다. 원본 열 `timestamp_low`, `csi_1_{rx}_{subcarrier}`를 읽습니다. RX=1–3, subcarrier=1–30, 복소 문자열 예시는 `3.2+-1.4i`입니다.

- 기본 `fs=320`, 3초는 960개 시점입니다. RX별로 30채널 윈도우를 만듭니다.
- 활동 A02·A05가 낙상입니다. A01–A12 밖의 코드는 거부합니다.
- segment 순서대로 연결하고 0 이하 timestamp 간격을 한 nominal sample 간격으로 보정합니다.
- `resampled_rows`를 지정하면 그 정확한 길이에 맞춰 보간합니다. 과거 추출과 정확히 비교하려면 당시 길이를 지정해야 합니다.
- 생략하면 보정된 timestamp 지속시간에서 길이를 계산합니다. timestamp 정밀도가 손실된 원본에서는 과거 manifest 기반 길이와 달라질 수 있습니다.

### 공통 진폭: `format: amplitude`

균일 시간격자의 NPZ를 받습니다. `amplitude: (T,30)` 또는 `(T,90)`, `fs: scalar`, 학습용 `labels: (T,)`의 0/1을 저장하세요. 이 형식은 이미 진폭 복원과 시간 보정을 수행한 데이터입니다. 복소수 IQ를 그대로 넣지 마세요.

## 5. 윈도우와 누수 방지

기본 윈도우 3초, stride 0.25초입니다. 샘플률이 정수가 아닐 때 시작 시각마다 반올림하므로 고정 정수 stride를 반복할 때의 누적 오차를 피합니다.

- 낙상 이벤트 중심이 윈도우 안에 있고, `겹친 길이 / min(이벤트 길이, 윈도우 길이) > 0.5`이면 낙상입니다.
- 낙상과 전후 0.5초 guard 모두 겹치지 않으면 비낙상입니다.
- 나머지 경계 윈도우는 학습에서 제외합니다. 임계값과 guard는 설정 가능합니다.
- 같은 recording/group과 같은 raw 파일이 여러 분할에 들어가는 것을 거부합니다. 서로 다른 파일 이름으로 복사된 동일 녹화나 명시되지 않은 참가자 연관성까지 자동 판단하지는 않습니다.
- 정규화·결측치 대체는 source train에서만 적합합니다. source가 없으면 target train에서 적합합니다. target refit 때도 같은 정규화를 유지합니다.
- external은 정규화, epoch, 임계값 선택에 사용하지 않습니다. 예측 임계값은 0.5로 고정합니다.

## 6. 피처 선택

JSON의 `feature` 값을 지정합니다. 한 실험 폴더는 한 피처 설정을 사용합니다. 다른 설정은 새로운 `output`에 실행하세요.

| feature | 표현 | shape (샘플당) |
|---|---|---|
| `channel_mean` | 채널별 정규화 지연곱 맵을 동일 가중 평균, 기본값 | 1×129×64 |
| `channel_energy` | 채널 에너지 가중 맵 | 1×129×64 |
| `channel_savgol` | 채널별 약 51ms 목표 2차 SG 평활화 후 맵 평균 | 1×129×64 |
| `channel_hampel` | 같은 창에서 3×1.4826 MAD 이상치 대체 후 맵 평균 | 1×129×64 |
| `channel_detrend` | 채널별 선형 추세 제거 후 맵 평균 | 1×129×64 |
| `channel_difference` | 1차 차분 후 맵 평균 | 1×129×64 |
| `mean_signal` | 채널 신호를 먼저 평균한 뒤 맵 | 1×129×64 |
| `channel_acf` | 30채널 표준 1D ACF, 시간 위치 합산 | 1×129×30 |
| `pca_map` | 기존 q 기반 채널·PC 선택 후 합 보존 맵 | 1×129×64 |
| `legacy_map` | 기존 crop·percentile clip 방식 PCA ACF 맵 | 1×128×64 |
| `curve` | PCA 대표 신호의 표준 1D ACF | 1×129 |
| `scalars` | 위 1D ACF에서 10개 통계 | 10 → 결측 표시 포함 20 |

129 lag는 0–0.4초입니다. 맵의 64개 열은 3초 내 시간 구간입니다. 채널 평균 맵의 시간 평균은 해당 채널들의 표준 ACF 평균을 복원합니다. 계산은 float32, 맵 저장은 float16이므로 수치적으로 완전히 무손실인 것은 아닙니다. 상수 채널은 0으로 처리하고 채널 평균에서 제외합니다.

10개 통계는 1/e 감쇠 시각, 첫 영점, 첫 최소 lag/값, 반복 피크 lag/prominence, 첫 양의 영역 면적, 음수 비율, tail 절댓값 평균, 총변동량입니다. 관측되지 않은 교차·피크는 NaN으로 보관하고 train median으로 대체하며 결측 플래그를 붙입니다.

`cwt: true`이면 기존 PCA 대표신호의 **CWT → S1/S2/S3 처리 → 224×224 S3 이미지**를 함께 추출합니다. ACF가 PCA-free여도 CWT 경로는 PCA를 사용합니다. `cwt: false`이면 CWT 계산과 인코더를 모두 생략합니다.

## 7. 모델과 학습

- 맵: compact 2D CNN, ACF 임베딩 64차원. ACF-only compact 맵 모델은 44,098 parameters입니다.
- `scalars`: MLP, 128차원. `curve`: 1D CNN, 128차원.
- `backbone: resnet18`: CWT와 2D 맵 인코더를 ResNet18로 선택합니다. scalar/curve 인코더는 그대로입니다.
- `imagenet_pretrained: true`: ResNet18을 ImageNet 가중치로 초기화합니다. 첫 사용 시 torchvision이 다운로드합니다. 기본값은 false입니다.
- CWT 결합: CWT 임베딩과 ACF 임베딩 concat → 분류 head. 두 인코더 모두 역전파됩니다. ACF 보조 분류 loss 가중치는 기본 0.3입니다.
- weighted cross entropy, AdamW, weight decay 1e-4, grad clipping 1.0입니다.
- source: 최대 20 epoch, patience 3, LR 1e-3. target: 최대 30 epoch, patience 5, LR 1e-4.
- target validation macro F1으로 epoch를 선택한 후 source 가중치부터 target train+val로 같은 epoch 수를 refit합니다. `training.refit: false`이면 validation 선택 모델을 사용합니다.
- CPU/CUDA float32 학습입니다. 과거 bf16 실험과 모든 학습 수치가 동일하다고 주장하지 않습니다. `training.seed`를 바꿔 별도 output에 반복 실행하세요.

```bash
# 단계별 실행
csi-fall prepare experiment.json --device cuda --feature-batch 16
csi-fall train outputs/experiment --device cuda

# source-only 실험의 모델로 target-only 설정 초기화
csi-fall prepare target.json --device cuda
csi-fall train outputs/target --device cuda \
  --initialize outputs/source/model/model.pt
```

캐시는 녹화/RX별 폴더의 NPY 배열로 저장하며 학습에서는 memory map으로 필요한 행을 읽습니다. 전체 피처를 GPU 메모리에 올리지 않습니다. 원본과 설정 hash가 일치하는 완료 캐시는 재사용합니다. 모델 학습은 자동 재개 기능이 없으며 기존 model 폴더를 덮어쓰지 않습니다. 새로운 실험은 새 output을 사용하세요. `best.pt`, `last.pt`는 중간 산출물이며 raw 예측에는 설정까지 담긴 `model/model.pt`를 사용합니다.

## 8. 새 녹화 예측

```bash
csi-fall predict outputs/experiment/model/model.pt \
  --record examples/record.json \
  --output outputs/new_recording_predictions.csv --device cuda
```

`record.json`에 raw 형식, 경로, 샘플률을 지정합니다. label 없는 CSV도 예측 가능합니다. 녹화의 **모든 완전한 3초 윈도우**를 예측하며 학습용 경계 제외 규칙이나 정답 라벨로 결과를 선별하지 않습니다.

- CSV: RX, 시작/끝 시각, 낙상 확률, 0/1 예측.
- `.embeddings.npz`: 같은 CSV 행 순서의 인코더 임베딩(CWT 사용 시 concat 결과).
- `.audit.json`: 입력 처리 정보·피처 설정·임계값.

직렬화된 PyTorch 체크포인트는 이 코드로 직접 만들었거나 신뢰하는 출처의 것만 사용하세요.

## 9. 검증 및 코드 위치

```bash
SSQ_PARALLEL=0 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 python -m pytest -q
```

테스트는 표준 ACF 독립 계산, 시간 평균 복원, 채널 상쇄, 라벨·guard 경계, timestamp wrap, 분할 누수 거부, train-only 정규화, 12개 피처 인코더 역전파, raw부터 학습·예측 재현, CWT 결합 역전파를 포함합니다. 실제 수행 결과는 [VALIDATION.md](VALIDATION.md)에 기록합니다.

| 모듈 | 역할 |
|---|---|
| `io.py`, `index.py` | raw 파싱·시간 보정·라벨·분할 |
| `features.py`, `multichannel.py`, `legacy.py`, `scalars.py`, `standard.py` | 공통 피처 구현 |
| `prepare.py` | 캐시·manifest·입력 provenance |
| `models.py` | ACF/CWT 인코더·concat·분류 head |
| `engine.py` | 정규화·사전학습·미세조정·refit·평가 |
| `cli.py` | 전체 실행과 raw 추론 |

이 패키지는 현재 실험 코드를 독립 실행 가능하게 정리한 버전입니다. 과거 실험의 정확한 분할·Mendeley grid 길이·초기 가중치·수치 정밀도를 자동 복원하지 않으며, 데모 결과를 실제 낙상 탐지 성능으로 해석하지 않습니다.
