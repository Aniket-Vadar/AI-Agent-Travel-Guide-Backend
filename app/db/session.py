"""Async engine, session factory and schema bootstrap."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Base

log = get_logger("db")

_engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    # SQLite's async driver does not support the default queue pool sizing args.
    **({} if settings.database_url.startswith("sqlite") else {"pool_size": 10, "max_overflow": 20}),
)

SessionFactory = async_sessionmaker(
    bind=_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


def get_engine():
    return _engine


async def init_db() -> None:
    """Create the SQLite file and tables on first boot.

    Alembic owns migrations for a real deployment; `create_all` keeps the demo and
    the test suite a single command away from running.
    """
    path = settings.sqlite_path
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("database_ready", url=settings.database_url.split("///")[-1])


async def dispose_db() -> None:
    await _engine.dispose()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one transaction-scoped session per request."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping() -> bool:
    from sqlalchemy import text

    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.warning("database_ping_failed", error=str(exc))
        return False
