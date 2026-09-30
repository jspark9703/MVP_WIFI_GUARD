#!/usr/bin/env bash
set -euo pipefail

BUNDLE="/home/wifiguard/wifiguard-physical-e2e-bundle.tar.gz"
: "${EXPECTED_SHA256:?EXPECTED_SHA256 must be provided by the deployment wrapper}"
: "${MQTT_HOST:?MQTT_HOST must be provided by the deployment wrapper}"
MQTT_PORT="${MQTT_PORT:-1884}"
WORK_ROOT="/home/wifiguard/wifiguard-physical-e2e"
OLD_COMPOSE_ROOT="/home/wifiguard/wifi-guard-edge"
IMAGE="wifiguard/physical-e2e-edge:20260927-health"
CONTAINER="wifiguard-physical-model-e2e"

echo '[bundle checksum]'
printf '%s  %s\n' "${EXPECTED_SHA256,,}" "$BUNDLE" | sha256sum -c -

echo '[stop legacy edge reader only]'
if [[ -f "$OLD_COMPOSE_ROOT/compose.yaml" ]]; then
  (cd "$OLD_COMPOSE_ROOT" && sudo docker compose --env-file edge.env stop edge-amfall-spi)
fi
if sudo docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  sudo docker stop -t 20 "$CONTAINER" >/dev/null 2>&1 || true
fi

echo '[sender-off electrical preflight]'
ready="$(gpioget --numeric -c gpiochip0 -b pull-down 25)"
echo "ready=$ready"
if [[ "$ready" != "0" ]]; then
  echo 'READY_NOT_LOW: keep sender OFF; deployment aborted'
  exit 10
fi

bufsiz="$(cat /sys/module/spidev/parameters/bufsiz)"
echo "spidev_bufsiz=$bufsiz"
if (( bufsiz < 4608 )); then
  echo 'SPIDEV_BUFFER_TOO_SMALL: deployment aborted'
  exit 11
fi

if ! timeout 3 bash -c "</dev/tcp/$MQTT_HOST/$MQTT_PORT"; then
  echo "MQTT_TCP_UNREACHABLE=$MQTT_HOST:$MQTT_PORT"
  exit 12
fi
echo "mqtt_tcp=pass host=$MQTT_HOST port=$MQTT_PORT"

echo '[device users after legacy stop]'
if sudo fuser -s /dev/spidev0.0 /dev/gpiochip0; then
  sudo fuser -v /dev/spidev0.0 /dev/gpiochip0 2>&1 || true
  echo 'SPI_OR_GPIO_BUSY: deployment aborted'
  exit 13
fi

mkdir -p "$WORK_ROOT"
tar -xzf "$BUNDLE" -C "$WORK_ROOT"

echo '[build isolated current-repository edge image]'
sudo docker build \
  --file "$WORK_ROOT/WIFIGUARD-RASPBERRY/deploy/hardware-e2e/Dockerfile" \
  --tag "$IMAGE" \
  "$WORK_ROOT"

if sudo docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  sudo docker rm -f "$CONTAINER" >/dev/null
fi

echo '[start physical E2E reader while sender remains OFF]'
sudo docker run -d \
  --name "$CONTAINER" \
  --network bridge \
  --device /dev/spidev0.0 \
  --device /dev/gpiochip0 \
  "$IMAGE" >/dev/null

sleep 8

status="$(sudo docker inspect "$CONTAINER" --format '{{.State.Status}}')"
echo "container_status=$status image=$IMAGE"
sudo docker logs --tail 20 "$CONTAINER"
gpioinfo -c gpiochip0 25
sudo pinctrl get 25

if [[ "$status" != "running" ]]; then
  echo 'E2E_EDGE_NOT_RUNNING'
  exit 14
fi

if [[ "$(gpioget --numeric -c gpiochip0 -b pull-down 25 2>/dev/null || true)" == "1" ]]; then
  echo 'READY_BECAME_HIGH_UNEXPECTEDLY: sender must remain OFF'
  exit 15
fi

echo 'PHYSICAL_E2E_EDGE_ARMED_SENDER_OFF'
