"""Fetch daily closes for the org's configured series.

Reads market_series_config to know which symbols to fetch.
On first call for a symbol (empty history), fetches 90 days.
On subsequent calls, fetches the last 5 days (idempotent upsert
catches late corrections without re-fetching the full window).
"""
from __future__ import annotations
import math

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


async def fetch_timeframe_closes(
    symbol: str,
    *,
    interval: str,
    period: str,
) -> list[dict[str, Any]]:
    """Fetch bars for `symbol` at `interval` over `period` via yfinance.

    interval: "1d" | "1wk" | "1mo" | "1h"
    period:   "1y" | "3mo" | "5y" | "max" ...

    yfinance is synchronous, so the call runs in a worker thread
    via asyncio.to_thread. Returns [] on any failure — never raises.
    """
    import asyncio as _asyncio

    def _sync_fetch() -> list[dict[str, Any]]:
        import yfinance as yf  # type: ignore[import-untyped]
        try:
            df = yf.Ticker(symbol).history(
                period=period, interval=interval, auto_adjust=False,
            )
        except Exception as exc:
            log.warning("market_history.fetch_failed",
                        symbol=symbol, interval=interval,
                        error=str(exc)[:200])
            return []
        if df is None or df.empty:
            return []
        out: list[dict[str, Any]] = []
        for idx, row in df.iterrows():
            try:
                # idx is a pandas Timestamp (or a date for daily bars).
                # bar_time is the exact timestamp; trade_date is the
                # date part, kept for backward-compat queries.
                if hasattr(idx, "to_pydatetime"):
                    bar_time = idx.to_pydatetime()
                else:
                    from datetime import datetime, timezone
                    _d = idx.date() if hasattr(idx, "date") else idx
                    bar_time = datetime.combine(
                        _d, datetime.min.time(), tzinfo=timezone.utc,
                    )
                trade_date = bar_time.date()
                c = float(row["Close"])
                o = float(row["Open"]) if "Open" in row.index else c
                h = float(row["High"]) if "High" in row.index else c
                lo = float(row["Low"]) if "Low" in row.index else c
            except Exception:
                continue
            out.append({
                "trade_date": trade_date,
                "bar_time": bar_time,
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
            })
        return out

    try:
        return await _asyncio.to_thread(_sync_fetch)
    except Exception as exc:
        log.warning("market_history.thread_failed",
                    symbol=symbol, interval=interval,
                    error=str(exc)[:200])
        return []


async def fetch_daily_closes(
    symbol: str, days: int = 90,
) -> list[dict[str, Any]]:
    """Daily closes for `symbol` over the last `days` days.

    Kept for compatibility. New code should call
    fetch_timeframe_closes directly.
    """
    if days <= 31:
        period = "1mo"
    elif days <= 93:
        period = "3mo"
    elif days <= 186:
        period = "6mo"
    else:
        period = "1y"
    return await fetch_timeframe_closes(
        symbol, interval="1d", period=period,
    )


async def upsert_market_history(
    session: AsyncSession,
    *,
    org_id: str,
    symbol: str,
    category: str,
    timeframe: str = "1d",
    rows: list[dict[str, Any]],
) -> int:
    """Insert or update closes for one (symbol, timeframe).

    Idempotent on (org, symbol, timeframe, trade_date).
    """
    if not rows:
        return 0
    written = 0
    for row in rows:
        try:
            await session.execute(sql_text("""
                INSERT INTO market_history
                    (org_id, symbol, category, timeframe,
                     trade_date, bar_time, open, high, low, close)
                VALUES (:oid, :sym, :cat, :tf,
                        :dt, :bt, :o, :h, :lo, :c)
                ON CONFLICT (org_id, symbol, timeframe, bar_time)
                WHERE deleted_at IS NULL
                DO UPDATE SET
                    trade_date = EXCLUDED.trade_date,
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    created_at = now()
            """), {
                "oid": org_id, "sym": symbol, "cat": category,
                "tf": timeframe,
                "dt": row["trade_date"],
                "bt": row.get("bar_time") or row["trade_date"],
                "o": row.get("open", row["close"]),
                "h": row.get("high", row["close"]),
                "lo": row.get("low", row["close"]),
                "c": row["close"],
            })
            written += 1
        except Exception as exc:
            log.warning("market_history.upsert_failed",
                        symbol=symbol, timeframe=timeframe,
                        date=str(row["trade_date"]),
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
            category=category, timeframe="1d", rows=rows,
        )
        symbols += 1
        rows_written += n

    return {
        "symbols": symbols,
        "rows_written": rows_written,
        "errors": errors,
    }

async def available_timeframes(
    session: AsyncSession, *, org_id: str, symbol: str,
) -> list[str]:
    """Return the timeframes that exist in market_history for a symbol.

    Ordered from longest to shortest: 1y, 1mo, 1wk, 1d, 1h.
    """
    _ORDER = ["1y", "1mo", "1wk", "1d", "1h"]
    rows = (await session.execute(sql_text("""
        SELECT DISTINCT timeframe FROM market_history
        WHERE org_id = :oid AND symbol = :sym AND deleted_at IS NULL
    """), {"oid": org_id, "sym": symbol})).scalars().all()
    have = set(rows)
    return [tf for tf in _ORDER if tf in have]


async def fetch_and_store_timeframes(
    session: AsyncSession,
    *,
    org_id: str,
    symbol: str,
    category: str = "stock",
) -> dict[str, Any]:
    """Fetch and store every timeframe this stock can support.

    Rule (S.9.44):
      - 1mo, 1wk, 1d, 1h are always fetched — every S&P 500 name
        has at least a few months of history.
      - 1y is fetched only when the stock has >= 60 monthly bars
        (5 years). A 2-year-old IPO has no meaningful yearly chart.

    Idempotent. Returns {symbol, timeframes_fetched, rows_written,
    errors}.
    """
    result: dict[str, Any] = {
        "symbol": symbol,
        "timeframes_fetched": [],
        "rows_written": 0,
        "errors": [],
    }

    # ── 1. Fetch monthly first — it's the age check. ──
    monthly_rows = await fetch_timeframe_closes(
        symbol, interval="1mo", period="max",
    )
    if monthly_rows:
        n = await upsert_market_history(
            session, org_id=org_id, symbol=symbol, category=category,
            timeframe="1mo", rows=monthly_rows,
        )
        result["timeframes_fetched"].append("1mo")
        result["rows_written"] += n
    else:
        result["errors"].append("1mo")

    # ── 2. Yearly only if the stock is >= 5 years old. ──
    if len(monthly_rows) >= 60:
        yearly_rows = []
        # Resample monthly to yearly, keeping year-end close.
        by_year: dict[int, float] = {}
        for r in monthly_rows:
            try:
                y = int(str(r["trade_date"])[:4])
                by_year[y] = float(r["close"])
            except Exception:
                continue
        # Drop the current (incomplete) year.
        from datetime import date as _d
        by_year.pop(_d.today().year, None)
        yearly_rows = [
            {"trade_date": _d(y, 12, 31), "close": c}
            for y, c in sorted(by_year.items())
        ]
        if yearly_rows:
            n = await upsert_market_history(
                session, org_id=org_id, symbol=symbol,
                category=category, timeframe="1y", rows=yearly_rows,
            )
            result["timeframes_fetched"].append("1y")
            result["rows_written"] += n

    # ── 3. Weekly, daily, hourly — always. ──
    plan = [
        ("1wk", "1wk", "5y"),
        ("1d",  "1d",  "1y"),
        ("1h",  "1h",  "3mo"),
    ]
    for tf, interval, period in plan:
        rows = await fetch_timeframe_closes(
            symbol, interval=interval, period=period,
        )
        if not rows:
            result["errors"].append(tf)
            continue
        n = await upsert_market_history(
            session, org_id=org_id, symbol=symbol, category=category,
            timeframe=tf, rows=rows,
        )
        result["timeframes_fetched"].append(tf)
        result["rows_written"] += n

    log.info(
        "market_history.timeframes_stored",
        symbol=symbol,
        timeframes=result["timeframes_fetched"],
        rows=result["rows_written"],
    )
    return result

# ─────────────────────────────────────────────────────────────────────
# Stock report — the model's source block for one (symbol, timeframe).
# ─────────────────────────────────────────────────────────────────────

_REPORT_WINDOW = {
    "1y":  (10,  "10-year view"),
    "1mo": (60,  "5-year monthly view"),
    "1wk": (52,  "1-year weekly view"),
    "1d":  (60,  "60-day daily view"),
    "1h":  (100, "recent intraday view"),
}


def _pct_return(closes: list[float]) -> float | None:
    if len(closes) < 2 or closes[0] == 0:
        return None
    return (closes[-1] - closes[0]) / closes[0] * 100.0


def _percentile(values: list[float], v: float) -> int | None:
    if not values:
        return None
    below = sum(1 for x in values if x <= v)
    return int(round(below / len(values) * 100))




def _forward_return_stats(returns: list[float]) -> dict:
    """Compute sample size, win rate, expectancy, stdev, and max drawdown
    for a list of forward returns (in percent).
    """
    if not returns:
        return {}
    n = len(returns)
    wins = [r for r in returns if r > 0]
    win_rate = len(wins) / n * 100.0
    expectancy = sum(returns) / n
    if n > 1:
        mean = expectancy
        var = sum((r - mean) ** 2 for r in returns) / (n - 1)
        std = var ** 0.5
    else:
        std = 0.0
    # Max drawdown across the sequence (in percent of running peak)
    peak = returns[0]
    dd = 0.0
    for r in returns:
        if r > peak:
            peak = r
        dd = min(dd, r - peak)
    # t-statistic: mean / (std / sqrt(n)). The test is whether
    # the mean forward return is significantly different from zero.
    # |t| > 1.96 means p < 0.05 (two-tailed). Below that, the edge
    # is not statistically distinguishable from noise.
    if std > 0 and n >= 2:
        t_stat = expectancy / (std / math.sqrt(n))
    else:
        t_stat = 0.0

    return {
        "n": n,
        "wins": len(wins),
        "losses": n - len(wins),
        "win_rate": win_rate,
        "expectancy": expectancy,
        "std": std,
        "max_dd": dd,
        "t_stat": t_stat,
        "significant": abs(t_stat) > 1.96,
    }

async def build_stock_report(
    session: AsyncSession,
    *,
    org_id: str,
    symbol: str,
    timeframe: str,
) -> str:
    """Produce the labeled text block the model writes from.

    Sections:
      THE STOCK             - symbol, name, sector
      THE <TIMEFRAME> VIEW  - picked timeframe numbers
      ACROSS TIMEFRAMES     - multi-window returns
      RECENT ACTION         - streak, best/worst, MAs
      VS THE MARKET         - stock vs broad index
      DISTRIBUTION          - up/down day histogram
      SETUP HISTORY         - prior percentile-band instances
      SECTOR TODAY          - peer movers
      RECENT CLOSES         - last 8 bars
      MY READINGS           - the stock's own readings
    """
    bars = (await session.execute(sql_text(
        "SELECT trade_date, close FROM market_history "
        "WHERE org_id = :oid AND symbol = :sym "
        "AND timeframe = :tf AND deleted_at IS NULL "
        "ORDER BY trade_date ASC"
    ), {"oid": org_id, "sym": symbol, "tf": timeframe})).mappings().all()

    if len(bars) < 3:
        return ""

    n_bars, window_label = _REPORT_WINDOW.get(timeframe, (60, "recent view"))
    window = bars[-n_bars:]
    dates = [str(b["trade_date"]) for b in window]
    closes = [float(b["close"]) for b in window]

    # Metadata: stock_metadata first, config fallback.
    meta = (await session.execute(sql_text(
        "SELECT name, sector, industry, market_cap_tier "
        "FROM stock_metadata WHERE symbol = :sym"
    ), {"sym": symbol})).mappings().first()
    name = (meta["name"] if meta else None)
    sector = (meta["sector"] if meta else None) or "unknown"
    industry = (meta["industry"] if meta else None) or ""
    if not name:
        cfg = (await session.execute(sql_text(
            "SELECT display FROM market_series_config "
            "WHERE org_id = :oid AND symbol = :sym "
            "AND deleted_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1"
        ), {"oid": org_id, "sym": symbol})).mappings().first()
        name = (cfg["display"] if cfg and cfg["display"] else symbol)

    parts: list[str] = []

    # Section 1 - the stock.
    parts.append("--- THE STOCK ---")
    line = f"{symbol} - {name}"
    if sector and sector != "unknown":
        line += f" - {sector}"
    parts.append(line)

    # Section 2 - the picked timeframe view.
    parts.append(f"\n--- THE {window_label.upper()} (last {len(closes)} bars) ---")
    parts.append(f"Current close: {closes[-1]:.2f}")
    lo = min(closes)
    hi = max(closes)
    parts.append(f"{window_label.split()[0]} high: {hi:.2f}   low: {lo:.2f}")
    ret = _pct_return(closes)
    if ret is not None:
        sign = "+" if ret >= 0 else ""
        parts.append(f"{window_label.split()[0]} return: {sign}{ret:.2f}%")
    pct = _percentile(closes, closes[-1])
    if pct is not None:
        parts.append(f"Percentile of current close: {pct}th (of {len(closes)} bars)")
    parts.append(f"Range: {dates[0]}  ->  {dates[-1]}")

    # Section 3 - across timeframes.
    parts.append("\n--- ACROSS TIMEFRAMES ---")
    tf_rows = (await session.execute(sql_text(
        "SELECT timeframe, trade_date, close FROM market_history "
        "WHERE org_id = :oid AND symbol = :sym AND deleted_at IS NULL "
        "AND timeframe IN ('1d', '1wk', '1mo', '1y') "
        "ORDER BY timeframe, trade_date ASC"
    ), {"oid": org_id, "sym": symbol})).mappings().all()
    tf_series: dict[str, list[float]] = {}
    for r in tf_rows:
        tf_series.setdefault(r["timeframe"], []).append(float(r["close"]))
    _WINDOW = {
        "1d":  (20, "20-day return"),
        "1wk": (12, "12-week return"),
        "1mo": (6,  "6-month return"),
        "1y":  (5,  "5-year return"),
    }
    for tf_key, (n_ret, label) in _WINDOW.items():
        series = tf_series.get(tf_key) or []
        if len(series) >= n_ret + 1 and series[-n_ret - 1]:
            old = series[-n_ret - 1]
            new = series[-1]
            r = (new - old) / old * 100.0
            sign = "+" if r >= 0 else ""
            parts.append(f"{label}:  {sign}{r:.2f}%")
        else:
            parts.append(f"{label}:  (insufficient history)")

    # Section 4 - recent action.
    daily = tf_series.get("1d") or closes
    parts.append("\n--- RECENT ACTION ---")
    if len(daily) >= 2:
        streak = 1
        direction = 1 if daily[-1] >= daily[-2] else -1
        for k in range(len(daily) - 2, 0, -1):
            if (1 if daily[k] >= daily[k - 1] else -1) == direction:
                streak += 1
            else:
                break
        word = "up" if direction > 0 else "down"
        parts.append(f"Streak: {streak} {word} day(s)")
    if len(daily) >= 21:
        window20 = daily[-21:]
        rets = []
        for k in range(1, len(window20)):
            if window20[k - 1]:
                rets.append((window20[k] - window20[k - 1]) / window20[k - 1] * 100.0)
        if rets:
            best = max(rets)
            worst = min(rets)
            parts.append(f"Best of last 20 days: +{best:.2f}%")
            parts.append(f"Worst of last 20 days: {worst:.2f}%")
            mean = sum(rets) / len(rets)
            var = sum((x - mean) ** 2 for x in rets) / max(1, len(rets) - 1)
            vol = var ** 0.5
            parts.append(f"20-day volatility: {vol:.2f}% per day")
    if len(daily) >= 20:
        ma20 = sum(daily[-20:]) / 20.0
        if ma20:
            dist = (daily[-1] - ma20) / ma20 * 100.0
            sign = "+" if dist >= 0 else ""
            parts.append(f"Distance from 20-day average: {sign}{dist:.2f}%")
    if len(daily) >= 50:
        ma50 = sum(daily[-50:]) / 50.0
        if ma50:
            dist = (daily[-1] - ma50) / ma50 * 100.0
            sign = "+" if dist >= 0 else ""
            parts.append(f"Distance from 50-day average: {sign}{dist:.2f}%")

    # Section 5 - vs the market.
    parts.append("\n--- VS THE MARKET ---")
    spy_rows = (await session.execute(sql_text(
        "SELECT trade_date, close FROM market_history "
        "WHERE org_id = :oid AND symbol = 'SPY' "
        "AND timeframe = '1d' AND deleted_at IS NULL "
        "ORDER BY trade_date ASC"
    ), {"oid": org_id})).mappings().all()
    spy_closes = [float(r["close"]) for r in spy_rows]
    if len(spy_closes) >= 21 and len(daily) >= 21:
        spy_ret = (spy_closes[-1] - spy_closes[-21]) / spy_closes[-21] * 100.0 if spy_closes[-21] else 0.0
        stk_ret = (daily[-1] - daily[-21]) / daily[-21] * 100.0 if daily[-21] else 0.0
        div = stk_ret - spy_ret
        parts.append(f"Stock return (20 days): {'+' if stk_ret >= 0 else ''}{stk_ret:.2f}%")
        parts.append(f"Broad index return (20 days):   {'+' if spy_ret >= 0 else ''}{spy_ret:.2f}%")
        parts.append(f"Divergence: {'+' if div >= 0 else ''}{div:.2f} points")
        import math
        n = 20
        sr = [(daily[k] - daily[k - 1]) / daily[k - 1] for k in range(len(daily) - n, len(daily)) if daily[k - 1]]
        pr = [(spy_closes[k] - spy_closes[k - 1]) / spy_closes[k - 1] for k in range(len(spy_closes) - n, len(spy_closes)) if spy_closes[k - 1]]
        if len(sr) == len(pr) and len(sr) > 2:
            ms = sum(sr) / len(sr)
            mp = sum(pr) / len(pr)
            cov = sum((sr[k] - ms) * (pr[k] - mp) for k in range(len(sr)))
            vs = sum((sr[k] - ms) ** 2 for k in range(len(sr)))
            vp = sum((pr[k] - mp) ** 2 for k in range(len(pr)))
            denom = math.sqrt(vs * vp)
            corr = cov / denom if denom else 0.0
            parts.append(f"Correlation to the broad index (20 days): {corr:.2f}")

    # Section 6 - distribution.
    parts.append("\n--- DISTRIBUTION (last 90 days) ---")
    if len(daily) >= 21:
        d90 = daily[-91:] if len(daily) >= 91 else daily
        rets = []
        for k in range(1, len(d90)):
            if d90[k - 1]:
                rets.append((d90[k] - d90[k - 1]) / d90[k - 1] * 100.0)
        ups = [r for r in rets if r > 0]
        downs = [r for r in rets if r < 0]
        parts.append(f"Up days: {len(ups)}    Down days: {len(downs)}")
        if ups:
            parts.append(f"Average up day: +{sum(ups)/len(ups):.2f}%")
        if downs:
            parts.append(f"Average down day: {sum(downs)/len(downs):.2f}%")
        n = len(rets)
        if n:
            for thresh in (1.0, 2.0, 3.0):
                c = sum(1 for r in rets if abs(r) >= thresh)
                parts.append(f"Moves >= {thresh:.0f}%: {c} of {n} ({c/n*100:.1f}%)")

    # Section 7 - setup history.
    parts.append("\n--- SETUP HISTORY ---")
    if len(daily) >= 30:
        cur_pct = _percentile(daily, daily[-1])
        if cur_pct is not None:
            band_lo = max(0, cur_pct - 5)
            band_hi = min(100, cur_pct + 5)
            parts.append(
                f"Current close is in the {cur_pct}th percentile "
                f"of the last {len(daily)} daily closes."
            )
            hits = []
            for k in range(len(daily) - 16, 20, -1):
                window_k = daily[:k+1]
                pct_k = _percentile(window_k, window_k[-1])
                if pct_k is None:
                    continue
                if band_lo <= pct_k <= band_hi:
                    f5 = None
                    f10 = None
                    if k + 5 < len(daily):
                        base = daily[k]
                        if base:
                            f5 = (daily[k + 5] - base) / base * 100.0
                    if k + 10 < len(daily):
                        base = daily[k]
                        if base:
                            f10 = (daily[k + 10] - base) / base * 100.0
                    hits.append((k, f5, f10))
                    if len(hits) >= 3:
                        break

            # Complete statistics for the setup.
            f5_returns = [h[1] for h in hits if h[1] is not None]
            f10_returns = [h[2] for h in hits if h[2] is not None]
            if f5_returns:
                s = _forward_return_stats(f5_returns)
                parts.append(
                    f"  Complete Statistics (5-day forward): "
                    f"N={s['n']}, "
                    f"Win rate={s['win_rate']:.1f}% "
                    f"({s['wins']}W/{s['losses']}L), "
                    f"Expectancy={s['expectancy']:+.2f}%, "
                    f"Std dev={s['std']:.2f}%, "
                    f"Max drawdown={s['max_dd']:+.2f}%, "
                    f"t-stat={s['t_stat']:.2f}, "
                    f"Significant={'YES' if s['significant'] else 'NO'}"
                )
            if f10_returns:
                s = _forward_return_stats(f10_returns)
                parts.append(
                    f"  Complete Statistics (10-day forward): "
                    f"N={s['n']}, "
                    f"Win rate={s['win_rate']:.1f}% "
                    f"({s['wins']}W/{s['losses']}L), "
                    f"Expectancy={s['expectancy']:+.2f}%, "
                    f"Std dev={s['std']:.2f}%, "
                    f"Max drawdown={s['max_dd']:+.2f}%, "
                    f"t-stat={s['t_stat']:.2f}, "
                    f"Significant={'YES' if s['significant'] else 'NO'}"
                )
            if hits:
                parts.append(
                    f"The last {len(hits)} times the percentile "
                    f"was in the {band_lo}-{band_hi} band:"
                )
                daily_dates = [
                    str(r["trade_date"])
                    for r in (await session.execute(sql_text(
                        "SELECT trade_date FROM market_history "
                        "WHERE org_id = :oid AND symbol = :sym "
                        "AND timeframe = '1d' AND deleted_at IS NULL "
                        "ORDER BY trade_date ASC"
                    ), {"oid": org_id, "sym": symbol})).mappings().all()
                ]
                for k, f5, f10 in hits:
                    d = daily_dates[k] if k < len(daily_dates) else "?"
                    f5s = f"{f5:+.2f}%" if f5 is not None else "(n/a)"
                    f10s = f"{f10:+.2f}%" if f10 is not None else "(n/a)"
                    parts.append(
                        f"  {d} -> next 5 days: {f5s}, next 10 days: {f10s}"
                    )
            else:
                parts.append(
                    "No prior setups in the same percentile band in "
                    "the available daily history."
                )

    # Section 8 - sector today.
    parts.append("\n--- SECTOR TODAY ---")
    parts.append(f"Sector: {sector}")
    if sector and sector != "unknown":
        mover_rows = (await session.execute(sql_text(
            "SELECT DISTINCT (m->>'symbol') AS sym "
            "FROM market_events, "
            "jsonb_array_elements(event_record->'top_movers') AS m "
            "WHERE org_id = :oid "
            "AND event_at >= now() - interval '24 hours' "
            "AND m->>'symbol' IS NOT NULL"
        ), {"oid": org_id})).scalars().all()
        mover_syms = [s for s in mover_rows if s and s.upper() != symbol.upper()]
        if mover_syms:
            peer_rows = (await session.execute(sql_text(
                "SELECT symbol FROM stock_metadata "
                "WHERE symbol = ANY(:syms) AND sector = :sec"
            ), {"syms": mover_syms, "sec": sector})).scalars().all()
            peers = sorted(set(peer_rows))
            if peers:
                parts.append(
                    "Sector peers in today's top movers: " + ", ".join(peers)
                )
            else:
                parts.append("No sector peers in today's top movers.")
        else:
            parts.append("No top movers in the last 24 hours to compare.")

    # Section 9 - recent closes.
    parts.append("\n--- RECENT CLOSES (most recent last) ---")
    parts.append(", ".join(f"{c:.2f}" for c in closes[-8:]))

    # Section 10 - the readings for THIS stock, from market_history.
    parts.append("\n--- MY READINGS (this stock) ---")
    if len(closes) >= 11:
        import math as _math
        d10 = closes[-11:]
        r10 = []
        for k in range(1, len(d10)):
            a, b = d10[k - 1], d10[k]
            if a > 0 and b > 0:
                r10.append(_math.log(b / a))
        if len(r10) >= 3:
            m = sum(r10) / len(r10)
            var = sum((x - m) ** 2 for x in r10) / (len(r10) - 1)
            rv = (var ** 0.5) * 100.0
            parts.append(f"10-day realized volatility: {rv:.2f}% per day")
    if len(closes) >= 21:
        d20 = closes[-21:]
        moves = []
        for k in range(1, len(d20)):
            a, b = d20[k - 1], d20[k]
            if a > 0:
                moves.append(abs(b - a) / a * 100.0)
        if moves:
            am = sum(moves) / len(moves)
            parts.append(f"20-day average move: {am:.2f}% per day")
    if len(spy_closes) >= 21 and len(closes) >= 21:
        import math as _math
        n = 20
        sr = [(closes[k] - closes[k - 1]) / closes[k - 1]
              for k in range(len(closes) - n, len(closes))
              if closes[k - 1]]
        pr = [(spy_closes[k] - spy_closes[k - 1]) / spy_closes[k - 1]
              for k in range(len(spy_closes) - n, len(spy_closes))
              if spy_closes[k - 1]]
        if len(sr) == len(pr) and len(sr) > 2:
            ms_ = sum(sr) / len(sr)
            mp_ = sum(pr) / len(pr)
            cov = sum((sr[k] - ms_) * (pr[k] - mp_) for k in range(len(sr)))
            vs = sum((sr[k] - ms_) ** 2 for k in range(len(sr)))
            vp = sum((pr[k] - mp_) ** 2 for k in range(len(pr)))
            denom = _math.sqrt(vs * vp)
            if denom:
                corr = cov / denom
                parts.append(
                    f"20-day correlation to the market: {corr:.2f}"
                )

    return "\n".join(parts)
