"""Provision one isolated account/device for a physical model E2E session.

The generated device TOML is copied to the Raspberry Pi test container.  The
access token is deliberately written to a separate temporary file so that the
shareable session manifest and final report contain no credentials.
"""

from __future__ import annotations

import argparse
import json
import secrets
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--mqtt-host", required=True)
    parser.add_argument("--mqtt-port", type=int, default=1884)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device-config", type=Path, required=True)
    parser.add_argument("--token-output", type=Path, required=True)
    return parser.parse_args()


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    token: str | None = None,
) -> dict:
    headers = {"Accept": "application/json"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}", data=payload, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {detail}") from exc
    return json.loads(raw) if raw else {}


def _render_device_config(
    *, tenant_id: str, device_id: UUID, mqtt_host: str, mqtt_port: int
) -> str:
    return f'''# Generated for one physical E2E validation session.
# Contains no password or API token.

[device]
tenant_id = "{tenant_id}"
device_id = "{device_id}"

[mqtt]
broker_host = "{mqtt_host}"
broker_port = {mqtt_port}
tls = false
ca_cert = ""
client_cert = ""
client_key = ""
username = ""
password = ""

[transport]
kind = "spi"
spi_bus = 0
spi_device = 0
max_speed_hz = 6000000
gpio_data_ready = 25
ready_gpio_chip = "/dev/gpiochip0"
ready_bias = "pull-down"
spi_batch_frames = 8
spi_transfer_api = "direct-ioctl"
spi_protocol_resync_seconds = 0.05
'''


def main() -> int:
    args = _arguments()
    health = _request(args.api, "GET", "/health")
    if health.get("status") != "ok":
        raise RuntimeError(f"API is not healthy: {health}")

    suffix = uuid4().hex
    password = f"HwE2e-{secrets.token_urlsafe(18)}!"
    signup = _request(
        args.api,
        "POST",
        "/api/v1/auth/signup",
        body={
            "email": f"hardware-e2e-{suffix}@example.invalid",
            "password": password,
            "name": "Physical hardware E2E",
            "service": "HOME",
        },
    )
    token = signup["accessToken"]
    user_id = UUID(signup["user"]["id"])
    tenant_id = f"home-{user_id}"
    device = _request(
        args.api,
        "POST",
        "/api/v1/devices",
        body={
            "name": "Physical model E2E",
            "room": "hardware-lab",
            "connection": "MQTT",
        },
        token=token,
    )
    device_id = UUID(device["id"])

    manifest = {
        "schema": "wifiguard-hardware-e2e-session-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "api": args.api,
        "mqtt_host": args.mqtt_host,
        "mqtt_port": args.mqtt_port,
        "tenant_id": tenant_id,
        "device_id": str(device_id),
        "mqtt_topic": device["mqttTopic"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.device_config.parent.mkdir(parents=True, exist_ok=True)
    args.token_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    args.device_config.write_text(
        _render_device_config(
            tenant_id=tenant_id,
            device_id=device_id,
            mqtt_host=args.mqtt_host,
            mqtt_port=args.mqtt_port,
        ),
        encoding="utf-8",
    )
    args.token_output.write_text(token, encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
