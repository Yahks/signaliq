"""Scheduled tasks: periodic fetches and maintenance across all orgs.

These are the only tasks that iterate over multiple orgs. They must be
careful: RLS is NOT set, so queries go through the owner role. Each org's
work is done in its own session with SET for tenant context.
"""
from __future__ import annotations

import asyncio

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


def _list_orgs_with_trading_block() -> list[dict]:
    """Return orgs that have a trading block configured.

    For now, all orgs that have any trading chain or trading event get fetched.
    Future: per-org config for enabled/disabled + ticker lists.
    """
    from sqlalchemy import create_engine, text as sql_text
    from packages.core.config import get_settings

    settings = get_settings()
    from packages.db.session import get_sync_engine
    engine = get_sync_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql_text("""
                SELECT DISTINCT o.id::text, o.slug
                FROM orgs o
                WHERE o.deleted_at IS NULL
            """)).all()
            return [{"id": r[0], "slug": r[1]} for r in rows]
    finally:
        engine.dispose()


@celery_app.task(name="tasks.scheduled_stock_fetch", bind=True)
def scheduled_stock_fetch(self, tickers: list[str] | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Run the stock news fetch for every org.

    Auto-builds new chains for unattached events; strong matches auto-attach
    to existing chains (that's the reattach flow inside fetch_stock_news).
    """
    from apps.worker.tasks.fetch_stock_news import fetch_stock_news

    orgs = _list_orgs_with_trading_block()
    log.info("scheduled.fetch_start", orgs=len(orgs))

    queued: list[str] = []
    for org in orgs:
        # Fan out per-org to keep RLS + tenant context clean
        result = fetch_stock_news.delay(
            org["id"],
            tickers,
            True,               # auto_build
            "us_stocks",        # subtopic
            20,                 # max_events per run
        )
        queued.append(result.id)

    return {"orgs": len(orgs), "tasks_queued": len(queued)}


@celery_app.task(name="tasks.recluster_all_orgs", bind=True)
def recluster_all_orgs(self) -> dict:  # type: ignore[no-untyped-def]
    """Nightly recluster of all orgs."""
    from apps.worker.tasks.cluster import recluster_org

    orgs = _list_orgs_with_trading_block()
    log.info("scheduled.recluster_start", orgs=len(orgs))
    for org in orgs:
        recluster_org.delay(org["id"])
    return {"orgs": len(orgs)}
