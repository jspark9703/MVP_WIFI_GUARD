# CSI 수집 도구 (`main.py`) 실행 방법

## 사전 요구사항

- Python 3.6 이상
- 스크립트에 필요한 패키지(예: PyQt5, pyserial, pandas 등)가 설치된 환경에서 실행

## 기본 형식

```bash
python main.py -p <시리얼포트> [옵션...]
```

## 인자 요약

| 옵션 | 설명 |
|------|------|
| `-p`, `--port` | **필수.** 시리얼 포트 (Windows: `COM3`, Linux: `/dev/ttyUSB0` 등) |
| `-s`, `--store` | CSV 저장 경로. **생략 시** 자동 경로 사용 (아래 참고) |
| `--suffix` | 자동 CSV 파일명에 붙일 접미사 (`-s` 생략 시에만 의미 있음) |
| `-t`, `--time` | 측정·저장 시간(초). 지정 시 해당 시간 후 자동 종료 |
| `-l`, `--log` | 시리얼 기타/비 CSI 로그 파일 (기본: `./csi_data_log.txt`) |

### CSV 자동 저장 경로 (`-s` 생략)

- **현재 작업 디렉터리(cwd)** 기준: `data/{yymmdd}/{hhmmss}.csv`
- `--suffix` 지정: `data/{yymmdd}/{hhmmss}_{suffix}.csv`

예: `tools` 폴더에서 `python main.py ...` 를 실행하면  
`tools\data\260323\003327_none.csv` 처럼 **프로젝트 아래 `data` 폴더**에 저장됩니다.

> 실행 시 콘솔에 `CSV save path:` / `Log save path:` 로 **절대 경로**가 출력되므로, 그 경로에서 파일을 확인하면 됩니다.

## 실행 예시

### 1) 최소 실행 (포트만 지정, 자동 CSV 경로)

```bash
python main.py -p COM3
```

### 2) CSV 파일명에 접미사 추가

```bash
python main.py -p COM3 --suffix experiment1
```

### 3) 60초만 측정 후 자동 종료

```bash
python main.py -p COM3 -t 60
```

### 4) 접미사 + 시간 제한

```bash
python main.py -p COM3 --suffix trial_a -t 120
```

### 5) CSV 저장 경로를 직접 지정

```bash
python main.py -p COM3 -s ./output/my_csi.csv
```

### 6) 로그 파일 경로 지정

```bash
python main.py -p COM3 -l ./logs/serial_extra.txt
```

### Linux 예시

```bash
python3 main.py -p /dev/ttyUSB0 -t 60
```

## 참고

- `--time` (`-t`) 값은 **양의 정수(초)** 여야 합니다.
- 자동/지정 경로의 상위 디렉터리가 없으면 실행 시 생성됩니다.
