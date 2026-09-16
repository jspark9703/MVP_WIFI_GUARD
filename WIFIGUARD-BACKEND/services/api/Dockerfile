FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY packages/contracts /app/packages/contracts
COPY packages/db /app/packages/db
COPY services/ingest /app/services/ingest
COPY services/notification /app/services/notification
COPY services/api /app/services/api

RUN python -m pip install --no-cache-dir \
    /app/packages/contracts \
    /app/packages/db \
    /app/services/notification \
    /app/services/ingest \
    /app/services/api

EXPOSE 8000
CMD ["uvicorn", "wifiguard_api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
