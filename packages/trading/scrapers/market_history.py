"""Fetch daily closes for the org's configured series.

Reads market_series_config to know which symbols to fetch.
On first call for a symbol (empty history), fetches 90 days.
On subsequent calls, fetches the last 5 days (idempotent upsert
catches late corrections without re-fetching the full window).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
_TIMEOUT = 20.0


def _parse_chart(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn a Yahoo chart response into [{trade_date, close}, ...]."""
    try:
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            return []
        r0 = result[0]
        timestamps = r0.get("timestamp") or []
        closes = (((r0.get("indicators") or {}).get("quote") or [{}])[0]
                  .get("close") or [])
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for ts, c in zip(timestamps, closes):
        if c is None:
            continue
        try:
            d = date.fromtimestamp(int(ts))
        except Exception:
            continue
        out.append({"trade_date": d, "close": float(c)})
    return out


async def fetch_daily_closes(
    symbol: str, days: int = 90,
) -> list[dict[str, Any]]:
    """Fetch the last `days` daily closes for `symbol` via yfinance.

    yfinance is synchronous, so the call runs in a worker thread
    via asyncio.to_thread. Returns [] on any failure — never raises.
    """
    import asyncio as _asyncio

    def _sync_fetch() -> list[dict[str, Any]]:
        import yfinance as yf  # type: ignore[import-untyped]
        # period is a yfinance string: "1mo", "3mo", "6mo", "1y".
        if days <= 31:
            period = "1mo"
        elif days <= 93:
            period = "3mo"
        elif days <= 186:
            period = "6mo"
        else:
            period = "1y"
        try:
            df = yf.Ticker(symbol).history(
                period=period, interval="1d", auto_adjust=False,
            )
        except Exception as exc:
            log.warning("market_history.fetch_failed",
                        symbol=symbol, error=str(exc)[:200])
            return []
        if df is None or df.empty:
            return []
        out: list[dict[str, Any]] = []
        for idx, row in df.iterrows():
            try:
                d = idx.date() if hasattr(idx, "date") else idx
                c = float(row["Close"])
            except Exception:
                continue
            out.append({"trade_date": d, "close": c})
        return out

    try:
        rows = await _asyncio.to_thread(_sync_fetch)
    except Exception as exc:
        log.warning("market_history.thread_failed",
                    symbol=symbol, error=str(exc)[:200])
        return []

    cutoff = date.today() - timedelta(days=days)
    return [r for r in rows if r["trade_date"] >= cutoff]


async def upsert_market_history(
    session: AsyncSession,
    *,
    org_id: str,
    symbol: str,
    category: str,
    rows: list[dict[str, Any]],
) -> int:
    """Insert or update daily closes. Idempotent on (org, symbol, date)."""
    if not rows:
        return 0
    written = 0
    for row in rows:
        try:
            await session.execute(sql_text("""
                INSERT INTO market_history
                    (org_id, symbol, category, trade_date, close)
                VALUES (:oid, :sym, :cat, :dt, :close)
                ON CONFLICT (org_id, symbol, trade_date)
                WHERE deleted_at IS NULL
                DO UPDATE SET close = EXCLUDED.close,
                              created_at = now()
            """), {
                "oid": org_id, "sym": symbol, "cat": category,
                "dt": row["trade_date"], "close": row["close"],
            })
            written += 1
        except Exception as exc:
            log.warning("market_history.upsert_failed",
                        symbol=symbol, date=str(row["trade_date"]),
                        error=str(exc)[:200])
    await session.commit()
    return written


async def _history_is_empty(
    session: AsyncSession, *, org_id: str, symbol: str,
) -> bool:
    row = (await session.execute(sql_text("""
        SELECT 1 FROM market_history
        WHERE org_id = :oid AND symbol = :sym AND deleted_at IS NULL
        LIMIT 1
    """), {"oid": org_id, "sym": symbol})).first()
    return row is None


async def _read_config(session: AsyncSession, *, org_id: str) -> list[dict]:
    rows = (await session.execute(sql_text("""
        SELECT symbol, display, category
        FROM market_series_config
        WHERE org_id = :oid AND enabled = true AND deleted_at IS NULL
        ORDER BY symbol
    """), {"oid": org_id})).mappings().all()
    return [dict(r) for r in rows]


_DEFAULT_SERIES = [
    ("^VIX", "VIX",           "vix"),
    ("SPY",  "S&P 500 ETF",   "index"),
    ("MDY",  "Mid-cap ETF",   "index"),
    ("IWM",  "Small-cap ETF", "index"),
    ("TLT",  "Long bonds",    "asset_class"),
    ("GLD",  "Gold",          "asset_class"),
    ("HYG",  "High-yield",    "asset_class"),
    ("UUP",  "Dollar index",  "asset_class"),
]


async def _seed_defaults(session: AsyncSession, *, org_id: str) -> None:
    """Insert the default series if the org has none (new orgs)."""
    for symbol, display, category in _DEFAULT_SERIES:
        try:
            await session.execute(sql_text("""
                INSERT INTO market_series_config
                    (org_id, symbol, display, category)
                VALUES (:oid, :sym, :disp, :cat)
                ON CONFLICT DO NOTHING
            """), {
                "oid": org_id, "sym": symbol,
                "disp": display, "cat": category,
            })
        except Exception:
            pass
    await session.commit()


async def refresh_all_series(
    session: AsyncSession, *, org_id: str,
) -> dict[str, Any]:
    """Fetch daily closes for every enabled series for the org.

    On first run for a symbol, fetches 90 days. On later runs,
    fetches the last 5 days.
    """
    cfg = await _read_config(session, org_id=org_id)
    if not cfg:
        await _seed_defaults(session, org_id=org_id)
        cfg = await _read_config(session, org_id=org_id)

    symbols = 0
    rows_written = 0
    errors: list[str] = []
    for entry in cfg:
        symbol = entry["symbol"]
        category = entry["category"]
        empty = await _history_is_empty(session, org_id=org_id, symbol=symbol)
        days = 90 if empty else 5
        rows = await fetch_daily_closes(symbol, days=days)
        if not rows:
            errors.append(symbol)
            continue
        n = await upsert_market_history(
            session, org_id=org_id, symbol=symbol,
            category=category, rows=rows,
        )
        symbols += 1
        rows_written += n

    return {
        "symbols": symbols,
        "rows_written": rows_written,
        "errors": errors,
    }
