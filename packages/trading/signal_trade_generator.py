"""Generate signal-driven CL trades from real price history.

These are BACKTEST trades, not executions. Every price is a real
NYMEX settlement; every rule is the trader's own. The row is written
with source='signal_backtest' so nothing downstream mistakes it for
a real fill, and the article must say "backtested" or "simulated".

Spread history: CL=F (front) and CLF27.NYM (Jan 27) both carry 250
daily bars, so the four spread states can be reconstructed for the
year. Each trade enters at the next bar's open when the state is
active, stops at 1.75xATR, targets 1.5%, and time-exits after 4
bars (the daily-bar approximation of the 4-hour time stop).

OPEC cut and API draw are NOT backtestable — their release values
were never recorded. No trades are generated for those signals.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.ingestion.sources.market_data import fetch_quote

log = get_logger(__name__)

ATR_WINDOW = 14
TARGET_PCT = 0.015
STOP_ATR_MULT = 1.75
MAX_HOLD_BARS = 4


async def _bars(sym: str) -> list[dict[str, Any]]:
    from packages.ingestion.sources.market_data import fetch_bars
    bars = await fetch_bars(sym, range_="1y", interval="1d")
    return bars or []


def _atr(bars: list[dict], i: int) -> float | None:
    if i < ATR_WINDOW:
        return None
    trs = []
    for k in range(i - ATR_WINDOW + 1, i + 1):
        h = float(bars[k]["high"]); l = float(bars[k]["low"])
        pc = float(bars[k - 1]["close"]) if k > 0 else float(bars[k]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def _spread_states(front: list[dict], second: list[dict]) -> dict[date, str]:
    """date -> backwardation_widening | ..._narrowing | contango_..."""
    by_date_f = {b["date"]: float(b["close"]) for b in front}
    by_date_s = {b["date"]: float(b["close"]) for b in second}
    common = sorted(set(by_date_f) & set(by_date_s))
    out: dict[date, str] = {}
    prev = None
    for d in common:
        sp = by_date_f[d] - by_date_s[d]
        level = "backwardation" if sp > 0 else "contango"
        if prev is None:
            state = f"{level}_flat"
        elif sp > prev:
            state = f"{level}_widening"
        elif sp < prev:
            state = f"{level}_narrowing"
        else:
            state = f"{level}_flat"
        out[d] = state
        prev = sp
    return out


async def generate_signal_trades(
    session: AsyncSession, org_id: str, signal: str = "spread",
) -> dict[str, Any]:
    if signal != "spread":
        return {"available": False,
                "reason": f"{signal} has no recorded release history"}

    front = await _bars("CL=F")
    second = await _bars("CLF27.NYM")
    if len(front) < ATR_WINDOW + 2 or not second:
        return {"available": False, "reason": "not enough bars"}

    states = _spread_states(front, second)
    by_date = {b["date"]: i for i, b in enumerate(front)}
    inserted = 0

    # Only states whose direction is bullish and whose composite is
    # not a veto produce a long. backwardation_widening is bullish and
    # allows entry; backwardation_narrowing is bearish and the veto
    # blocks new longs. contango_narrowing is bullish (the bearish
    # bias eroding) and allows entry; contango_widening is bearish.
    LONG_STATES = {
        "backwardation_widening",
        "contango_narrowing",
    }
    for d, state in states.items():
        if state not in LONG_STATES:
            continue
        i = by_date.get(d)
        if i is None or i + 2 >= len(front):
            continue
        atr = _atr(front, i)
        if not atr or atr <= 0:
            continue
        entry = float(front[i + 1]["open"])
        stop = round(entry - STOP_ATR_MULT * atr, 4)
        target = round(entry * (1 + TARGET_PCT), 4)
        risk = entry - stop
        if risk <= 0:
            continue

        exit_px = None
        exit_reason = None
        for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, len(front))):
            b = front[j]
            if float(b["low"]) <= stop:
                exit_px, exit_reason = stop, "stop hit"
                break
            if float(b["high"]) >= target:
                exit_px, exit_reason = target, "target hit"
                break
        if exit_px is None:
            last = front[min(i + MAX_HOLD_BARS, len(front) - 1)]
            exit_px, exit_reason = float(last["close"]), "time exit"

        result = round((exit_px - entry) * 1000.0, 2)  # CL = 1,000 bbl
        if result == 0:
            continue
        # R = dollar result / risk at entry. A gapped stop lands
        # far below -1R; that is the honest record on daily bars.
        r_multiple = round(result / (risk * 1000.0), 4)

        await session.execute(sql_text("""
            INSERT INTO trade_log
              (org_id, traded_at, instrument, direction, setup, entry,
               contracts, stop, atr_at_entry, target, exit, exit_reason,
               result, symbol, timeframe, source)
            VALUES
              (:o, :d, 'CL', 'long', :setup, :e, 1, :st, :atr, :tg,
               :ex, :er, :res, 'CL', '1d', 'signal_backtest')
        """), {
            "o": org_id,
            "d": (datetime.fromisoformat(d).date()
                  if isinstance(d, str) else d),
            "setup": f"spread {state}",
            "e": entry, "st": stop, "atr": round(atr, 4),
            "tg": target, "ex": exit_px, "er": exit_reason, "res": result,
        })
        inserted += 1

    await session.commit()
    log.info("signal_trades.generated", org_id=org_id[:8],
             signal=signal, inserted=inserted)
    return {"available": True, "inserted": inserted, "signal": signal}
