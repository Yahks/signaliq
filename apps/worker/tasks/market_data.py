"""Celery tasks for market data ingestion.

Two daily tasks:
  - refresh market_history from Yahoo for every enabled series
  - refresh stock_metadata for symbols in recent movers

Both iterate every org and write per-org rows. Runs after US
market close, before the overnight article batch.
"""
from __future__ import annotations

import asyncio
import threading

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

_loop_local = threading.local()


def _get_loop() -> asyncio.AbstractEventLoop:
    loop = getattr(_loop_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loop_local.loop = loop
    return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


async def _all_org_ids() -> list[str]:
    from sqlalchemy import text as sql_text
    from packages.db.session import get_session_factory
    factory = get_session_factory()
    async with factory() as s:
        rows = (await s.execute(sql_text(
            "SELECT id::text FROM orgs WHERE deleted_at IS NULL"
        ))).scalars().all()
        return list(rows)


async def _refresh_history_for_org(org_id: str) -> dict:
    from sqlalchemy import text as sql_text
    from packages.db.session import get_session_factory
    from packages.trading.scrapers.market_history import refresh_all_series

    factory = get_session_factory()
    async with factory() as s:
        await s.execute(sql_text(
            f"SET app.current_org_id = '{org_id}'"
        ))
        await s.commit()
        return await refresh_all_series(s, org_id=org_id)


async def _refresh_metadata_for_org(org_id: str) -> dict:
    from sqlalchemy import text as sql_text
    from packages.db.session import get_session_factory
    from packages.trading.scrapers.stock_metadata import (
        refresh_movers_metadata,
    )

    factory = get_session_factory()
    async with factory() as s:
        await s.execute(sql_text(
            f"SET app.current_org_id = '{org_id}'"
        ))
        await s.commit()
        return await refresh_movers_metadata(s, org_id=org_id)


@celery_app.task(
    name="tasks.trading_refresh_market_history", bind=True,
)
def trading_refresh_market_history(self, org_id: str | None = None) -> dict:
    """Fetch daily closes for every enabled series. Runs daily."""
    if org_id:
        result = _run(_refresh_history_for_org(org_id))
        return {"org_id": org_id, **result}

    org_ids = _run(_all_org_ids())
    summary = {"orgs": 0, "symbols": 0, "rows": 0, "errors": []}
    for oid in org_ids:
        try:
            r = _run(_refresh_history_for_org(oid))
            summary["orgs"] += 1
            summary["symbols"] += r.get("symbols", 0)
            summary["rows"] += r.get("rows_written", 0)
            summary["errors"].extend(r.get("errors", []))
        except Exception as exc:
            log.warning("market_data.history_org_failed",
                        org_id=oid[:8], error=str(exc)[:200])
            summary["errors"].append(f"{oid[:8]}: {exc}")
    return summary


@celery_app.task(
    name="tasks.trading_refresh_stock_metadata", bind=True,
)
def trading_refresh_stock_metadata(self, org_id: str | None = None) -> dict:
    """Fetch metadata for symbols appearing in recent movers."""
    if org_id:
        result = _run(_refresh_metadata_for_org(org_id))
        return {"org_id": org_id, **result}

    org_ids = _run(_all_org_ids())
    summary = {"orgs": 0, "fetched": 0, "errors": []}
    for oid in org_ids:
        try:
            r = _run(_refresh_metadata_for_org(oid))
            summary["orgs"] += 1
            summary["fetched"] += r.get("fetched", 0)
            summary["errors"].extend(r.get("errors", []))
        except Exception as exc:
            log.warning("market_data.metadata_org_failed",
                        org_id=oid[:8], error=str(exc)[:200])
            summary["errors"].append(f"{oid[:8]}: {exc}")
    return summary
