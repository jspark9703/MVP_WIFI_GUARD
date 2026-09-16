"""Raspberry 합성 replay를 실제 MQTT → Kafka 경로로 흘려 검증한다."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

from kafka import KafkaConsumer

from validate_mock_mqtt_kafka import (
    _end_offsets,
    _load_bridge_class,
    topics,
    FeatureRecord,
    StatusRecord,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
RASPBERRY_ROOT = BACKEND_ROOT.parent / "WIFIGUARD-RASPBERRY"
sys.path.insert(0, str(RASPBERRY_ROOT / "src"))

from wifiguard_edge.__main__ import EdgeApp  # noqa: E402
from wifiguard_edge.config import load_config  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mqtt-host", default="mosquitto")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--kafka-bootstrap", default="kafka:9092")
    parser.add_argument("--duration", type=float, default=12.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    tenant = f"home-{uuid4()}"
    device_id = uuid4()
    kafka_topics = (topics.KAFKA_FEATURE_STREAM, topics.KAFKA_TELEMETRY)
    consumer = KafkaConsumer(
        bootstrap_servers=args.kafka_bootstrap.split(","),
        enable_auto_commit=False,
        group_id=None,
        request_timeout_ms=10_000,
    )
    partitions, offsets_before = _end_offsets(consumer, kafka_topics)

    settings = type(
        "Settings",
        (),
        {
            "mqtt_host": args.mqtt_host,
            "mqtt_port": args.mqtt_port,
            "mqtt_username": "",
            "mqtt_password": "",
            "mqtt_tls": False,
            "mqtt_ca_cert": "",
            "kafka_enabled": True,
            "kafka_bootstrap": args.kafka_bootstrap,
        },
    )()
    bridge = _load_bridge_class()(settings)
    app = None
    counts: Counter[str] = Counter()
    sample_records: dict[str, dict] = {}

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        bridge.start()
        deadline = time.monotonic() + args.timeout
        while not bridge.status()["connected"]:
            if time.monotonic() >= deadline:
                raise TimeoutError("MQTT 브리지가 제한 시간 안에 연결되지 않았다")
            time.sleep(0.05)

        with tempfile.TemporaryDirectory(prefix="wifiguard-replay-") as temp_name:
            config_dir = Path(temp_name)
            shutil.copyfile(RASPBERRY_ROOT / "config" / "default.toml", config_dir / "default.toml")
            (config_dir / "device.toml").write_text(
                "\n".join(
                    [
                        "[device]",
                        f'tenant_id = "{tenant}"',
                        f'device_id = "{device_id}"',
                        "",
                        "[transport]",
                        'kind = "replay"',
                        'replay_source = "synthetic"',
                        "replay_speed = 1.0",
                        "",
                        "[mqtt]",
                        f'broker_host = "{args.mqtt_host}"',
                        f"broker_port = {args.mqtt_port}",
                        "tls = false",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            app = EdgeApp(load_config(config_dir))
            app.start()
            time.sleep(args.duration)
            app.stop()
            app = None

        bridge._producer.flush(timeout=args.timeout)
        read_deadline = time.monotonic() + args.timeout
        while time.monotonic() < read_deadline:
            for records in consumer.poll(timeout_ms=300, max_records=500).values():
                for record in records:
                    parsed = json.loads(record.value)
                    if parsed.get("device_id") != str(device_id):
                        continue
                    if record.topic == topics.KAFKA_FEATURE_STREAM:
                        validated = FeatureRecord.model_validate(parsed)
                    else:
                        validated = StatusRecord.model_validate(parsed)
                    kind = validated.payload.kind
                    counts[kind] += 1
                    sample_records.setdefault(
                        kind,
                        {
                            "topic": record.topic,
                            "partition": record.partition,
                            "offset": record.offset,
                            "seq": validated.payload.seq,
                        },
                    )
            if counts["presence"] > 0 and counts["signal"] > 0 and counts["telemetry"] > 0:
                break
            time.sleep(0.05)
    finally:
        if app is not None:
            app.stop()
        bridge.stop()
        offsets_after = consumer.end_offsets(partitions)
        consumer.close()

    passed = counts["presence"] > 0 and counts["signal"] > 0 and counts["telemetry"] > 0
    result = {
        "schema": "wifiguard-replay-mqtt-kafka-v1",
        "tenant_id": tenant,
        "device_id": str(device_id),
        "duration_seconds": args.duration,
        "records_by_kind": dict(sorted(counts.items())),
        "sample_records": sample_records,
        "bridge": bridge.status(),
        "offset_delta": {
            f"{partition.topic}:{partition.partition}": offsets_after[partition]
            - offsets_before[partition]
            for partition in partitions
        },
        "pass": passed,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
