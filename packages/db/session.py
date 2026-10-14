"""Async engine and session factory."""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from packages.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Lazy-initialized async engine (singleton)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            # S.8.49.8 — raised from 10/20. Verify batches plus
            # the outer session can exhaust the pool when several
            # subblocks run in the same process. 20/40 gives
            # headroom while staying well under Postgres default.
            pool_size=20,
            max_overflow=40,
            pool_pre_ping=True,
            echo=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Lazy-initialized session factory (singleton)."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session, commits on success, rolls back on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Shutdown hook: close the connection pool."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


# --- Async owner session (bypasses RLS via the owner role) ---
# Used only for: registration bootstrap, admin operations, migrations.
# NEVER use on user-facing tenant queries — RLS would be bypassed.
_owner_engine: AsyncEngine | None = None
_owner_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_owner_session_factory() -> async_sessionmaker[AsyncSession]:
    """Async session factory bound to the OWNER role (bypasses RLS)."""
    global _owner_engine, _owner_session_factory
    if _owner_session_factory is None:
        settings = get_settings()
        _owner_engine = create_async_engine(
            settings.database_url_sync.replace(
                "postgresql://", "postgresql+asyncpg://"
            ),
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False,
        )
        _owner_session_factory = async_sessionmaker(
            bind=_owner_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _owner_session_factory


# ─────────────────────────────────────────────────────────────────────
# S.9.22 — cached sync engine
# ─────────────────────────────────────────────────────────────────────
# The Celery worker is a long-lived process. Every task that used to
# call create_engine() directly leaked one Postgres connection per
# task run. Over hundreds of tasks, the pool filled up.
#
# This helper returns one cached engine per (url) for the lifetime
# of the process. Callers get the same engine and share its pool.
# No leaks.

_SYNC_ENGINE_CACHE: dict = {}


def get_sync_engine(url: str | None = None):
    """Return a process-cached sync engine. Do NOT dispose it."""
    from sqlalchemy import create_engine
    from packages.core.config import get_settings

    settings = get_settings()
    key = url or settings.database_url_sync
    if key not in _SYNC_ENGINE_CACHE:
        _SYNC_ENGINE_CACHE[key] = create_engine(
            key,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=1800,
            pool_timeout=30,
        )
    return _SYNC_ENGINE_CACHE[key]

