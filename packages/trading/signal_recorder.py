"""Record the fundamental signals as they fire, one row per day.

The article could never state a hit rate because the signal was
never recorded. This writes it.

Spread: real. Front-month CL=F minus the next listed month
(CLF27.NYM, or whichever two resolve). Level gives backwardation
vs contango; the change against the previous logged spread gives
widening vs narrowing.

Price: FRED WTISPLC daily, for forward returns.

OPEC cut / API draw: recorded as 'unavailable' — the release
numbers are not on Yahoo or FRED. The article says so rather than
inventing a value.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.ingestion.sources.market_data import fetch_quote

log = get_logger(__name__)


async def _upsert(
    session: AsyncSession, org_id: str, on: date, signal: str,
    state: str, value: float | None, source: str,
) -> None:
    await session.execute(sql_text("""
        INSERT INTO fundamental_signal_log
            (org_id, observed_on, signal, state, value, source)
        VALUES (:o, :d, :s, :st, :v, :src)
        ON CONFLICT (org_id, observed_on, signal) DO UPDATE
        SET state = EXCLUDED.state,
            value = EXCLUDED.value,
            source = EXCLUDED.source,
            recorded_at = now()
    """), {"o": org_id, "d": on, "s": signal, "st": state,
           "v": value, "src": source})


async def record_spread(session: AsyncSession, org_id: str) -> dict[str, Any]:
    """front-month minus next month. Both from Yahoo, real."""
    front = await fetch_quote("CL=F")
    # The next listed month after the front. Try the near ones.
    second = None
    for sym in ("CLF27.NYM", "CLG27.NYM", "CLH27.NYM", "CLJ27.NYM"):
        q = await fetch_quote(sym)
        if q and front and q.latest_price != front.latest_price:
            second = q
            break
    if not front or not second:
        await _upsert(session, org_id, date.today(), "spread",
                      "unavailable", None, "no second month")
        return {"status": "unavailable"}

    spread = round(front.latest_price - second.latest_price, 4)
    # previous logged spread
    prev = (await session.execute(sql_text("""
        SELECT value FROM fundamental_signal_log
        WHERE org_id=:o AND signal='spread' AND observed_on < :d
        ORDER BY observed_on DESC LIMIT 1
    """), {"o": org_id, "d": date.today()})).first()
    prev_val = float(prev[0]) if prev and prev[0] is not None else None

    level = "backwardation" if spread > 0 else "contango"
    if prev_val is None:
        state = f"{level}_flat"          # first observation, no change
    elif spread > prev_val:
        state = f"{level}_widening"
    elif spread < prev_val:
        state = f"{level}_narrowing"
    else:
        state = f"{level}_flat"

    await _upsert(session, org_id, date.today(), "spread", state,
                  spread, f"{front.symbol} - {second.symbol}")
    await session.commit()
    return {"status": "ok", "spread": spread, "state": state,
            "front": front.latest_price, "second": second.latest_price}


async def record_unavailable(session: AsyncSession, org_id: str,
                             signal: str, why: str) -> None:
    await _upsert(session, org_id, date.today(), signal, "unavailable",
                  None, why)
    await session.commit()


async def backfill_spread_history(
    session: AsyncSession, org_id: str,
) -> dict[str, Any]:
    """Rebuild the spread series from stored CL bars.

    Both contracts have daily history. For each date where both
    have a close, spread = front - second; the state is the level
    (backwardation / contango) plus the change against the prior
    day (widening / narrowing / flat).
    """
    from packages.ingestion.sources.market_data import fetch_bars

    front = await fetch_bars("CL=F")
    second = None
    for sym in ("CLF27.NYM", "CLG27.NYM", "CLH27.NYM"):
        b = await fetch_bars(sym)
        if b and front and b[0]["close"] != front[0]["close"]:
            second = b
            break
    if not front or not second:
        return {"status": "no second month"}

    fmap = {b["date"]: float(b["close"]) for b in front}
    smap = {b["date"]: float(b["close"]) for b in second}
    dates = sorted(set(fmap) & set(smap))

    prev = None
    written = 0
    for d in dates:
        spread = round(fmap[d] - smap[d], 4)
        level = "backwardation" if spread > 0 else "contango"
        if prev is None or spread == prev:
            state = f"{level}_flat"
        elif spread > prev:
            state = f"{level}_widening"
        else:
            state = f"{level}_narrowing"
        from datetime import date as _date
        y, m, dd = (int(x) for x in d.split("-"))
        await _upsert(session, org_id, _date(y, m, dd), "spread",
                      state, spread, f"CL=F - {second[0].get('symbol','CL2')}")
        prev = spread
        written += 1
    await session.commit()
    return {"status": "ok", "rows": written,
            "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None}
