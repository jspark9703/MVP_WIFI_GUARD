"""동기 SQLAlchemy 엔진/세션 (psycopg3).

이번 단계는 CRUD 만 있으므로 동기 엔진을 쓴다. FastAPI 는 sync 의존성을 스레드풀에서 실행한다.
실시간 경로(WS 팬아웃)를 붙일 때 이 파일과 `wifiguard_api.deps.get_db` 만 async 로 교체하면 된다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, echo=settings.echo_sql)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def get_session() -> Iterator[Session]:
    """`with get_session() as db:` — 예외 시 롤백, 정상 종료 시 커밋."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
