"""Publish contract-valid segmentation SignalMsg values and await results.

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
from wifiguard_contracts.mqtt import SignalMsg, encode_amplitude, encode_signal

from validate_model_checkpoint import representative_amplitude


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", default="127.0.0.1:9092")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--device", required=True, type=UUID)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--count", type=int, default=4)
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
    # Establish the result-topic assignment and its "latest" positions before
    # publishing. Otherwise a fast model can produce all results before the
    # first consumer poll and the validator can incorrectly time out.
    assignment_deadline = time.monotonic() + 10
    while not consumer.assignment() and time.monotonic() < assignment_deadline:
        consumer.poll(timeout_ms=250)
    if not consumer.assignment():
        raise TimeoutError("inference-result consumer assignment was not established")
    for partition in consumer.assignment():
        consumer.position(partition)

    first_seq = int(time.time_ns() % 2_000_000_000)
    now = datetime.now(UTC)
    amplitude = representative_amplitude()
    signal = amplitude.mean(axis=1).astype("float32")
    expected = set(range(first_seq, first_seq + args.count))
    for seq in sorted(expected):
        message = SignalMsg(
            device_id=args.device,
            tenant_id=args.tenant,
            ts=now,
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
    matched: dict[int, InferenceResult] = {}
    try:
        while time.monotonic() < deadline:
            for item in consumer:
                result = InferenceResult.model_validate_json(item.value)
                if result.device_id == args.device and result.seq in expected:
                    matched[result.seq] = result
                if set(matched) == expected:
                    ordered = [matched[seq] for seq in sorted(matched)]
                    print(json.dumps({
                        "status": "LIVE_MODEL_KAFKA_PASS",
                        "device_id": str(args.device),
                        "sequences": [result.seq for result in ordered],
                        "probabilities": [result.proba_fall for result in ordered],
                        "threshold": ordered[0].threshold,
                        "model_version": ordered[0].model_version,
                        "mean_feature_ms": sum(result.feature_ms or 0 for result in ordered) / len(ordered),
                        "mean_infer_ms": sum(result.infer_ms or 0 for result in ordered) / len(ordered),
                    }, ensure_ascii=False, indent=2))
                    return
        raise TimeoutError("matching csi-inference-result was not received")
    finally:
        consumer.close()
        producer.close()


if __name__ == "__main__":
    main()
