"""Validate the complete software path with the real segmentation checkpoint.

No Raspberry Pi or ESP receiver is used. The validator deliberately keeps two
claims separate:

1. Synthetic 30-subcarrier windows travel through MQTT -> bridge -> Kafka ->
   real feature extraction/model -> inference-result Kafka.
2. Contract-valid positive inference records travel through the production
   causal mode-5 state machine -> PostgreSQL -> authenticated REST API.

The second lane checks persistence without pretending that a synthetic sine
wave is a real fall or changing the checkpoint threshold.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import paho.mqtt.client as mqtt
from kafka import KafkaConsumer, KafkaProducer
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import InferenceResult
from wifiguard_contracts.mqtt import SignalMsg, encode_amplitude, encode_signal


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--kafka-bootstrap", default="127.0.0.1:9092")
    parser.add_argument("--model-count", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path)
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


def _representative_amplitude() -> np.ndarray:
    sample_count = 960
    fs_hz = 320.0
    timeline = np.arange(sample_count, dtype=np.float32) / fs_hz
    channels = []
    for index in range(30):
        carrier = np.sin(2 * np.pi * (0.65 + index * 0.025) * timeline + index * 0.07)
        detail = 0.12 * np.cos(2 * np.pi * (2.2 + index * 0.018) * timeline)
        channels.append(carrier + detail)
    return np.stack(channels, axis=1).astype(np.float32)


def _wait_for_ingest(api: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = _request(api, "GET", "/health")
        ingest = latest.get("ingest") or {}
        bridge = ingest.get("bridge") or {}
        consumer = ingest.get("consumer") or {}
        if (
            latest.get("status") == "ok"
            and bridge.get("connected") is True
            and consumer.get("running") is True
        ):
            return latest
        time.sleep(0.25)
    raise TimeoutError(f"API ingest did not become ready: {latest}")


def _result_consumer(bootstrap: str) -> KafkaConsumer:
    consumer = KafkaConsumer(
        topics.KAFKA_INFERENCE_RESULT,
        bootstrap_servers=bootstrap.split(","),
        group_id=f"wifiguard-software-e2e-{uuid4()}",
        auto_offset_reset="latest",
        enable_auto_commit=False,
        consumer_timeout_ms=500,
    )
    deadline = time.monotonic() + 10
    while not consumer.assignment() and time.monotonic() < deadline:
        consumer.poll(timeout_ms=250)
    if not consumer.assignment():
        consumer.close()
        raise TimeoutError("inference result consumer assignment was not established")
    for partition in consumer.assignment():
        consumer.position(partition)
    return consumer


def _publish_model_inputs(
    args: argparse.Namespace,
    tenant: str,
    device_id: UUID,
    first_seq: int,
) -> set[int]:
    amplitude = _representative_amplitude()
    signal = amplitude.mean(axis=1, dtype=np.float32)
    expected = set(range(first_seq, first_seq + args.model_count))
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"wifiguard-software-e2e-{device_id}",
    )
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
    client.loop_start()
    try:
        base_time = datetime.now(UTC)
        for offset, seq in enumerate(sorted(expected)):
            message = SignalMsg(
                tenant_id=tenant,
                device_id=device_id,
                ts=base_time + timedelta(milliseconds=250 * offset),
                seq=seq,
                signal_b64=encode_signal(signal),
                signal_len=len(signal),
                amplitude_b64=encode_amplitude(amplitude),
                amplitude_rows=amplitude.shape[0],
                amplitude_cols=amplitude.shape[1],
                fs_hz=320.0,
                window_samples=len(amplitude),
                window_span_s=3.0,
                selected_subcarrier_count=30,
                selected_stream_count=30,
                selected_pc_indices="0",
                candidate_pc_count=1,
                selected_pc_count=1,
                input_frames=len(amplitude),
                input_subcarriers=245,
                signal_q=None,
                presence_state="present",
                gate_reason="forced",
            )
            info = client.publish(
                topics.leaf_topic(tenant, device_id, "signal"),
                message.model_dump_json().encode("utf-8"),
                qos=1,
            )
            info.wait_for_publish(timeout=15)
            if not info.is_published():
                raise TimeoutError(f"MQTT signal was not acknowledged: seq={seq}")
    finally:
        client.loop_stop()
        client.disconnect()
    return expected


def _await_model_results(
    consumer: KafkaConsumer,
    device_id: UUID,
    expected: set[int],
    timeout: float,
) -> list[InferenceResult]:
    found: dict[int, InferenceResult] = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and set(found) != expected:
        for records in consumer.poll(timeout_ms=500, max_records=100).values():
            for record in records:
                result = InferenceResult.model_validate_json(record.value)
                if result.device_id == device_id and result.seq in expected:
                    found[result.seq] = result
    if set(found) != expected:
        raise TimeoutError(f"missing model results: {sorted(expected - set(found))}")
    return [found[seq] for seq in sorted(found)]


def _publish_positive_state_sequence(
    bootstrap: str,
    tenant: str,
    device_id: UUID,
    first_seq: int,
) -> datetime:
    producer = KafkaProducer(bootstrap_servers=bootstrap.split(","))
    base_time = datetime.now(UTC) + timedelta(seconds=1)
    try:
        for offset in range(5):
            occurred_at = base_time + timedelta(milliseconds=250 * offset)
            result = InferenceResult(
                tenant_id=tenant,
                device_id=device_id,
                ts=occurred_at,
                seq=first_seq + offset,
                proba_fall=0.99,
                threshold=0.5,
                postprocess="none",
                inferred_at=datetime.now(UTC) + timedelta(milliseconds=offset),
                feature_ms=0.0,
                infer_ms=0.0,
                model_version="software-e2e-state-path",
                scale_cache_hit=True,
            )
            producer.send(
                topics.KAFKA_INFERENCE_RESULT,
                key=str(device_id).encode(),
                value=result.model_dump_json().encode(),
            ).get(timeout=10)
        producer.flush()
    finally:
        producer.close()
    # With causal mode-5, the first majority occurs on the third positive.
    return base_time + timedelta(milliseconds=500)


def _await_fall(api: str, token: str, device_id: UUID, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = _request(api, "GET", "/api/v1/falls?limit=50", token=token)
        for item in latest.get("items", []):
            if (
                item.get("deviceId") == str(device_id)
                and item.get("source") == "EDGE"
                and abs(float(item.get("confidence", 0.0)) - 0.99) < 1e-6
            ):
                return item
        time.sleep(0.25)
    raise TimeoutError(f"confirmed fall did not reach the REST API: {latest}")


def main() -> int:
    args = _arguments()
    if args.model_count < 1:
        raise ValueError("--model-count must be at least 1")

    initial_health = _wait_for_ingest(args.api, args.timeout)
    suffix = uuid4().hex
    signup = _request(
        args.api,
        "POST",
        "/api/v1/auth/signup",
        body={
            "email": f"software-e2e-{suffix}@example.invalid",
            "password": f"E2e-{suffix}!",
            "name": "Software E2E",
            "service": "HOME",
        },
    )
    token = signup["accessToken"]
    user_id = UUID(signup["user"]["id"])
    tenant = f"home-{user_id}"
    device = _request(
        args.api,
        "POST",
        "/api/v1/devices",
        body={"name": "Segmentation software E2E", "room": "lab", "connection": "MQTT"},
        token=token,
    )
    device_id = UUID(device["id"])

    consumer = _result_consumer(args.kafka_bootstrap)
    first_seq = int(time.time_ns() % 1_000_000_000)
    try:
        expected = _publish_model_inputs(args, tenant, device_id, first_seq)
        actual_results = _await_model_results(
            consumer, device_id, expected, args.timeout
        )
    finally:
        consumer.close()

    expected_fall_at = _publish_positive_state_sequence(
        args.kafka_bootstrap,
        tenant,
        device_id,
        first_seq + args.model_count + 100,
    )
    fall = _await_fall(args.api, token, device_id, args.timeout)
    final_health = _request(args.api, "GET", "/health")
    ingest = final_health.get("ingest") or {}
    consumer_status = ingest.get("consumer") or {}
    fall_sink = ingest.get("fall_sink") or {}

    report = {
        "schema": "wifiguard-model-software-e2e-v1",
        "status": "SOFTWARE_E2E_PASS",
        "hardware_exercised": False,
        "actual_model_path": {
            "messages": len(actual_results),
            "sequences": [item.seq for item in actual_results],
            "probabilities": [item.proba_fall for item in actual_results],
            "threshold": actual_results[0].threshold,
            "model_version": actual_results[0].model_version,
            "mean_feature_ms": sum(item.feature_ms or 0 for item in actual_results)
            / len(actual_results),
            "mean_infer_ms": sum(item.infer_ms or 0 for item in actual_results)
            / len(actual_results),
        },
        "persistence_path": {
            "state_input": "five contract-valid positive inference records",
            "expected_first_majority_at": expected_fall_at.isoformat(),
            "fall_id": fall["id"],
            "source": fall["source"],
            "confidence": fall["confidence"],
            "api_visible": True,
            "sink_written_total": fall_sink.get("written"),
        },
        "ingest": {
            "initial_status": initial_health.get("status"),
            "final_status": final_health.get("status"),
            "counts": consumer_status.get("counts"),
            "errors": consumer_status.get("errors"),
            "fall_sink_errors": fall_sink.get("errors"),
        },
        "not_proven": [
            "physical ESP32/Raspberry Pi SPI and GPIO transport",
            "accuracy on real fall/non-fall CSI recordings",
            "production multi-device network capacity",
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
