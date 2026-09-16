"""모의 엣지 메시지가 MQTT → 통합 브리지 → Kafka까지 도달하는지 검증한다.

이 도구는 실제 장치나 DB를 사용하지 않는다. 매 실행마다 새 tenant/device UUID를 만들고
presence, signal, telemetry 한 건씩 발행한 뒤 Kafka에서 같은 device_id의 레코드를 읽어
계약 모델로 다시 검증한다.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import struct
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import paho.mqtt.client as mqtt
from kafka import KafkaConsumer, TopicPartition


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_SRC = BACKEND_ROOT / "packages" / "contracts" / "src"
BRIDGE_FILE = BACKEND_ROOT / "services" / "ingest" / "src" / "wifiguard_ingest" / "mqtt_bridge.py"
sys.path.insert(0, str(CONTRACTS_SRC))

from wifiguard_contracts import topics  # noqa: E402
from wifiguard_contracts.kafka import FeatureRecord, StatusRecord  # noqa: E402
from wifiguard_contracts.mqtt import (  # noqa: E402
    LinkStats,
    LoopStats,
    PresenceMsg,
    SignalMsg,
    TelemetryMsg,
)


def _load_bridge_class():
    """패키지의 DB consumer를 기동하지 않고 브리지 모듈만 불러온다."""
    spec = importlib.util.spec_from_file_location("wifiguard_mock_mqtt_bridge", BRIDGE_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"브리지 모듈을 불러올 수 없다: {BRIDGE_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MqttBridge


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mqtt-host", default="mosquitto")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--kafka-bootstrap", default="kafka:9092")
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser.parse_args()


def _end_offsets(consumer: KafkaConsumer, topic_names: tuple[str, ...]):
    partitions: list[TopicPartition] = []
    for topic_name in topic_names:
        ids = consumer.partitions_for_topic(topic_name)
        if not ids:
            raise RuntimeError(f"Kafka 토픽이 없거나 메타데이터를 읽지 못했다: {topic_name}")
        partitions.extend(TopicPartition(topic_name, partition_id) for partition_id in sorted(ids))
    consumer.assign(partitions)
    consumer.seek_to_end(*partitions)
    return partitions, {partition: consumer.position(partition) for partition in partitions}


def main() -> int:
    args = _arguments()
    tenant = f"home-{uuid4()}"
    device_id = uuid4()
    now = datetime.now(UTC)

    consumer = KafkaConsumer(
        bootstrap_servers=args.kafka_bootstrap.split(","),
        enable_auto_commit=False,
        group_id=None,
        request_timeout_ms=10_000,
    )
    kafka_topics = (topics.KAFKA_FEATURE_STREAM, topics.KAFKA_TELEMETRY)
    partitions, offsets_before = _end_offsets(consumer, kafka_topics)

    settings = SimpleNamespace(
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        mqtt_username="",
        mqtt_password="",
        mqtt_tls=False,
        mqtt_ca_cert="",
        kafka_enabled=True,
        kafka_bootstrap=args.kafka_bootstrap,
    )
    bridge = _load_bridge_class()(settings)
    publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"wg-mock-{device_id}")

    signal_values = [float(index) / 10.0 for index in range(16)]
    signal_b64 = base64.b64encode(struct.pack("<16f", *signal_values)).decode("ascii")
    messages = {
        "presence": PresenceMsg(
            tenant_id=tenant,
            device_id=device_id,
            ts=now,
            seq=1,
            tick_uptime_s=1.0,
            state="present",
        ),
        "signal": SignalMsg(
            tenant_id=tenant,
            device_id=device_id,
            ts=now,
            seq=2,
            signal_b64=signal_b64,
            signal_len=16,
            fs_hz=64.0,
            window_samples=16,
            window_span_s=0.234375,
            selected_subcarrier_count=8,
            selected_stream_count=1,
            selected_pc_indices="0",
            candidate_pc_count=1,
            selected_pc_count=1,
            input_frames=16,
            input_subcarriers=8,
            signal_q=1.0,
            presence_state="present",
            gate_reason="present",
        ),
        "telemetry": TelemetryMsg(
            tenant_id=tenant,
            device_id=device_id,
            ts=now,
            seq=3,
            link=LinkStats(connected=True, transport="replay", frames_ok=16),
            presence_loop=LoopStats(enabled=True, tick_count=1),
            feature_loop=LoopStats(enabled=True, tick_count=1),
            gate_open=True,
            signals_published=1,
            signals_gated=0,
            edge_version="mock-validation",
            uptime_s=1.0,
        ),
    }

    found: dict[str, dict] = {}
    try:
        bridge.start()
        deadline = time.monotonic() + args.timeout
        while not bridge.status()["connected"]:
            if time.monotonic() >= deadline:
                raise TimeoutError("MQTT 브리지가 제한 시간 안에 연결되지 않았다")
            time.sleep(0.05)

        publisher.connect(args.mqtt_host, args.mqtt_port, keepalive=20)
        publisher.loop_start()
        for leaf, message in messages.items():
            info = publisher.publish(
                topics.leaf_topic(tenant, device_id, leaf),
                message.model_dump_json().encode("utf-8"),
                qos=1,
            )
            info.wait_for_publish(timeout=args.timeout)
            if not info.is_published():
                raise TimeoutError(f"MQTT 발행 완료를 확인하지 못했다: {leaf}")

        while time.monotonic() < deadline and len(found) < 3:
            for records in consumer.poll(timeout_ms=250, max_records=100).values():
                for record in records:
                    parsed = json.loads(record.value)
                    if parsed.get("device_id") != str(device_id):
                        continue
                    if record.topic == topics.KAFKA_FEATURE_STREAM:
                        validated = FeatureRecord.model_validate(parsed)
                    else:
                        validated = StatusRecord.model_validate(parsed)
                    found[validated.payload.kind] = {
                        "topic": record.topic,
                        "partition": record.partition,
                        "offset": record.offset,
                        "seq": validated.payload.seq,
                    }
            if len(found) < 3:
                time.sleep(0.05)
    finally:
        publisher.loop_stop()
        publisher.disconnect()
        bridge.stop()
        offsets_after = consumer.end_offsets(partitions)
        consumer.close()

    expected = {"presence", "signal", "telemetry"}
    result = {
        "schema": "wifiguard-mock-mqtt-kafka-v1",
        "tenant_id": tenant,
        "device_id": str(device_id),
        "expected_kinds": sorted(expected),
        "found": found,
        "bridge": bridge.status(),
        "offset_delta": {
            f"{partition.topic}:{partition.partition}": offsets_after[partition]
            - offsets_before[partition]
            for partition in partitions
        },
        "pass": set(found) == expected and bridge.status()["forwarded"] == 3,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
