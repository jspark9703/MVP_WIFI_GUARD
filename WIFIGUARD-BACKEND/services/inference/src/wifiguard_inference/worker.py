"""Kafka ``SignalMsg`` micro-batch consumer and ``InferenceResult`` producer."""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict, defaultdict
from datetime import UTC, datetime
from typing import Any

import numpy as np
from csi_fall_segmentation.postprocess import ABTriggerState
from pydantic import ValidationError
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, InferenceResult

from .features import IncompatibleSignalError, LiveFeatureBuilder
from .settings import InferenceSettings

log = logging.getLogger("wifiguard.inference")
DEDUPE_CAPACITY = 4096


class InferenceWorker:
    def __init__(
        self,
        settings: InferenceSettings | None = None,
        *,
        engine: Any | None = None,
        feature_builder: Any | None = None,
        consumer_factory=None,
        producer_factory=None,
    ) -> None:
        self.settings = settings or InferenceSettings.from_env()
        self.engine = engine
        self.feature_builder = feature_builder or LiveFeatureBuilder()
        self._consumer_factory = consumer_factory
        self._producer_factory = producer_factory
        self._consumer: Any = None
        self._producer: Any = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._seen: OrderedDict[tuple[str, int, str], None] = OrderedDict()
        self._lock = threading.Lock()
        self._counts = {
            "received": 0,
            "ignored": 0,
            "incompatible": 0,
            "published": 0,
            "duplicates": 0,
            "errors": 0,
            "batches": 0,
        }
        self._last_error: str | None = None
        self._prepared = False
        self._trigger_states: dict[str, tuple[ABTriggerState, int, datetime]] = {}

    def prepare(self) -> None:
        """Validate and warm the model without requiring Kafka connectivity."""
        if self._prepared:
            return
        self.settings.validate(require_checkpoint=self.engine is None, require_kafka=False)
        self.feature_builder.start()
        if hasattr(self.feature_builder, "warmup"):
            self.feature_builder.warmup(self.settings.batch_size, 320.0)
        if self.engine is None:
            from csi_fall_segmentation.engine import SegmentationInferenceEngine

            self.engine = SegmentationInferenceEngine(
                self.settings.checkpoint_path,
                device=self.settings.model_device,
            )
        self.engine.warmup()
        self._prepared = True

    def start(self) -> None:
        self.prepare()
        self.settings.validate(require_checkpoint=False, require_kafka=True)
        self._consumer = self._make_consumer()
        self._producer = self._make_producer()
        self._thread = threading.Thread(target=self._run, daemon=True, name="inference-worker")
        self._thread.start()

    def _make_consumer(self):
        if self._consumer_factory is not None:
            return self._consumer_factory()
        from kafka import KafkaConsumer

        return KafkaConsumer(
            topics.KAFKA_FEATURE_STREAM,
            bootstrap_servers=self.settings.kafka_bootstrap.split(","),
            group_id=self.settings.kafka_group,
            auto_offset_reset="latest",
            enable_auto_commit=False,
            consumer_timeout_ms=500,
        )

    def _make_producer(self):
        if self._producer_factory is not None:
            return self._producer_factory()
        from kafka import KafkaProducer

        return KafkaProducer(bootstrap_servers=self.settings.kafka_bootstrap.split(","))

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        for client in (self._consumer, self._producer):
            if client is not None:
                try:
                    client.close()
                except Exception:
                    log.debug("Kafka client close failed", exc_info=True)

    def _run(self) -> None:
        log.info(
            "inference worker started group=%s batch_size=%s wait_ms=%s",
            self.settings.kafka_group,
            self.settings.batch_size,
            self.settings.batch_wait_ms,
        )
        while not self._stop_event.is_set():
            try:
                polled = self._consumer.poll(
                    timeout_ms=self.settings.batch_wait_ms,
                    max_records=self.settings.batch_size,
                )
                messages = [message for records in polled.values() for message in records]
                if messages:
                    self._process_messages(messages)
            except Exception as exc:
                self._record_error(exc)
                log.exception("inference loop failed")
                self._stop_event.wait(1.0)

    def _process_messages(self, messages: list[Any]) -> bool:
        """Retry one polled micro-batch in place, then commit all consumed offsets."""
        while not self._stop_event.is_set():
            values = [(message.topic, message.value) for message in messages]
            if self.handle_batch(values):
                self._consumer.commit()
                return True
            first = messages[0]
            log.warning(
                "inference batch failed; retrying without advancing offsets "
                "topic=%s partition=%s offset=%s records=%s",
                first.topic,
                getattr(first, "partition", "unknown"),
                getattr(first, "offset", "unknown"),
                len(messages),
            )
            if self._stop_event.wait(1.0):
                return False
        return False

    def _process_message(self, message: Any) -> bool:
        """Compatibility wrapper used by focused tests and diagnostics."""
        return self._process_messages([message])

    def handle(self, kafka_topic: str, raw: bytes) -> bool:
        """Process one Kafka value. Safe for direct use in contract tests."""
        return self.handle_batch([(kafka_topic, raw)])

    def handle_batch(self, values: list[tuple[str, bytes]]) -> bool:
        """Validate, group, transform, and infer a bounded Kafka micro-batch."""
        pending: list[tuple[FeatureRecord, Any, tuple[str, int, str], np.ndarray]] = []
        batch_identities: set[tuple[str, int, str]] = set()

        for kafka_topic, raw in values:
            if kafka_topic != topics.KAFKA_FEATURE_STREAM:
                return False
            try:
                record = FeatureRecord.model_validate_json(raw)
            except ValidationError as exc:
                self._record_error(exc)
                log.warning("invalid feature record rejected permanently: %s", exc)
                continue

            with self._lock:
                self._counts["received"] += 1
            if record.payload.kind != "signal":
                with self._lock:
                    self._counts["ignored"] += 1
                continue

            msg = record.payload
            identity = (str(record.device_id), msg.seq, msg.ts.isoformat())
            if identity in self._seen or identity in batch_identities:
                with self._lock:
                    self._counts["duplicates"] += 1
                continue
            try:
                amplitude = msg.decode_amplitude()
            except ValueError as exc:
                with self._lock:
                    self._counts["incompatible"] += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
                log.warning("signal permanently incompatible with segmentation model: %s", exc)
                continue
            batch_identities.add(identity)
            pending.append((record, msg, identity, amplitude))

        if not pending:
            return True

        # Feature extraction requires one fs and tensor shape per call. Most live
        # batches form one group; grouping keeps mixed-device records correct.
        grouped: dict[tuple[float, tuple[int, ...]], list[Any]] = defaultdict(list)
        for item in pending:
            grouped[(float(item[1].fs_hz), item[3].shape)].append(item)

        for (fs_hz, _shape), group in grouped.items():
            try:
                feature_started = time.perf_counter()
                features = self.feature_builder.build_batch([item[3] for item in group], fs_hz)
                feature_ms = (time.perf_counter() - feature_started) * 1000.0 / len(group)
                infer_started = time.perf_counter()
                if hasattr(self.engine, "predict_batch"):
                    probabilities = np.asarray(
                        self.engine.predict_batch(features.s3, features.acf),
                        dtype=np.float32,
                    )
                else:
                    probabilities = np.asarray(
                        [
                            self.engine.predict(secondary, cwt)
                            for secondary, cwt in zip(
                                features.s3, features.acf, strict=True
                            )
                        ],
                        dtype=np.float32,
                    )
                infer_ms = (time.perf_counter() - infer_started) * 1000.0 / len(group)
                if len(probabilities) != len(group):
                    raise RuntimeError("model returned a different batch length")
                for item, probability in zip(group, probabilities, strict=True):
                    self._publish_result(item, float(probability), feature_ms, infer_ms)
            except IncompatibleSignalError as exc:
                with self._lock:
                    self._counts["incompatible"] += len(group)
                    self._last_error = f"{type(exc).__name__}: {exc}"
                log.warning("signal batch permanently incompatible with segmentation model: %s", exc)
                continue
            except Exception as exc:
                self._record_error(exc)
                log.exception("inference batch processing failed")
                return False

        with self._lock:
            self._counts["batches"] += 1
            self._last_error = None
        return True

    def _publish_result(
        self,
        item: tuple[FeatureRecord, Any, tuple[str, int, str], np.ndarray],
        probability: float,
        feature_ms: float,
        infer_ms: float,
    ) -> None:
        record, msg, identity, _amplitude = item
        threshold = (
            self.settings.threshold_override
            if self.settings.threshold_override is not None
            else float(self.engine.threshold)
        )
        decision: bool | None = None
        postprocess = "none"
        if self.settings.postprocess != "none":
            key = str(record.device_id)
            state_entry = self._trigger_states.get(key)
            if state_entry is None:
                trigger_state = ABTriggerState()
            else:
                trigger_state, previous_seq, previous_ts = state_entry
                elapsed = (msg.ts - previous_ts).total_seconds()
                if msg.seq <= previous_seq or elapsed < 0 or elapsed > 2.0:
                    trigger_state.reset()
            trigger_values = trigger_state.update(probability)
            self._trigger_states[key] = (trigger_state, msg.seq, msg.ts)
            postprocess = f"segmentation_{self.settings.postprocess}"
            decision = bool(trigger_values[f"{self.settings.postprocess}_trigger"])
        result = InferenceResult(
            tenant_id=record.tenant_id,
            device_id=record.device_id,
            ts=msg.ts,
            seq=msg.seq,
            proba_fall=probability,
            threshold=threshold,
            postprocess=postprocess,
            decision=decision,
            inferred_at=datetime.now(UTC),
            feature_ms=feature_ms,
            infer_ms=infer_ms,
            model_version=getattr(self.engine, "model_version", None),
            scale_cache_hit=None,
        )
        future = self._producer.send(
            topics.KAFKA_INFERENCE_RESULT,
            key=str(record.device_id).encode(),
            value=result.model_dump_json().encode(),
        )
        if hasattr(future, "get"):
            future.get(timeout=10)
        self._seen[identity] = None
        self._seen.move_to_end(identity)
        while len(self._seen) > DEDUPE_CAPACITY:
            self._seen.popitem(last=False)
        with self._lock:
            self._counts["published"] += 1

    def _record_error(self, exc: Exception) -> None:
        with self._lock:
            self._counts["errors"] += 1
            self._last_error = f"{type(exc).__name__}: {exc}"

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counts": dict(self._counts),
                "last_error": self._last_error,
                "running": bool(self._thread and self._thread.is_alive()),
                "model_version": getattr(self.engine, "model_version", None),
            }
