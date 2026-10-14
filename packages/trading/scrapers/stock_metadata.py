"""Fetch sector, industry, market cap for symbols in recent movers.

Uses Yahoo's quoteSummary endpoint. Writes to stock_metadata
(reference data, not org-scoped). Skips symbols already fetched
within the last 7 days.
"""
from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

YAHOO_QUOTE = (
    "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
_TIMEOUT = 20.0


def _tier(market_cap: float | None) -> str | None:
    if market_cap is None:
        return None
    if market_cap >= 200_000_000_000:
        return "mega"
    if market_cap >= 10_000_000_000:
        return "large"
    if market_cap >= 2_000_000_000:
        return "mid"
    if market_cap >= 300_000_000:
        return "small"
    return "micro"


async def fetch_stock_metadata(symbol: str) -> dict[str, Any] | None:
    """Return sector/industry/market cap for `symbol` via yfinance.

    yfinance is synchronous, so the call runs in a worker thread
    via asyncio.to_thread. Returns None on any failure — never raises.
    """
    import asyncio as _asyncio

    def _sync_fetch() -> dict[str, Any] | None:
        import yfinance as yf  # type: ignore[import-untyped]
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception as exc:
            log.warning("stock_metadata.fetch_failed",
                        symbol=symbol, error=str(exc)[:200])
            return None
        cap = info.get("marketCap")
        try:
            cap_f = float(cap) if cap else None
        except Exception:
            cap_f = None
        return {
            "symbol": symbol.upper(),
            "name": (
                info.get("longName")
                or info.get("shortName")
                or symbol
            ),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "market_cap": cap_f,
            "market_cap_tier": _tier(cap_f),
        }

    try:
        return await _asyncio.to_thread(_sync_fetch)
    except Exception as exc:
        log.warning("stock_metadata.thread_failed",
                    symbol=symbol, error=str(exc)[:200])
        return None


async def upsert_stock_metadata(
    session: AsyncSession, data: dict[str, Any],
) -> None:
    await session.execute(sql_text("""
        INSERT INTO stock_metadata
            (symbol, name, sector, industry, market_cap, market_cap_tier)
        VALUES (:sym, :name, :sector, :industry, :cap, :tier)
        ON CONFLICT (symbol) DO UPDATE SET
            name = EXCLUDED.name,
            sector = EXCLUDED.sector,
            industry = EXCLUDED.industry,
            market_cap = EXCLUDED.market_cap,
            market_cap_tier = EXCLUDED.market_cap_tier,
            updated_at = now()
    """), {
        "sym": data["symbol"],
        "name": data.get("name"),
        "sector": data.get("sector"),
        "industry": data.get("industry"),
        "cap": data.get("market_cap"),
        "tier": data.get("market_cap_tier"),
    })
    await session.commit()


async def refresh_movers_metadata(
    session: AsyncSession, *, org_id: str, days: int = 7,
) -> dict[str, Any]:
    """Fetch metadata for symbols in recent top_movers."""
    rows = (await session.execute(sql_text("""
        SELECT DISTINCT (m->>'symbol') AS symbol
        FROM market_events,
             jsonb_array_elements(event_record->'top_movers') AS m
        WHERE org_id = :oid
          AND event_at >= now() - make_interval(days => :d)
          AND m->>'symbol' IS NOT NULL
    """), {"oid": org_id, "d": days})).scalars().all()

    # Skip symbols already fetched in the last 7 days.
    known = (await session.execute(sql_text("""
        SELECT symbol FROM stock_metadata
        WHERE updated_at >= now() - interval '7 days'
    """))).scalars().all()
    known_set = {s.upper() for s in known}

    fetched = 0
    errors: list[str] = []
    for raw in rows:
        sym = (raw or "").strip().upper()
        if not sym or sym in known_set:
            continue
        data = await fetch_stock_metadata(sym)
        if not data:
            errors.append(sym)
            continue
        await upsert_stock_metadata(session, data)
        fetched += 1

    return {"fetched": fetched, "errors": errors}
