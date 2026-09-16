"""Kafka ``SignalMsg`` consumer and ``InferenceResult`` producer."""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, InferenceResult

from .features import LiveFeatureBuilder
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
        self._counts = {"received": 0, "ignored": 0, "published": 0, "duplicates": 0, "errors": 0}
        self._last_error: str | None = None
        self._prepared = False

    def prepare(self) -> None:
        """Validate and warm the model without requiring Kafka connectivity."""
        if self._prepared:
            return
        self.settings.validate(require_checkpoint=self.engine is None, require_kafka=False)
        self.feature_builder.start()
        if self.engine is None:
            from wifiguard_serving.engine import FallInferenceEngine

            self.engine = FallInferenceEngine(
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
        log.info("inference worker started group=%s", self.settings.kafka_group)
        while not self._stop_event.is_set():
            try:
                for message in self._consumer:
                    if self._stop_event.is_set():
                        break
                    if not self._process_message(message):
                        break
            except StopIteration:
                continue
            except Exception as exc:
                self._record_error(exc)
                log.exception("inference loop failed")
                self._stop_event.wait(1.0)

    def _process_message(self, message: Any) -> bool:
        """Process and commit exactly one record, retrying transient failures in place.

        The consumer uses manual commits.  Moving on after ``handle`` returns false
        would let a later commit advance past the failed record and silently lose a
        signal window.  Keep this partition position pinned until publication
        succeeds or shutdown is requested.
        """
        while not self._stop_event.is_set():
            if self.handle(message.topic, message.value):
                self._consumer.commit()
                return True
            log.warning(
                "inference record failed; retrying without advancing offset "
                "topic=%s partition=%s offset=%s",
                message.topic,
                getattr(message, "partition", "unknown"),
                getattr(message, "offset", "unknown"),
            )
            if self._stop_event.wait(1.0):
                return False
        return False

    def handle(self, kafka_topic: str, raw: bytes) -> bool:
        """Process one Kafka value. Safe for direct use in contract tests."""
        if kafka_topic != topics.KAFKA_FEATURE_STREAM:
            return False
        try:
            record = FeatureRecord.model_validate_json(raw)
        except ValidationError as exc:
            self._record_error(exc)
            # A malformed record cannot become valid on retry. Record the reject
            # and acknowledge it so one poison record cannot block the partition.
            log.warning("invalid feature record rejected permanently: %s", exc)
            return True

        with self._lock:
            self._counts["received"] += 1
        if record.payload.kind != "signal":
            with self._lock:
                self._counts["ignored"] += 1
            return True

        msg = record.payload
        identity = (str(record.device_id), msg.seq, msg.ts.isoformat())
        if identity in self._seen:
            with self._lock:
                self._counts["duplicates"] += 1
            return True

        try:
            feature_started = time.perf_counter()
            features = self.feature_builder.build(msg.decode(), msg.fs_hz)
            feature_ms = (time.perf_counter() - feature_started) * 1000.0
            infer_started = time.perf_counter()
            probability = float(self.engine.predict(features.s3, features.acf))
            infer_ms = (time.perf_counter() - infer_started) * 1000.0
            threshold = (
                self.settings.threshold_override
                if self.settings.threshold_override is not None
                else float(self.engine.threshold)
            )
            result = InferenceResult(
                tenant_id=record.tenant_id,
                device_id=record.device_id,
                ts=msg.ts,
                seq=msg.seq,
                proba_fall=probability,
                threshold=threshold,
                postprocess="none",
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
        except Exception as exc:
            self._record_error(exc)
            return False

        self._seen[identity] = None
        self._seen.move_to_end(identity)
        while len(self._seen) > DEDUPE_CAPACITY:
            self._seen.popitem(last=False)
        with self._lock:
            self._counts["published"] += 1
            self._last_error = None
        return True

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
