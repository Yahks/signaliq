"""Maintenance tasks: trash purge, future housekeeping.

The trash purge deletes rows that have been soft-deleted longer than
TRASH_TTL_DAYS. It runs with the owner session (signaliq role) which
bypasses RLS, so one DELETE per table covers every org.

The query still includes `deleted_at IS NOT NULL` as belt-and-braces:
even with the bypass, the only rows the WHERE clause can match are
rows that are already in the trash and older than the TTL. A visible
row can never be removed by this task.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.worker.celery_app import celery_app
from packages.core.logging import get_logger
from packages.db.session import get_owner_session_factory
from apps.worker._async import run as _run

log = get_logger(__name__)

TRASH_TTL_DAYS = 2
TRASH_TABLES = ("trading_posts", "generated_posts", "daily_briefs")


async def _purge_trash_async() -> dict:
    factory = get_owner_session_factory()
    counts: dict[str, int] = {}

    async with factory() as session:
        for table in TRASH_TABLES:
            result = await session.execute(
                sql_text(f"""
                    DELETE FROM {table}
                    WHERE deleted_at IS NOT NULL
                      AND deleted_at < now() - make_interval(days => :ttl)
                """),
                {"ttl": TRASH_TTL_DAYS},
            )
            counts[table] = result.rowcount or 0
        await session.commit()

    log.info("maintenance.trash_purged", **counts)
    return counts


@celery_app.task(name="tasks.purge_trash")
def purge_trash() -> dict:
    """Daily — permanently delete rows soft-deleted more than 2 days ago.

    Idempotent: running it twice in a row purges zero the second time.
    """
    return _run(_purge_trash_async())
