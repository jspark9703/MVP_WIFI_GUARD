from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, InferenceResult
from wifiguard_contracts.mqtt import PresenceMsg, SignalMsg, encode_signal

from wifiguard_inference.settings import InferenceSettings
from wifiguard_inference.worker import InferenceWorker


class FakeBuilder:
    def __init__(self) -> None:
        self.started = False
        self.seen: list[tuple[np.ndarray, float]] = []

    def start(self) -> None:
        self.started = True

    def build(self, signal, fs_hz):
        self.seen.append((signal, fs_hz))
        return SimpleNamespace(
            s3=np.zeros((224, 224), np.float32),
            acf=np.zeros((1, 128, 64), np.float32),
        )


class FakeEngine:
    threshold = 0.47
    model_version = "fake:test"

    def warmup(self): ...

    def predict(self, s3, acf):
        assert s3.shape == (224, 224)
        assert acf.shape == (1, 128, 64)
        return 0.875


class FakeFuture:
    def get(self, timeout=None):
        return True


class FakeProducer:
    def __init__(self) -> None:
        self.sent = []

    def send(self, topic, *, key, value):
        self.sent.append((topic, key, value))
        return FakeFuture()


class FakeConsumer:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def settings(**over):
    base = dict(
        kafka_bootstrap="localhost:9092",
        kafka_group="test",
        checkpoint_path=Path("missing.pt"),
        model_device="cpu",
        threshold_override=None,
    )
    return InferenceSettings(**{**base, **over})


def feature_record(message) -> bytes:
    return FeatureRecord(
        received_at=datetime.now(UTC),
        source_topic=f"test/{message.kind}",
        tenant_id=message.tenant_id,
        device_id=message.device_id,
        payload=message,
    ).model_dump_json().encode()


def signal_message():
    signal = np.linspace(-1, 1, 500, dtype=np.float32)
    return SignalMsg(
        device_id=uuid4(),
        tenant_id=f"home-{uuid4()}",
        ts=datetime.now(UTC),
        seq=7,
        signal_b64=encode_signal(signal),
        signal_len=len(signal),
        fs_hz=166.75,
        window_samples=500,
        window_span_s=3.0,
        selected_subcarrier_count=30,
        selected_stream_count=10,
        selected_pc_indices="0;1",
        candidate_pc_count=2,
        selected_pc_count=2,
        input_frames=520,
        input_subcarriers=245,
        presence_state="present",
        gate_reason="present",
    )


def test_signal_is_transformed_inferred_and_published_once():
    producer, builder = FakeProducer(), FakeBuilder()
    worker = InferenceWorker(settings(), engine=FakeEngine(), feature_builder=builder)
    worker._producer = producer
    message = signal_message()
    raw = feature_record(message)

    assert worker.handle(topics.KAFKA_FEATURE_STREAM, raw)
    assert worker.handle(topics.KAFKA_FEATURE_STREAM, raw), "Kafka redelivery is acknowledged"
    assert len(producer.sent) == 1, "but it must not publish duplicate inference"
    topic, key, value = producer.sent[0]
    assert topic == topics.KAFKA_INFERENCE_RESULT
    assert key == str(message.device_id).encode()
    result = InferenceResult.model_validate_json(value)
    assert result.proba_fall == 0.875
    assert result.threshold == 0.47
    assert result.postprocess == "none"
    assert result.model_version == "fake:test"
    assert builder.seen[0][0].shape == (500,)
    assert worker.status()["counts"]["duplicates"] == 1


def test_presence_is_acknowledged_but_not_sent_to_model():
    producer, builder = FakeProducer(), FakeBuilder()
    worker = InferenceWorker(settings(), engine=FakeEngine(), feature_builder=builder)
    worker._producer = producer
    msg = PresenceMsg(
        device_id=uuid4(), tenant_id=f"home-{uuid4()}", ts=datetime.now(UTC), seq=1,
        tick_uptime_s=1, state="present",
    )
    assert worker.handle(topics.KAFKA_FEATURE_STREAM, feature_record(msg))
    assert builder.seen == [] and producer.sent == []


def test_missing_checkpoint_fails_closed():
    with pytest.raises(FileNotFoundError, match="MODEL_CHECKPOINT"):
        settings().validate(require_checkpoint=True)


def test_prepare_warms_model_without_kafka_connection():
    builder = FakeBuilder()
    worker = InferenceWorker(
        settings(kafka_bootstrap=""),
        engine=FakeEngine(),
        feature_builder=builder,
    )
    worker.prepare()
    assert builder.started is True
    assert worker.status()["model_version"] == "fake:test"


def test_start_requires_kafka_after_model_prepare():
    worker = InferenceWorker(
        settings(kafka_bootstrap=""),
        engine=FakeEngine(),
        feature_builder=FakeBuilder(),
    )
    with pytest.raises(ValueError, match="KAFKA_BOOTSTRAP"):
        worker.start()


def test_transient_failure_is_retried_before_offset_commit(monkeypatch):
    worker = InferenceWorker(
        settings(),
        engine=FakeEngine(),
        feature_builder=FakeBuilder(),
    )
    worker._consumer = FakeConsumer()
    outcomes = iter([False, True])
    monkeypatch.setattr(worker, "handle", lambda _topic, _value: next(outcomes))
    monkeypatch.setattr(worker._stop_event, "wait", lambda _timeout: False)
    message = SimpleNamespace(topic=topics.KAFKA_FEATURE_STREAM, value=b"record", partition=0, offset=7)

    assert worker._process_message(message) is True
    assert worker._consumer.commits == 1


def test_malformed_record_is_rejected_without_infinite_retry():
    worker = InferenceWorker(
        settings(),
        engine=FakeEngine(),
        feature_builder=FakeBuilder(),
    )
    worker._producer = FakeProducer()

    assert worker.handle(topics.KAFKA_FEATURE_STREAM, b"not-json") is True
    assert worker.status()["counts"]["errors"] == 1
