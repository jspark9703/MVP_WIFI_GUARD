"""WIFI-GUARD 서비스 API 엔트리포인트.

    uv run uvicorn wifiguard_api.app:app --reload --port 8000     (또는 make api)

- 접두 없음 : /, /health, /ports  (routers/health.py — 드라이런·프론트 useBackendUp 호환)
- /api/v1   : auth · facilities · devices · residents · falls · event-logs · recipients · config · account
- main.py(현행 로컬 백엔드 계약 원본)는 마운트하지 않는다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .errors import install_error_handlers
from .routers import auth, health

log = logging.getLogger("wifiguard_api")

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    app = FastAPI(
        title="WIFI-GUARD API",
        version="0.1.0",
        description="HOME/FACILITY 서비스 API — 계정·시설·기기·거주자·낙상 이력·이벤트 로그·수신자·설정 (실시간·추론 경로는 후속 단계)",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=settings.cors_origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    if settings.jwt_secret_is_default:
        log.warning("JWT_SECRET 이 기본값(changeme)입니다 — 개발 환경 외에서는 반드시 교체하세요.")

    app.include_router(health.router)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(auth.router)
    for name in ("facilities", "devices", "residents", "falls", "event_logs", "recipients", "config", "account"):
        try:
            module = __import__(f"wifiguard_api.routers.{name}", fromlist=["router"])
        except ModuleNotFoundError:
            continue  # M3 에서 추가되는 라우터 — 없으면 건너뛴다
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
