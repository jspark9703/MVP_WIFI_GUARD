# services/provisioning — 미착수

기기 등록과 인증서 발급·회수(F-B15). 계획서 M6 백로그.

지금은 브로커 공용 계정 + **토픽 위조 방어**로 대신한다 — 브리지가 페이로드의
`device_id`/`tenant_id` 를 토픽에서 파싱한 값과 대조해 다르면 버린다
(`services/ingest/src/wifiguard_ingest/mqtt_bridge.py`).

그것으로 막지 못하는 것: 자격증명이 샌 기기가 **자기 토픽으로** 거짓 데이터를 보내는 경우.
per-device 자격증명(`mosquitto-go-auth` 또는 AWS IoT 기기별 인증서)이 필요하다.
