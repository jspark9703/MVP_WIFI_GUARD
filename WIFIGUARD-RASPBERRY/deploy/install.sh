#!/usr/bin/env bash
# 라즈베리파이 초기 설치 스크립트.
# 근거: deploy/SETUP_BY_PLATFORM.md (시제품 infra/raspberry 의 설정 절차)
#
# 미검증 — 실기에서 한 번도 돌린 적 없다. 명세 §4 Phase 5-2의 항목을 옮겨 적은 것이다.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/wifiguard-edge}"

echo "== 1. SPI 활성화 =="
# raspi-config nonint: 0 = enable
sudo raspi-config nonint do_spi 0
ls -la /dev/spi* || { echo "SPI 장치가 없다. 재부팅 후 다시 확인할 것."; }

echo "== 2. GPIO/SPI 권한 =="
sudo usermod -a -G gpio,spi "$USER"
echo "   그룹 변경은 재로그인 후 적용된다. 확인: groups | grep -E 'gpio|spi'"

echo "== 3. 파이썬 환경 =="
sudo mkdir -p "$APP_DIR"
sudo chown "$USER:$USER" "$APP_DIR"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
# ssqueezepy 는 ARM64 휠이 없을 수 있다. 실패하면 features/common.py 의
# fallback_cwt(NumPy 전용 경로)로 동작시키고 소스 빌드를 검토할 것 (명세 R2).
"$APP_DIR/.venv/bin/pip" install -r requirements-pi.txt

echo "== 4. 로그를 tmpfs 로 (SD카드 마모 완화) =="
grep -q '/var/log.*tmpfs' /etc/fstab || \
  echo 'tmpfs /var/log tmpfs defaults,noatime,nosuid,size=64m 0 0' | sudo tee -a /etc/fstab

echo "== 5. 하드웨어 워치독 =="
grep -q '^dtparam=watchdog=on' /boot/firmware/config.txt 2>/dev/null || \
  echo 'dtparam=watchdog=on' | sudo tee -a /boot/firmware/config.txt

echo "== 6. systemd 서비스 =="
sudo cp deploy/wifiguard-edge.service /etc/systemd/system/
sudo systemctl daemon-reload
echo "   기동: sudo systemctl enable --now wifiguard-edge"
echo "   (단, src/wifiguard_edge/__main__.py 구현 전에는 기동에 실패한다)"

echo "== 7. 핀맵 확인 (BCM) =="
cat <<'PINS'
   MOSI 10 / MISO 9 / SCLK 11 / CE0 8 / DATA_READY 25 (RISING)
   spidev(0, 0), 10MHz, mode 0, MSB first, 8192B 전이중 / 50ms
PINS

echo "완료. 재부팅을 권장한다."
