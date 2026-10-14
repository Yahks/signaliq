"""Pytest fixtures for SignalIQ tests.

Uses a separate Postgres instance (port 5433) so tests never touch dev data.

**Two-role setup (mirrors production):**
    - `signaliq_test` (superuser)   → owns tables, runs migrations, truncates
    - `signaliq_app` (limited)      → used by test queries, subject to RLS

Without this split, tests connect as superuser and RLS is bypassed entirely.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# Owner (superuser — migrations, truncate)
DB_OWNER_URL = "postgresql+asyncpg://signaliq_test:signaliq_test@localhost:5433/signaliq_test"
DB_OWNER_URL_SYNC = "postgresql://signaliq_test:signaliq_test@localhost:5433/signaliq_test"

# App (limited role — RLS enforced)
DB_APP_URL = "postgresql+asyncpg://signaliq_app:signaliq_app@localhost:5433/signaliq_test"


_TRUNCATE_TABLES = [
    "chain_updates", "chain_impacts", "chain_scenarios", "chain_watchlist",
    "chain_follows", "notifications", "org_ai_usage",
    "post_evidence", "post_versions", "post_feedback", "generated_posts",
    "chain_links", "news_chains", "news_events",
    "cluster_members", "clusters",
    "feature_requests", "pain_points",
    "topic_memberships", "topics",
    "entity_mentions", "entity_aliases", "entities",
    "ai_enrichments", "embeddings",
    "ai_calls", "prompt_versions", "ai_models",
    "normalized_records", "raw_records_default", "raw_records",
    "ingestion_jobs", "sources",
    "memberships", "users", "orgs",
]


# --------------------------------------------------------------- env setup

@pytest.fixture(scope="session", autouse=True)
def _set_test_env() -> None:
    os.environ["ENV"] = "test"
    os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"

    # The API must run as the LIMITED role (RLS-enforced) — same as production.
    # If we set DATABASE_URL to the owner role, the API bypasses RLS and
    # tests would pass while production leaks.
    os.environ["DATABASE_URL"] = DB_APP_URL

    # Migrations and test cleanup run as the OWNER (RLS bypass for DDL/truncate).
    os.environ["DATABASE_URL_SYNC"] = DB_OWNER_URL_SYNC

    from packages.core import config as config_module
    config_module.get_settings.cache_clear()


# --------------------------------------------------------------- bootstrap

@pytest.fixture(scope="session", autouse=True)
def _bootstrap_test_db(_set_test_env):
    """Prepare the test DB before migrations.

    1. Create `signaliq` role placeholder (migration 0003 needs it)
    2. Create `signaliq_app` role (for RLS-enforced tests)
    """
    import psycopg2
    conn = psycopg2.connect(DB_OWNER_URL_SYNC)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signaliq') THEN
                        CREATE ROLE signaliq NOINHERIT NOLOGIN;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signaliq_app') THEN
                        CREATE ROLE signaliq_app LOGIN PASSWORD 'signaliq_app'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                    END IF;
                END
                $$;
            """)
            cur.execute("GRANT CONNECT ON DATABASE signaliq_test TO signaliq_app")
            cur.execute("GRANT USAGE ON SCHEMA public TO signaliq_app")
    finally:
        conn.close()
    yield


@pytest.fixture(scope="session", autouse=True)
def _migrate_test_db(_bootstrap_test_db):
    """Downgrade then upgrade to head — clean slate for the session."""
    import subprocess
    subprocess.run(["alembic", "downgrade", "base"], check=False, capture_output=True, cwd=".")
    r = subprocess.run(
        ["alembic", "upgrade", "head"],
        check=False, capture_output=True, cwd=".", text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Migration failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")

    # Post-migration grants: ensure signaliq_app can read/write all tables
    # (migration 0003 also grants, but on a fresh DB we need to be sure)
    import psycopg2
    conn = psycopg2.connect(DB_OWNER_URL_SYNC)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("GRANT USAGE ON SCHEMA public TO signaliq_app")
            cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO signaliq_app")
            cur.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO signaliq_app")
    finally:
        conn.close()
    yield


# --------------------------------------------------------------- DB fixtures

@pytest_asyncio.fixture
async def db_owner_engine(_migrate_test_db):
    """Superuser engine — used for setup (creating test orgs/users)."""
    engine = create_async_engine(DB_OWNER_URL, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_engine(_migrate_test_db):
    """Limited-role engine — used by tests. RLS is enforced here."""
    engine = create_async_engine(DB_APP_URL, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_owner_session_factory(db_owner_engine):
    return async_sessionmaker(bind=db_owner_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_owner_session(db_owner_session_factory) -> AsyncIterator[AsyncSession]:
    """Superuser session for setup/teardown (no RLS)."""
    async with db_owner_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def db_session(db_session_factory, db_owner_session_factory) -> AsyncIterator[AsyncSession]:
    """Function-scoped limited-role session. Truncates all tables after."""
    async with db_session_factory() as session:
        yield session

    # Cleanup via owner session (RLS would block truncate as app role)
    async with db_owner_session_factory() as session:
        await session.execute(text("SET session_replication_role = 'replica'"))
        for table in _TRUNCATE_TABLES:
            try:
                await session.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
            except Exception:
                pass
        await session.execute(text("SET session_replication_role = 'origin'"))
        await session.commit()


# --------------------------------------------------------------- domain fixtures
# These use the OWNER session so they can insert without RLS setup.
# Tests then use db_session (limited role) to verify RLS.

@pytest_asyncio.fixture
async def test_org_a(db_owner_session: AsyncSession) -> dict[str, Any]:
    org_id = (await db_owner_session.execute(text(
        "INSERT INTO orgs (slug, name) VALUES ('test-org-a', 'Test Org A') RETURNING id::text"
    ))).scalar_one()
    user_id = (await db_owner_session.execute(text(
        "INSERT INTO users (email, password_hash) VALUES ('owner-a@test.com', 'x') RETURNING id::text"
    ))).scalar_one()
    await db_owner_session.execute(text(
        "INSERT INTO memberships (org_id, user_id, role) VALUES (:o, :u, 'owner')"
    ), {"o": org_id, "u": user_id})
    await db_owner_session.commit()
    return {"org_id": org_id, "user_id": user_id}


@pytest_asyncio.fixture
async def test_org_b(db_owner_session: AsyncSession) -> dict[str, Any]:
    org_id = (await db_owner_session.execute(text(
        "INSERT INTO orgs (slug, name) VALUES ('test-org-b', 'Test Org B') RETURNING id::text"
    ))).scalar_one()
    user_id = (await db_owner_session.execute(text(
        "INSERT INTO users (email, password_hash) VALUES ('owner-b@test.com', 'x') RETURNING id::text"
    ))).scalar_one()
    await db_owner_session.execute(text(
        "INSERT INTO memberships (org_id, user_id, role) VALUES (:o, :u, 'owner')"
    ), {"o": org_id, "u": user_id})
    await db_owner_session.commit()
    return {"org_id": org_id, "user_id": user_id}
