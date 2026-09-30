"""Observe and correlate one real ESP32 -> Pi -> model E2E session.

Run this inside the API container, which already has kafka-python and the
wire-contract package installed.  It consumes only records produced after its
Kafka assignments are established, so old validation traffic cannot satisfy a
new physical test.
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from kafka import KafkaConsumer
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, InferenceResult, StatusRecord


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap", default="kafka:29092")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--minimum-matches", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


def _request(base_url: str, path: str, token: str | None = None) -> dict | list:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}", headers=headers, method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {path} failed: HTTP {exc.code}: {detail}") from exc
    return json.loads(raw) if raw else {}


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int(0.95 * len(ordered)))
    return {
        "mean": statistics.fmean(ordered),
        "p50": statistics.median(ordered),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


def main() -> int:
    args = _arguments()
    if args.minimum_matches < 1:
        raise ValueError("--minimum-matches must be at least 1")

    session = json.loads(args.session.read_text(encoding="utf-8"))
    token = args.token_file.read_text(encoding="utf-8").strip()
    tenant_id = session["tenant_id"]
    device_id = UUID(session["device_id"])
    started_at = datetime.now(UTC)

    health_before = _request(args.api, "/health")
    consumer = KafkaConsumer(
        topics.KAFKA_FEATURE_STREAM,
        topics.KAFKA_TELEMETRY,
        topics.KAFKA_INFERENCE_RESULT,
        bootstrap_servers=args.bootstrap.split(","),
        group_id=f"wifiguard-hardware-e2e-{uuid4()}",
        auto_offset_reset="latest",
        enable_auto_commit=False,
        consumer_timeout_ms=500,
    )
    assignment_deadline = time.monotonic() + 15
    while not consumer.assignment() and time.monotonic() < assignment_deadline:
        consumer.poll(timeout_ms=250)
    if not consumer.assignment():
        consumer.close()
        raise TimeoutError("Kafka assignments were not established")
    consumer.seek_to_end(*consumer.assignment())

    signals: dict[int, FeatureRecord] = {}
    inferences: dict[int, InferenceResult] = {}
    telemetry: list[StatusRecord] = []
    parse_errors: list[str] = []
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            for topic_partition, records in consumer.poll(
                timeout_ms=500, max_records=100
            ).items():
                for record in records:
                    try:
                        if topic_partition.topic == topics.KAFKA_FEATURE_STREAM:
                            feature = FeatureRecord.model_validate_json(record.value)
                            if (
                                feature.tenant_id == tenant_id
                                and feature.device_id == device_id
                                and feature.payload.kind == "signal"
                            ):
                                signals[feature.payload.seq] = feature
                        elif topic_partition.topic == topics.KAFKA_INFERENCE_RESULT:
                            result = InferenceResult.model_validate_json(record.value)
                            if result.tenant_id == tenant_id and result.device_id == device_id:
                                inferences[result.seq] = result
                        elif topic_partition.topic == topics.KAFKA_TELEMETRY:
                            status = StatusRecord.model_validate_json(record.value)
                            if status.tenant_id == tenant_id and status.device_id == device_id:
                                telemetry.append(status)
                    except Exception as exc:  # retain evidence instead of hiding a bad record
                        parse_errors.append(
                            f"{topic_partition.topic}:{record.partition}:{record.offset}: {exc}"
                        )
            matches = sorted(set(signals) & set(inferences))
            if len(matches) >= args.minimum_matches and telemetry:
                break
    finally:
        consumer.close()

    matches = sorted(set(signals) & set(inferences))
    matched_signals = [signals[seq].payload for seq in matches]
    matched_results = [inferences[seq] for seq in matches]
    shape_ok = bool(matched_signals) and all(
        item.amplitude_rows == 960
        and item.amplitude_cols == 30
        and abs(item.fs_hz - 320.0) <= 0.25
        for item in matched_signals
    )
    # Decode one payload with the standard library so this observer stays runnable
    # in the lean API image (which intentionally does not ship NumPy).
    decoded_shape = None
    if matched_signals:
        sample = matched_signals[0]
        try:
            raw_amplitude = base64.b64decode(sample.amplitude_b64, validate=True)
            if len(raw_amplitude) == sample.amplitude_rows * sample.amplitude_cols * 4:
                decoded_shape = [sample.amplitude_rows, sample.amplitude_cols]
        except (ValueError, TypeError):
            decoded_shape = None

    bridge_to_model_ms = [
        max(0.0, (result.inferred_at - signals[result.seq].received_at).total_seconds() * 1000)
        for result in matched_results
    ]
    feature_ms = [item.feature_ms for item in matched_results if item.feature_ms is not None]
    infer_ms = [item.infer_ms for item in matched_results if item.infer_ms is not None]

    devices = _request(args.api, "/api/v1/devices", token=token)
    device = next(
        (item for item in devices if item.get("id") == str(device_id)), None
    )
    falls = _request(args.api, "/api/v1/falls?limit=50", token=token)
    device_falls = [
        item for item in falls.get("items", []) if item.get("deviceId") == str(device_id)
    ]
    health_after = _request(args.api, "/health")

    checks = {
        "matched_sequences_at_least_minimum": len(matches) >= args.minimum_matches,
        "real_amplitude_contract_960x30_at_320hz": shape_ok
        and decoded_shape == [960, 30],
        "model_version_present": bool(matched_results)
        and all(bool(item.model_version) for item in matched_results),
        "telemetry_observed": bool(telemetry),
        "device_visible_and_online": bool(device) and device.get("online") is True,
        "record_parse_errors_zero": not parse_errors,
        "api_healthy": health_after.get("status") == "ok",
    }
    passed = all(checks.values())
    report = {
        "schema": "wifiguard-model-physical-e2e-v1",
        "status": "PHYSICAL_MODEL_E2E_PASS" if passed else "PHYSICAL_MODEL_E2E_FAIL",
        "physical_hardware_observed": True,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "tenant_id": tenant_id,
        "device_id": str(device_id),
        "checks": checks,
        "kafka": {
            "signals": len(signals),
            "inferences": len(inferences),
            "matched_sequences": matches,
            "telemetry_records": len(telemetry),
            "parse_errors": parse_errors,
        },
        "input_contract": {
            "decoded_amplitude_shape": decoded_shape,
            "fs_hz_values": sorted({item.fs_hz for item in matched_signals}),
            "input_frames_values": sorted({item.input_frames for item in matched_signals}),
            "input_subcarriers_values": sorted(
                {item.input_subcarriers for item in matched_signals}
            ),
        },
        "model": {
            "versions": sorted(
                {item.model_version for item in matched_results if item.model_version}
            ),
            "probabilities": [item.proba_fall for item in matched_results],
            "decisions": [item.decision for item in matched_results],
            "thresholds": sorted({item.threshold for item in matched_results}),
            "postprocess": sorted({item.postprocess for item in matched_results}),
            "feature_ms": _percentiles(feature_ms),
            "infer_ms": _percentiles(infer_ms),
            "bridge_to_model_ms": _percentiles(bridge_to_model_ms),
        },
        "api": {
            "initial_health": health_before.get("status"),
            "final_health": health_after.get("status"),
            "device_online": device.get("online") if device else None,
            "actual_model_fall_events": len(device_falls),
            "fall_persistence_note": (
                "Actual model decisions included a persisted fall event."
                if device_falls
                else "No actual fall decision was persisted; this is expected for a non-fall physical run."
            ),
        },
        "scope": {
            "proven": [
                "ESP32 receiver to Raspberry Pi WGSP Batch8 SPI/GPIO",
                "Raspberry Pi feature window to MQTT",
                "MQTT bridge to Kafka feature stream",
                "real checkpoint inference to Kafka result",
                "telemetry ingest and authenticated API device visibility",
            ],
            "not_proven_by_non_fall_run": [
                "real-world fall-detection accuracy",
                "notification delivery after an actual physical fall",
            ],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
