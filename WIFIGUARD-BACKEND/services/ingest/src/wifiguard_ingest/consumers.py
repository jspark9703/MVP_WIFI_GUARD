"""Kafka 컨슈머 — 레코드를 싱크·캐시·허브로 흘려보낸다.

토픽 두 개를 한 컨슈머 스레드가 함께 구독한다. presence 4Hz + telemetry 1Hz 는 한 스레드로
충분하고, 스레드를 나누면 같은 기기의 두 갈래가 서로 다른 순서로 처리될 수 있다.

`csi-inference-result`(낙상)는 **M5 에서** 붙인다. 지금 구독해도 발행자가 없다.

## 한 건이 실패해도 컨슈머는 살아야 한다

레코드 하나가 깨졌다고 스레드가 죽으면 그 기기뿐 아니라 전체 실시간 경로가 멎는다.
건별로 잡아 세고 넘어간다.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from pydantic import ValidationError
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, StatusRecord
from wifiguard_contracts.mqtt import PRESENCE_STATUS_FIELDS

log = logging.getLogger("ingest.consumers")


class IngestConsumer:
    """`csi-feature-stream` + `csi-telemetry` 를 소비한다."""

    def __init__(
        self,
        settings: Any,
        *,
        cache,
        hub,
        presence_sink=None,
        telemetry_sink=None,
        consumer_factory=None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.hub = hub
        self.presence_sink = presence_sink
        self.telemetry_sink = telemetry_sink
        self._consumer_factory = consumer_factory
        self._consumer: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._counts = {"presence": 0, "signal": 0, "telemetry": 0, "ack": 0}
        self._errors = 0
        self._last_error: str | None = None

    # ── 수명 ────────────────────────────────────────────────────────
    def start(self) -> None:
        self._consumer = self._make_consumer()
        if self._consumer is None:
            log.warning("KAFKA_BOOTSTRAP 미설정 — 컨슈머를 기동하지 않는다")
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="ingest-consumer")
        self._thread.start()

    def _make_consumer(self) -> Any:
        if self._consumer_factory is not None:
            return self._consumer_factory()
        if not self.settings.kafka_enabled:
            return None
        from kafka import KafkaConsumer

        return KafkaConsumer(
            topics.KAFKA_FEATURE_STREAM,
            topics.KAFKA_TELEMETRY,
            bootstrap_servers=self.settings.kafka_bootstrap.split(","),
            group_id=self.settings.kafka_group,
            # 실시간 경로다. 재기동 시 밀린 것을 따라잡기보다 지금 값을 보는 게 맞다.
            auto_offset_reset="latest",
            enable_auto_commit=True,
            consumer_timeout_ms=500,  # stop() 에 반응하려면 주기적으로 루프를 빠져나와야 한다
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._consumer is not None:
            try:
                self._consumer.close()
            except Exception:
                log.debug("kafka consumer 종료 중 예외", exc_info=True)

    def _run(self) -> None:
        log.info("Kafka 컨슈머 기동 group=%s", self.settings.kafka_group)
        while not self._stop.is_set():
            try:
                for message in self._consumer:
                    if self._stop.is_set():
                        break
                    self.handle(message.topic, message.value)
            except StopIteration:  # consumer_timeout_ms
                continue
            except Exception as exc:
                with self._lock:
                    self._errors += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
                log.warning("컨슈머 루프 예외: %s", exc)
                self._stop.wait(1.0)

    # ── 본체 (테스트가 직접 부른다) ─────────────────────────────────
    def handle(self, kafka_topic: str, raw: bytes) -> bool:
        try:
            if kafka_topic == topics.KAFKA_FEATURE_STREAM:
                record = FeatureRecord.model_validate_json(raw)
            elif kafka_topic == topics.KAFKA_TELEMETRY:
                record = StatusRecord.model_validate_json(raw)
            else:
                return False
        except ValidationError as exc:
            with self._lock:
                self._errors += 1
                self._last_error = f"레코드 검증 실패: {exc.error_count()} 건"
            return False

        kind = record.payload.kind
        with self._lock:
            self._counts[kind] = self._counts.get(kind, 0) + 1

        if kind == "presence":
            self._on_presence(record)
        elif kind == "telemetry":
            self._on_telemetry(record)
        # signal 은 모델서버가 소비한다(M5). ack 는 기록만 하고 흘려보낸다.
        return True

    def _on_presence(self, record: FeatureRecord) -> None:
        msg = record.payload
        fields = msg.model_dump(include=set(PRESENCE_STATUS_FIELDS))
        if self.presence_sink is not None:
            self.presence_sink.offer(msg.ts, record.tenant_id, str(record.device_id), fields)
        state = self.cache.apply_presence(record.device_id, record.tenant_id, fields, msg.ts)
        self.hub.publish_from_thread(state)

    def _on_telemetry(self, record: StatusRecord) -> None:
        msg = record.payload
        link = msg.link.model_dump()
        state = self.cache.apply_telemetry(record.device_id, record.tenant_id, link, msg.ts)
        if self.telemetry_sink is not None:
            self.telemetry_sink.apply(record.device_id, msg.ts, online=bool(msg.link.connected))
        self.hub.publish_from_thread(state)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counts": dict(self._counts),
                "errors": self._errors,
                "last_error": self._last_error,
                "running": bool(self._thread and self._thread.is_alive()),
            }
