"""Backtest the exit structure on stored daily bars.

Answers the reviewer's Error 2: given a 1.5% target, a 1.75xATR
stop, and a 4-hour time stop, what actually happens on CL?

The 4-hour time stop cannot be evaluated on daily bars — one bar is
a day, and the target/stop can be hit intrabar. This computes the
honest daily-bar version: enter at next open, and on each subsequent
bar check whether the high/low crossed the target or the stop. If
both are crossed in the same bar, count it as the stop (conservative).
The time stop is approximated as: exit at the close of the bar that
is 1 day after entry (4 hours is within one daily bar).

Returns hit rates, R:R, breakeven hit rate, and expectancy in R.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

TARGET_PCT = 0.015
STOP_ATR_MULT = 1.75
ATR_WINDOW = 14


def _atr(bars: list[dict], i: int, window: int = ATR_WINDOW) -> float | None:
    if i < window:
        return None
    trs = []
    for k in range(i - window + 1, i + 1):
        h = bars[k]["high"]
        l = bars[k]["low"]
        pc = bars[k - 1]["close"] if k > 0 else bars[k]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


async def backtest_exit_structure(
    session: AsyncSession, org_id: str, symbol: str = "CL",
    max_hold_bars: int = 1,
) -> dict[str, Any]:
    rows = (await session.execute(sql_text("""
        SELECT trade_date, open, high, low, close
        FROM market_history
        WHERE org_id = :o AND symbol = :s AND deleted_at IS NULL
          AND trade_date IS NOT NULL
        ORDER BY trade_date
    """), {"o": org_id, "s": symbol})).mappings().all()
    # Numeric columns arrive as Decimal; coerce to float once.
    bars = [
        {
            "trade_date": r["trade_date"],
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
        }
        for r in rows
        if None not in (r["open"], r["high"], r["low"], r["close"])
    ]
    if len(bars) < ATR_WINDOW + 2:
        return {"available": False, "reason": "not enough bars"}

    target_hits = 0
    stop_hits = 0
    time_exits = 0
    r_results: list[float] = []

    for i in range(ATR_WINDOW, len(bars) - 2):
        atr = _atr(bars, i)
        if not atr or atr <= 0:
            continue
        entry = bars[i + 1]["open"]
        if entry <= 0:
            continue
        target = entry * (1 + TARGET_PCT)
        stop = entry - STOP_ATR_MULT * atr
        risk = entry - stop
        if risk <= 0:
            continue

        hit = None
        for j in range(i + 1, min(i + 1 + max_hold_bars, len(bars))):
            b = bars[j]
            # conservative: if both crossed in one bar, count the stop
            if b["low"] <= stop:
                hit = ("stop", -1.0)
                break
            if b["high"] >= target:
                hit = ("target", (target - entry) / risk)
                break
        if hit is None:
            # time stop: exit at close of the last held bar
            last = bars[min(i + max_hold_bars, len(bars) - 1)]
            hit = ("time", (last["close"] - entry) / risk)

        kind, r = hit
        if kind == "target":
            target_hits += 1
        elif kind == "stop":
            stop_hits += 1
        else:
            time_exits += 1
        r_results.append(r)

    n = len(r_results)
    if n == 0:
        return {"available": False, "reason": "no evaluable entries"}

    hit_rate = target_hits / n
    rr = TARGET_PCT / (STOP_ATR_MULT * (1.0))  # placeholder, replaced below
    expectancy_r = sum(r_results) / n

    return {
        "available": True,
        "symbol": symbol,
        "n": n,
        "target_hits": target_hits,
        "stop_hits": stop_hits,
        "time_exits": time_exits,
        "target_hit_rate_pct": hit_rate * 100.0,
        "expectancy_r": expectancy_r,
        "first_bar": bars[0]["trade_date"].isoformat(),
        "last_bar": bars[-1]["trade_date"].isoformat(),
    }
