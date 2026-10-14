"""Periodic task: refresh quotes for all symbols referenced in any org's watchlist.

Keeps the cache warm so the UI shows live data instantly.
Rate-limited internally (1.5s between symbols).
"""
from __future__ import annotations

import asyncio

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)


def _list_watchlist_symbols() -> list[str]:
    """Return distinct symbols across all org_watchlist rows."""
    from sqlalchemy import create_engine, text as sql_text
    from packages.core.config import get_settings

    settings = get_settings()
    from packages.db.session import get_sync_engine
    engine = get_sync_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql_text("""
                SELECT DISTINCT symbol
                FROM chain_watchlist
                WHERE symbol IS NOT NULL AND symbol != ''
            """)).all()
            return [r[0] for r in rows if r[0]]
    finally:
        engine.dispose()


async def _warm_async(symbols: list[str]) -> dict:
    from packages.ingestion.sources.market_cache import get_quote_cached

    refreshed = 0
    failed = 0
    for sym in symbols:
        try:
            q = await get_quote_cached(sym, force_refresh=True)
            if q is not None:
                refreshed += 1
            else:
                failed += 1
        except Exception as exc:
            log.warning("market_warm.fetch_failed", symbol=sym, error=str(exc)[:200])
            failed += 1
    return {"symbols": len(symbols), "refreshed": refreshed, "failed": failed}


@celery_app.task(name="tasks.warm_market_cache", bind=True)
def warm_market_cache(self) -> dict:  # type: ignore[no-untyped-def]
    """Fetch + cache fresh quotes for all watchlist symbols across all orgs."""
    symbols = _list_watchlist_symbols()
    log.info("market_warm.start", symbols=len(symbols))
    if not symbols:
        return {"symbols": 0, "refreshed": 0, "failed": 0}
    result = _run(_warm_async(symbols))
    log.info("market_warm.done", **result)
    return result
