# services/mqtt-bridge — `services/ingest` 로 통합됨 (2026-09-10)

MQTT→Kafka 브리지는 `services/ingest/src/wifiguard_ingest/mqtt_bridge.py` 에 있다.

별도 서비스로 두지 않은 이유: 브리지는 상태가 없고 컨슈머와 같은 프로세스에 있어도
비용이 없다. 반대로 프로세스를 나누면 현재 EC2 2대 토폴로지에서 메모리만 더 쓴다.

운영에서는 AWS IoT Rule 이 이 역할을 대신하므로 브리지 자체가 사라진다 — 그때
`wifiguard_ingest` 는 Kafka 컨슈머만 남는다.
