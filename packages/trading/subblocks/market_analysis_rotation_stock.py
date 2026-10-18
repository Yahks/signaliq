"""Stock + timeframe rotation for the market_analysis subblock.

Each run of market_analysis now picks one stock and one timeframe
to write about. The chart follows the pair. This module holds the
picker and the availability check.

Pattern matches market_analysis_rotation.py: random draws that
avoid the last N pairs already used. No rigid index formula.

The stock must have history in market_history. If it doesn't,
the picker fetches it once, then proceeds.

A stock may not support every timeframe (a young IPO has no
meaningful yearly chart). The picker checks available_timeframes
and only picks what the stock actually has.
"""
from __future__ import annotations

import random

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.trading.scrapers.market_history import (
    available_timeframes,
    fetch_and_store_timeframes,
)

log = get_logger(__name__)

# Every timeframe the rotation can pick.
TIMEFRAMES = ["1y", "1mo", "1wk", "1d", "1h"]

# Finding types, rotated alongside stock and timeframe. Each
# article anchors its finding to one section of the report.
# The cycle is 6 articles.
FINDING_TYPES = [
    "correlation",
    "percentile",
    "setup_history",
    "distribution",
    "multi_timeframe",
    "mechanism",
]

_SECTION_FOR_FINDING = {
    "correlation":    "VS THE MARKET",
    "percentile":     "WHERE IT SITS",
    "setup_history":  "SETUP HISTORY",
    "distribution":   "DISTRIBUTION",
    "multi_timeframe": "ACROSS TIMEFRAMES",
    "mechanism":      "MECHANISM",
}

# How many recent picks to avoid.
_RECENT_LOOKBACK = 20

# Bounded attempts at avoiding a recent pick.
_MAX_ATTEMPTS = 30


async def _stocks_in_config(
    session: AsyncSession, *, org_id: str,
) -> list[str]:
    """Return every stock symbol in market_series_config, alphabetically."""
    rows = (await session.execute(sql_text("""
        SELECT symbol FROM market_series_config
        WHERE org_id = :oid
          AND category = 'stock'
          AND enabled = true
          AND deleted_at IS NULL
        ORDER BY symbol
    """), {"oid": org_id})).scalars().all()
    return list(rows)


async def _recent_stock_picks(
    session: AsyncSession, *, org_id: str,
) -> set[tuple[str, str]]:
    """Return the last N (symbol, timeframe) pairs from the run log.

    Ignores NULL rows (pre-migration runs).
    """
    rows = (await session.execute(sql_text("""
        SELECT symbol, timeframe
        FROM market_analysis_runs
        WHERE org_id = :oid
          AND symbol IS NOT NULL
          AND timeframe IS NOT NULL
        ORDER BY ran_at DESC
        LIMIT :lim
    """), {"oid": org_id, "lim": _RECENT_LOOKBACK})).mappings().all()
    return {(r["symbol"], r["timeframe"]) for r in rows}


async def _ensure_history(
    session: AsyncSession, *, org_id: str, symbol: str,
) -> list[str]:
    """Return the timeframes we have for `symbol`.

    If the stock has no history at all, fetch its timeframes once.
    """
    tfs = await available_timeframes(session, org_id=org_id, symbol=symbol)
    if tfs:
        return tfs
    log.info("market_analysis.stock_first_fetch", symbol=symbol)
    try:
        await fetch_and_store_timeframes(
            session, org_id=org_id, symbol=symbol, category="stock",
        )
    except Exception as exc:
        log.warning("market_analysis.stock_fetch_failed",
                    symbol=symbol, error=str(exc)[:200])
        return []
    return await available_timeframes(
        session, org_id=org_id, symbol=symbol,
    )


async def pick_stock_and_timeframe(
    session: AsyncSession, *,
    org_id: str,
    subblock_id: str | None = None,
    max_attempts: int = _MAX_ATTEMPTS,
) -> tuple[str, str, str, str] | None:
    """Pick a (stock, timeframe, finding_type, section) not recently used.

    The finding_type rotates by article count so consecutive
    articles use different sections of the report as the
    finding's anchor.

    Returns None if the config has no stocks.
    """
    stocks = await _stocks_in_config(session, org_id=org_id)
    if not stocks:
        log.warning("market_analysis_rotation.no_stocks", org_id=org_id[:8])
        return None

    recent = await _recent_stock_picks(session, org_id=org_id)

    # Cached availability per stock, filled lazily.
    avail_cache: dict[str, list[str]] = {}

    last_pick: tuple[str, str] | None = None
    for _ in range(max_attempts):
        stock = random.choice(stocks)
        if stock not in avail_cache:
            avail_cache[stock] = await _ensure_history(
                session, org_id=org_id, symbol=stock,
            )
        available = avail_cache[stock]
        # Restrict to trading-horizon timeframes only. Yearly and
        # monthly series are context, not trade windows.
        available = [tf for tf in available if tf in ("1d", "1wk", "1h")]
        if not available:
            continue
        timeframe = random.choice(available)
        last_pick = (stock, timeframe)
        if last_pick not in recent:
            # Compute finding_type from the article count so
            # consecutive articles cycle through the six types.
            article_count = 0
            if subblock_id:
                _cnt = (await session.execute(sql_text("""
                    SELECT COUNT(*) FROM trading_posts
                    WHERE subblock_id = :sid AND org_id = :oid
                      AND deleted_at IS NULL
                """), {"sid": subblock_id, "oid": org_id})).scalar()
                article_count = int(_cnt or 0)
            finding_type = FINDING_TYPES[article_count % len(FINDING_TYPES)]
            section = _SECTION_FOR_FINDING[finding_type]
            log.info(
                "market_analysis_rotation.picked",
                symbol=stock, timeframe=timeframe,
                finding_type=finding_type, section=section,
            )
            return (stock, timeframe, finding_type, section)

    # Fallback — every attempt hit the recent set. Return the last
    # draw so the pipeline never stalls.
    if last_pick:
        # Fallback: same as the success path, but log the warning.
        _stock, _tf = last_pick
        article_count = 0
        if subblock_id:
            _cnt = (await session.execute(sql_text("""
                SELECT COUNT(*) FROM trading_posts
                WHERE subblock_id = :sid AND org_id = :oid
                  AND deleted_at IS NULL
            """), {"sid": subblock_id, "oid": org_id})).scalar()
            article_count = int(_cnt or 0)
        finding_type = FINDING_TYPES[article_count % len(FINDING_TYPES)]
        section = _SECTION_FOR_FINDING[finding_type]
        log.warning(
            "market_analysis_rotation.exhausted_recent",
            fallback=(_stock, _tf, finding_type, section),
        )
        return (_stock, _tf, finding_type, section)

    # No stock had any history. Should not happen — the ensure
    # path fetches on first use. But return None rather than raise.
    log.warning("market_analysis_rotation.no_available")
    return None
