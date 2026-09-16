FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 SSQ_PARALLEL=0
WORKDIR /app

COPY WIFIGUARD-BACKEND/packages/contracts /app/backend/packages/contracts
COPY WIFIGUARD-BACKEND/services/csi-fall-pipeline /app/backend/services/csi-fall-pipeline
COPY WIFIGUARD-BACKEND/services/model-serving /app/backend/services/model-serving
COPY WIFIGUARD-BACKEND/services/inference /app/backend/services/inference
COPY WIFIGUARD-RASPBERRY /app/raspberry

RUN python -m pip install --no-cache-dir \
    /app/backend/packages/contracts \
    '/app/raspberry[cwt]' \
    /app/backend/services/csi-fall-pipeline \
    /app/backend/services/model-serving \
    /app/backend/services/inference

USER 65532:65532
CMD ["python", "-m", "wifiguard_inference"]
