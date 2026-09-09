# 3초 최고 모델 수집 검증 추론 패키지

이 패키지는 기존 3초 윈도우 S3+PCA-ACF dual ResNet18 최고 모델로
수집 데이터 검증셋을 재현 가능하게 추론한다.

## 포함 내용

- 모델: native-Hz 수집 데이터 파인튜닝 최고 체크포인트, epoch 5
- 검증 데이터: 연속 CSV 13개, 375개 윈도우
- 레이블: 비낙상 351개, 낙상 24개
- 윈도우/스트라이드: 3초 / 0.25초
- 수집 주파수: 약 166.67 Hz 유지
- 피처: S3 `(224,224)` + PCA-ACF `(1,128,64)`

## 설치

Linux CUDA:

```bash
bash setup_cuda.sh
```

Windows PowerShell CUDA:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_cuda.ps1
```

CPU 환경에서는 직접 가상환경을 만든 뒤 다음 패키지를 설치해도 된다.

```bash
pip install torch numpy scikit-learn
```

## 추론

Linux:

```bash
.venv/bin/python infer_validation.py
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe .\infer_validation.py
```

장치를 명시하려면 `--device cuda`, `--device cpu`를 사용한다.

```bash
.venv/bin/python infer_validation.py --device cuda --batch-size 64
```

## 출력

`outputs/`에 다음 파일이 생성된다.

- `validation_predictions.csv`: 윈도우별 확률과 네 가지 예측값
- `summary.json`: raw 및 후처리 평가지표

기본 출력 열:

- `pred_threshold_0p5`: 모델 기본 임계값 0.5
- `pred_threshold_selected`: 검증 선택 임계값 0.468
- `pred_threshold_selected_mode5`: 각 recording 안에서만 적용한 mode5
- `proba_fall`: 낙상 확률

기존 재현 목표는 다음과 같다.

| 설정 | Macro F1 | 낙상 Recall | TN/FP/FN/TP |
|---|---:|---:|---:|
| 임계값 0.5 | 0.7926 | 0.6250 | 341/10/9/15 |
| 임계값 0.468 | 0.8004 | 0.7083 | 338/13/7/17 |
| 임계값 0.468 + mode5 | 0.8936 | 0.7500 | 348/3/6/18 |

이 검증셋은 모델/임계값/후처리 선택에 사용되었으므로 독립 테스트셋이 아니다.

