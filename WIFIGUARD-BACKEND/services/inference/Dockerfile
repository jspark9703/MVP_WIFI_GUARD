FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 SSQ_PARALLEL=0 \
    NUMBA_CACHE_DIR=/tmp/wifiguard-numba-cache
WORKDIR /app

COPY WIFIGUARD-BACKEND/packages/contracts /app/backend/packages/contracts
COPY WIFIGUARD-BACKEND/services/csi-fall-segmentation /app/backend/services/csi-fall-segmentation
COPY WIFIGUARD-BACKEND/services/inference /app/backend/services/inference
COPY WIFIGUARD-RASPBERRY /app/raspberry

RUN python -m pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    torch==2.9.1 torchvision==0.24.1 && \
    python -m pip install --no-cache-dir \
    /app/backend/packages/contracts \
    /app/backend/services/csi-fall-segmentation \
    '/app/raspberry[cwt]' \
    /app/backend/services/inference

RUN mkdir -p /tmp/wifiguard-numba-cache && chown 65532:65532 /tmp/wifiguard-numba-cache

USER 65532:65532
CMD ["python", "-m", "wifiguard_inference"]
