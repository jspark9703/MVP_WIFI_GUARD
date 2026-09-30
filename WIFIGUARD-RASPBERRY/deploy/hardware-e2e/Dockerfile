FROM wifi-guard-streaming-toy/edge-agent-rpi3:2.10.2-batch8-auto-resync

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/wifiguard-hardware-e2e/raspberry/src:/opt/wifiguard-hardware-e2e/contracts/src
WORKDIR /opt/wifiguard-hardware-e2e

COPY WIFIGUARD-BACKEND/packages/contracts /opt/wifiguard-hardware-e2e/contracts
COPY WIFIGUARD-RASPBERRY /opt/wifiguard-hardware-e2e/raspberry

ENTRYPOINT ["python", "-m", "wifiguard_edge"]
CMD ["--config-dir", "/opt/wifiguard-hardware-e2e/raspberry/config", "--log-level", "INFO"]
