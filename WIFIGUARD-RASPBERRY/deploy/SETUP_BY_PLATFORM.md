# Platform-Specific Setup Guide

이 프로젝트는 Windows 개발 환경과 Raspberry Pi 실행 환경을 지원합니다.

## Windows 개발 환경

### 1. 의존성 설치

```bash
pip install -r requirements-windows.txt
```

### 2. 애플리케이션 실행

```bash
python main.py
```

**특징:**
- Mock SPI 인터페이스 사용 (`spi_interface_windows.py`)
- 실제 하드웨어 없이 합성 CSI 데이터 생성
- 전체 시스템 테스트 및 개발 가능
- 웹 인터페이스: http://localhost:8000

**Mock 데이터:**
- 매 100ms마다 합성 CSI 프레임 생성
- RSSI, 노이즈 플로어 시뮬레이션
- 낙상 감지 알고리즘 테스트 가능

## Raspberry Pi 실행 환경

### 1. 의존성 설치

```bash
pip install -r requirements-raspberrypi.txt
```

### 2. 애플리케이션 실행

```bash
python main.py
```

**특징:**
- 실제 SPI 인터페이스 사용 (`spi_interface.py`)
- RPi.GPIO를 통한 인터럽트 기반 데이터 수신
- ESP32C5 RX와 실시간 통신
- GPIO 핀 설정:
  - GPIO 25: 데이터 준비(데이터 준비) 인터럽트
  - GPIO 8: SPI CS 핀 (CE0)
  - SPI 버스: Bus 0, Device 0

### 3. GPIO 권한 설정

Raspberry Pi에서 GPIO를 사용하려면 사용자가 gpio 그룹에 속해야 합니다:

```bash
sudo usermod -a -G gpio $USER
# 그룹 변경 적용을 위해 로그아웃 후 다시 로그인
```

## 코드 구조

### 플랫폼 감지

`web_server.py`는 `platform.system()`을 사용하여 플랫폼을 감지하고 자동으로 적절한 SPI 인터페이스를 로드합니다:

```python
if platform.system() == "Windows":
    from spi_interface_windows import SPIInterface  # Mock 버전
else:
    from spi_interface import SPIInterface  # 실제 버전
```

### 파일 구조

```
.
├── main.py                      # 메인 진입점
├── web_server.py               # FastAPI 웹 서버 (플랫폼 감지 로직)
├── spi_interface.py            # Raspberry Pi용 실제 SPI 인터페이스
├── spi_interface_windows.py    # Windows용 Mock SPI 인터페이스
├── fall_detector.py            # 낙상 감지 알고리즘
├── state_manager.py            # 시스템 상태 관리
├── protocol.py                 # SPI 프로토콜 정의
├── requirements-windows.txt    # Windows 의존성
├── requirements-raspberrypi.txt # Raspberry Pi 의존성
└── static/
    └── index.html              # 웹 UI
```

## 개발 워크플로우

1. **Windows에서 개발/테스트:**
   ```bash
   pip install -r requirements-windows.txt
   python main.py
   ```
   - Mock 데이터로 UI와 로직 개발
   - 낙상 감지 알고리즘 테스트

2. **Raspberry Pi에 배포:**
   ```bash
   pip install -r requirements-raspberrypi.txt
   python main.py
   ```
   - 실제 하드웨어와 통신
   - 실제 CSI 데이터 처리

## 문제 해결

### Windows에서 `spidev` 오류가 발생하는 경우
✅ 정상 동작 - Mock 인터페이스를 사용하고 있습니다.

### Raspberry Pi에서 GPIO 오류가 발생하는 경우
1. GPIO 권한 확인: `groups | grep gpio`
2. 필요시 사용자를 gpio 그룹에 추가
3. SPI 활성화 확인: `raspi-config` → Interfacing Options → SPI

### Raspberry Pi에서 SPI 통신 오류
1. SPI 버스 확인: `ls -la /dev/spi*`
2. ESP32C5와의 연결 확인
3. 로그 파일 확인: `tail -f fall_detection.log`

## 환경 변수

필요에 따라 다음 환경 변수를 설정할 수 있습니다:

- `LOG_LEVEL`: 로깅 레벨 (DEBUG, INFO, WARNING, ERROR)
- `SPI_BUS`: SPI 버스 번호 (기본값: 0)
- `SPI_DEVICE`: SPI 장치 번호 (기본값: 0)
