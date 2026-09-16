"""Publish a contract-valid synthetic SignalMsg and wait for its inference result.

This verifies Kafka routing and actual model execution, not model accuracy.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

from kafka import KafkaConsumer, KafkaProducer
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, InferenceResult
from wifiguard_contracts.mqtt import SignalMsg, encode_signal

from validate_model_checkpoint import representative_signal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", default="127.0.0.1:9092")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--device", required=True, type=UUID)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    consumer = KafkaConsumer(
        topics.KAFKA_INFERENCE_RESULT,
        bootstrap_servers=args.bootstrap.split(","),
        group_id=f"wifiguard-e2e-{uuid4()}",
        auto_offset_reset="latest",
        enable_auto_commit=False,
        consumer_timeout_ms=1000,
    )
    producer = KafkaProducer(bootstrap_servers=args.bootstrap.split(","))
    seq = int(time.time_ns() % 2_000_000_000)
    now = datetime.now(UTC)
    signal = representative_signal()
    message = SignalMsg(
        device_id=args.device,
        tenant_id=args.tenant,
        ts=now,
        seq=seq,
        signal_b64=encode_signal(signal),
        signal_len=len(signal),
        fs_hz=166.75,
        window_samples=len(signal),
        window_span_s=3.0,
        selected_subcarrier_count=30,
        selected_stream_count=8,
        selected_pc_indices="0",
        candidate_pc_count=1,
        selected_pc_count=1,
        input_frames=len(signal),
        input_subcarriers=245,
        signal_q=None,
        presence_state="present",
        gate_reason="forced",
    )
    record = FeatureRecord(
        received_at=now,
        source_topic=topics.leaf_topic(args.tenant, args.device, "signal"),
        tenant_id=args.tenant,
        device_id=args.device,
        payload=message,
    )
    producer.send(
        topics.KAFKA_FEATURE_STREAM,
        key=str(args.device).encode(),
        value=record.model_dump_json().encode(),
    ).get(timeout=10)
    producer.flush()

    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            for item in consumer:
                result = InferenceResult.model_validate_json(item.value)
                if result.device_id == args.device and result.seq == seq:
                    print(json.dumps({
                        "status": "LIVE_MODEL_KAFKA_PASS",
                        "device_id": str(result.device_id),
                        "seq": result.seq,
                        "proba_fall": result.proba_fall,
                        "threshold": result.threshold,
                        "model_version": result.model_version,
                        "feature_ms": result.feature_ms,
                        "infer_ms": result.infer_ms,
                    }, ensure_ascii=False, indent=2))
                    return
        raise TimeoutError("matching csi-inference-result was not received")
    finally:
        consumer.close()
        producer.close()


if __name__ == "__main__":
    main()
