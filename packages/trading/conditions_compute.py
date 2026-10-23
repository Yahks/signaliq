"""Compute market-condition readings from stored price history.

The `market_conditions` table holds five numeric readings, but
nothing in the codebase writes them — they are entered by the
trader. This module computes the four that can be derived from
`market_history` (daily OHLC, decades deep) and `trade_log`:

    realized_vol_current   10-day std of daily log returns, median
                           across the traded symbols, in %/day
    range_current          mean (high-low)/close over 10 days,
                           median across symbols, in %
    correlation_current    mean pairwise Pearson of 10-day daily
                           returns across the symbols held on the
                           as-of date
    breadth_current        the count of trade_log rows dated as-of
                           (the scan's candidate count)

    spread_current         NO SOURCE — no quote/spread data exists.
                           Returned as None; the skeleton marks it
                           unavailable.

Every value carries a `metric_source` string naming the window,
the symbol set, and the as-of date.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

VOL_WINDOW = 10
RANGE_WINDOW = 10
CORR_WINDOW = 10
MIN_RETURNS = 5      # need at least this many returns to compute a std


async def _daily_closes(
    session: AsyncSession, org_id: str, symbols: list[str],
    as_of: date, lookback_days: int,
) -> dict[str, list[tuple[date, float]]]:
    """Return per-symbol [(date, close)] ordered ascending, for the
    `lookback_days` calendar days ending at as_of."""
    start = as_of - timedelta(days=lookback_days * 2)  # calendar slack
    # market_history carries multiple rows per (symbol, trade_date)
    # even within timeframe='1d' — the same close at bar_time 00:00
    # and 04:00. DISTINCT ON (symbol, trade_date) with bar_time DESC
    # picks exactly one row per day: the latest bar, i.e. the close.
    rows = (await session.execute(sql_text("""
        SELECT DISTINCT ON (symbol, trade_date)
               symbol, trade_date, close
        FROM market_history
        WHERE org_id = :oid AND deleted_at IS NULL
          AND symbol = ANY(:syms)
          AND trade_date <= :as_of AND trade_date >= :start
          AND close IS NOT NULL
          AND timeframe = '1d'
        ORDER BY symbol, trade_date, bar_time DESC
    """), {"oid": org_id, "syms": symbols,
           "as_of": as_of, "start": start})).mappings().all()
    out: dict[str, list[tuple[date, float]]] = {}
    for r in rows:
        out.setdefault(r["symbol"], []).append(
            (r["trade_date"], float(r["close"]))
        )
    # keep only the most recent `lookback_days` entries per symbol
    for s in out:
        out[s] = out[s][-lookback_days:]
    return out


async def _daily_bars(
    session: AsyncSession, org_id: str, symbols: list[str],
    as_of: date, lookback_days: int,
) -> dict[str, list[tuple[date, float, float, float]]]:
    """Return per-symbol [(date, high, low, close)] for range."""
    start = as_of - timedelta(days=lookback_days * 2)
    rows = (await session.execute(sql_text("""
        SELECT DISTINCT ON (symbol, trade_date)
               symbol, trade_date, high, low, close
        FROM market_history
        WHERE org_id = :oid AND deleted_at IS NULL
          AND symbol = ANY(:syms)
          AND trade_date <= :as_of AND trade_date >= :start
          AND close IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL
          AND timeframe = '1d'
        ORDER BY symbol, trade_date, bar_time DESC
    """), {"oid": org_id, "syms": symbols,
           "as_of": as_of, "start": start})).mappings().all()
    out: dict[str, list[tuple[date, float, float, float]]] = {}
    for r in rows:
        out.setdefault(r["symbol"], []).append(
            (r["trade_date"], float(r["high"]),
             float(r["low"]), float(r["close"]))
        )
    for s in out:
        out[s] = out[s][-lookback_days:]
    return out


def _log_returns(series: list[tuple[date, float]]) -> list[float]:
    import math
    out = []
    for i in range(1, len(series)):
        p0 = series[i - 1][1]
        p1 = series[i][1]
        if p0 > 0 and p1 > 0:
            out.append(math.log(p1 / p0))
    return out


async def compute_realized_vol(
    session: AsyncSession, org_id: str, symbols: list[str], as_of: date,
) -> tuple[float | None, str]:
    closes = await _daily_closes(
        session, org_id, symbols, as_of, VOL_WINDOW + 1,
    )
    per_symbol: list[float] = []
    for sym, series in closes.items():
        rets = _log_returns(series)
        if len(rets) < MIN_RETURNS:
            continue
        import statistics
        per_symbol.append(statistics.pstdev(rets) * 100.0)
    if not per_symbol:
        return None, f"no usable price history in {VOL_WINDOW}d window"
    v = median(per_symbol)
    return v, (f"{VOL_WINDOW}-day std of daily log returns, median across "
               f"{len(per_symbol)} symbols, as of {as_of.isoformat()}")


async def compute_range(
    session: AsyncSession, org_id: str, symbols: list[str], as_of: date,
) -> tuple[float | None, str]:
    bars = await _daily_bars(
        session, org_id, symbols, as_of, RANGE_WINDOW,
    )
    per_symbol: list[float] = []
    for sym, series in bars.items():
        vals = [(h - l) / c * 100.0 for _, h, l, c in series if c > 0]
        if len(vals) < MIN_RETURNS:
            continue
        per_symbol.append(sum(vals) / len(vals))
    if not per_symbol:
        return None, f"no usable OHLC in {RANGE_WINDOW}d window"
    v = median(per_symbol)
    return v, (f"mean (high-low)/close over {RANGE_WINDOW} days, median "
               f"across {len(per_symbol)} symbols, as of {as_of.isoformat()}")


async def compute_correlation(
    session: AsyncSession, org_id: str, symbols: list[str], as_of: date,
) -> tuple[float | None, str]:
    import math
    closes = await _daily_closes(
        session, org_id, symbols, as_of, CORR_WINDOW + 1,
    )
    # build date -> return per symbol, then align on common dates
    rets_by_sym: dict[str, dict[date, float]] = {}
    for sym, series in closes.items():
        r = {}
        for i in range(1, len(series)):
            p0, p1 = series[i - 1][1], series[i][1]
            if p0 > 0 and p1 > 0:
                r[series[i][0]] = math.log(p1 / p0)
        if len(r) >= MIN_RETURNS:
            rets_by_sym[sym] = r
    syms = list(rets_by_sym)
    if len(syms) < 2:
        return None, "fewer than 2 symbols with usable returns"
    pair_corrs: list[float] = []
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            a, b = rets_by_sym[syms[i]], rets_by_sym[syms[j]]
            common = sorted(set(a) & set(b))
            if len(common) < MIN_RETURNS:
                continue
            xs = [a[d] for d in common]
            ys = [b[d] for d in common]
            n = len(xs)
            mx = sum(xs) / n
            my = sum(ys) / n
            cov = sum((xs[k] - mx) * (ys[k] - my) for k in range(n))
            vx = sum((x - mx) ** 2 for x in xs)
            vy = sum((y - my) ** 2 for y in ys)
            if vx > 0 and vy > 0:
                pair_corrs.append(cov / math.sqrt(vx * vy))
    if not pair_corrs:
        return None, "no usable correlated pairs"
    v = sum(pair_corrs) / len(pair_corrs)
    return v, (f"mean pairwise Pearson of {CORR_WINDOW}-day daily returns "
               f"across {len(syms)} held symbols ({len(pair_corrs)} pairs), "
               f"as of {as_of.isoformat()}")


async def compute_breadth(
    session: AsyncSession, org_id: str, as_of: date,
) -> tuple[int | None, str]:
    # Use the most recent trading day at or before as_of — the
    # snapshot date itself may be a weekend or a day with no scan.
    row = (await session.execute(sql_text("""
        SELECT max(traded_at::date) AS d FROM trade_log
        WHERE org_id = :oid AND deleted_at IS NULL
          AND traded_at::date <= :as_of
    """), {"oid": org_id, "as_of": as_of})).first()
    eff_day = row[0] if row and row[0] else as_of
    row2 = (await session.execute(sql_text("""
        SELECT count(*) AS n FROM trade_log
        WHERE org_id = :oid AND deleted_at IS NULL
          AND traded_at::date = :d
    """), {"oid": org_id, "d": eff_day})).first()
    n = int(row2[0]) if row2 else 0
    return n, (f"trade_log rows dated {eff_day.isoformat()} "
               f"(most recent scan day on or before "
               f"{as_of.isoformat()})")


async def compute_conditions(
    session: AsyncSession, org_id: str, as_of: date | None = None,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Compute the readings that can be derived, and mark the rest
    unavailable. Never invents a value."""
    if as_of is None:
        as_of = date.today()
    if symbols is None:
        rows = (await session.execute(sql_text("""
            SELECT DISTINCT symbol FROM trade_log
            WHERE org_id = :oid AND deleted_at IS NULL AND symbol IS NOT NULL
        """), {"oid": org_id})).all()
        symbols = [r[0] for r in rows]

    vol, vol_src = await compute_realized_vol(session, org_id, symbols, as_of)
    rng, rng_src = await compute_range(session, org_id, symbols, as_of)
    corr, corr_src = await compute_correlation(session, org_id, symbols, as_of)
    breadth, breadth_src = await compute_breadth(session, org_id, as_of)

    return {
        "as_of": as_of,
        "realized_vol_current": vol,
        "range_current": rng,
        "correlation_current": corr,
        "breadth_current": breadth,
        "spread_current": None,
        "metric_source": " | ".join(s for s in (
            f"vol: {vol_src}",
            f"range: {rng_src}",
            f"corr: {corr_src}",
            f"breadth: {breadth_src}",
            "spread: no quote/spread source — unavailable",
        ) if s),
    }
