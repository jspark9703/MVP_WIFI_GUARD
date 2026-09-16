#!/usr/bin/env bash
# 라즈베리파이 초기 설치 스크립트.
# 근거: deploy/SETUP_BY_PLATFORM.md (시제품 infra/raspberry 의 설정 절차)
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
APP_DIR="${APP_DIR:-/opt/wifiguard-edge}"
APP_GROUP="$(id -gn)"
CONTRACTS_DIR="${CONTRACTS_DIR:-$REPO_DIR/../WIFIGUARD-BACKEND/packages/contracts}"

if [[ ! -f "$CONTRACTS_DIR/pyproject.toml" ]]; then
  echo "공통 계약 패키지를 찾을 수 없다: $CONTRACTS_DIR" >&2
  echo "통합 작업장 밖에서 설치할 때는 CONTRACTS_DIR=/실제/경로를 지정할 것." >&2
  exit 1
fi

echo "== 1. SPI 활성화 =="
# raspi-config nonint: 0 = enable
sudo raspi-config nonint do_spi 0
ls -la /dev/spi* || { echo "SPI 장치가 없다. 재부팅 후 다시 확인할 것."; }

echo "== 1-1. Batch8용 spidev 버퍼 8192바이트 =="
CMDLINE=/boot/firmware/cmdline.txt
if [[ -f "$CMDLINE" ]]; then
  if grep -qE '(^| )spidev\.bufsiz=' "$CMDLINE"; then
    sudo sed -E -i '1 s/(^| )spidev\.bufsiz=[^ ]+/\1spidev.bufsiz=8192/' "$CMDLINE"
  else
    sudo sed -i '1 s/$/ spidev.bufsiz=8192/' "$CMDLINE"
  fi
fi
echo 'options spidev bufsiz=8192' | sudo tee /etc/modprobe.d/wifiguard-spidev.conf >/dev/null
if [[ -r /sys/module/spidev/parameters/bufsiz ]]; then
  CURRENT_BUFSIZ=$(cat /sys/module/spidev/parameters/bufsiz)
  [[ "$CURRENT_BUFSIZ" -ge 4608 ]] || echo "   현재 버퍼는 ${CURRENT_BUFSIZ}B다. 재부팅 후 8192B가 적용된다."
fi

echo "== 2. GPIO/SPI 권한 =="
sudo usermod -a -G gpio,spi "$USER"
echo "   그룹 변경은 재로그인 후 적용된다. 확인: groups | grep -E 'gpio|spi'"

echo "== 3. 파이썬 환경 =="
sudo install -d -o "$USER" -g "$APP_GROUP" "$APP_DIR" "$APP_DIR/src" "$APP_DIR/config"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
(cd "$REPO_DIR" && "$APP_DIR/.venv/bin/pip" install -r requirements-pi.txt)
"$APP_DIR/.venv/bin/pip" install "$CONTRACTS_DIR"

echo "== 3-1. 애플리케이션 코드와 설정 =="
sudo cp -a "$REPO_DIR/src/." "$APP_DIR/src/"
sudo cp "$REPO_DIR/config/default.toml" "$APP_DIR/config/default.toml"
if [[ ! -f "$APP_DIR/config/device.toml" ]]; then
  sudo cp "$REPO_DIR/config/device.toml.example" "$APP_DIR/config/device.toml"
  echo "   $APP_DIR/config/device.toml 의 tenant_id·device_id·MQTT 값을 채울 것."
else
  echo "   기존 $APP_DIR/config/device.toml 은 보존했다."
fi
sudo chown -R "$USER:$APP_GROUP" "$APP_DIR/src" "$APP_DIR/config"

echo "== 4. 로그를 tmpfs 로 (SD카드 마모 완화) =="
grep -q '/var/log.*tmpfs' /etc/fstab || \
  echo 'tmpfs /var/log tmpfs defaults,noatime,nosuid,size=64m 0 0' | sudo tee -a /etc/fstab

echo "== 5. 하드웨어 워치독 =="
grep -q '^dtparam=watchdog=on' /boot/firmware/config.txt 2>/dev/null || \
  echo 'dtparam=watchdog=on' | sudo tee -a /boot/firmware/config.txt

echo "== 6. systemd 서비스 =="
sudo cp "$SCRIPT_DIR/wifiguard-edge.service" /etc/systemd/system/
sudo sed -i \
  -e "s/^User=.*/User=$USER/" \
  -e "s/^Group=.*/Group=$(id -gn)/" \
  -e "s#^WorkingDirectory=.*#WorkingDirectory=$APP_DIR#" \
  -e "s#^Environment=PYTHONPATH=.*#Environment=PYTHONPATH=$APP_DIR/src#" \
  -e "s#^ExecStart=.*#ExecStart=$APP_DIR/.venv/bin/python -m wifiguard_edge#" \
  /etc/systemd/system/wifiguard-edge.service
sudo systemctl daemon-reload
echo "   기동: sudo systemctl enable --now wifiguard-edge"
echo "   설정 확인: $APP_DIR/config/device.toml"

echo "== 7. 핀맵 확인 (BCM) =="
cat <<'PINS'
   MOSI 10 / MISO 9 / SCLK 11 / CE0 8 / DATA_READY 25
   spidev(0, 0), 6MHz, mode 0, MSB first, 4608B Batch8
PINS

echo "완료. 재부팅을 권장한다."
