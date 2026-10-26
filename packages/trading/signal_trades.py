"""Turn a logged signal into a trade, from the rule.

Every article needs a real trade. This reads the signal log, and
for each state with no trade yet, creates one from the trader's own
rule: entry at the next session's open, stop at 1.75 x ATR, target
at 1.5% above entry. The trade row carries `signal`, so the article
cites it and the math filters by state.

The outcome is filled when the bar after entry closes: stop hit if
low <= stop, target hit if high >= target, else time exit at close.
That is the same convention the backtest uses.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

CL_MULTIPLIER = 1000.0
TARGET_PCT = 0.015
STOP_ATR_MULT = 1.75
ATR_WINDOW = 14


def _trade_datetime(d) -> "datetime":
    """A date -> the session-open timestamp the other trade rows use.

    The pipeline's trade rows carry a tz-aware datetime at 13:30 UTC
    (the US cash open). signal_log stores a date; this binds it to
    the same convention so the row is comparable.
    """
    from datetime import datetime, time, timezone
    if isinstance(d, str):
        y, m, dd = (int(x) for x in d.split("-"))
        d = date(y, m, dd)
    return datetime.combine(d, time(13, 30), tzinfo=timezone.utc)


async def _bars(session: AsyncSession, org_id: str, symbol: str) -> list[dict]:
    rows = (await session.execute(sql_text("""
        SELECT trade_date, open, high, low, close
        FROM market_history
        WHERE org_id=:o AND symbol=:s AND deleted_at IS NULL
          AND trade_date IS NOT NULL
        ORDER BY trade_date
    """), {"o": org_id, "s": symbol})).mappings().all()
    return [
        {"d": r["trade_date"], "o": float(r["open"]), "h": float(r["high"]),
         "l": float(r["low"]), "c": float(r["close"])}
        for r in rows
        if None not in (r["open"], r["high"], r["low"], r["close"])
    ]


def _atr(bars: list[dict], i: int) -> float | None:
    if i < ATR_WINDOW:
        return None
    trs = []
    for k in range(i - ATR_WINDOW + 1, i + 1):
        pc = bars[k - 1]["c"] if k > 0 else bars[k]["c"]
        trs.append(max(bars[k]["h"] - bars[k]["l"],
                       abs(bars[k]["h"] - pc), abs(bars[k]["l"] - pc)))
    return sum(trs) / len(trs)


async def create_trades_for_signals(
    session: AsyncSession, org_id: str, symbol: str = "CL",
    account_equity: float = 200000.0, risk_pct: float = 0.015,
) -> list[dict[str, Any]]:
    """One trade per logged signal day that has none yet.

    A trade is created only if the risk budget can afford at least
    one contract at the 1.75xATR stop. Otherwise the signal is
    recorded as skipped — which is what the trader's own rule says.
    """
    budget_dollars = account_equity * risk_pct
    bars = await _bars(session, org_id, symbol)
    if len(bars) < ATR_WINDOW + 2:
        return []
    by_date = {b["d"]: i for i, b in enumerate(bars)}

    logged = (await session.execute(sql_text("""
        SELECT observed_on, signal, state, value
        FROM fundamental_signal_log
        WHERE org_id=:o AND state NOT LIKE '%unavailable%'
          AND state NOT LIKE '%_flat'
        ORDER BY observed_on
    """), {"o": org_id})).mappings().all()

    made: list[dict[str, Any]] = []
    for s in logged:
        sig = f"{s['signal']}/{s['state']}"
        exists = (await session.execute(sql_text("""
            SELECT 1 FROM trade_log
            WHERE org_id=:o AND signal=:sig AND deleted_at IS NULL LIMIT 1
        """), {"o": org_id, "sig": sig})).first()
        if exists:
            continue
        i = by_date.get(s["observed_on"])
        if i is None or i + 1 >= len(bars):
            continue
        atr = _atr(bars, i)
        if not atr:
            continue
        nxt = bars[i + 1]
        entry = nxt["o"]
        stop = round(entry - STOP_ATR_MULT * atr, 4)
        target = round(entry * (1 + TARGET_PCT), 4)
        risk = entry - stop
        if risk <= 0:
            continue
        # The trader's sizing rule: if the risk budget cannot afford
        # one contract at this stop distance, the trade is SKIPPED.
        # At current CL volatility 1.75xATR is ~$7.67/bbl = $7,668
        # per contract, against a $3,000 budget -> 0.39 contracts ->
        # skip. The generator must apply the same rule the source
        # states, or it fabricates trades the trader would not take.
        risk_per_contract = risk * CL_MULTIPLIER
        if risk_per_contract > budget_dollars:
            made.append({
                "signal": sig, "skipped": True,
                "risk_per_contract": round(risk_per_contract, 2),
                "budget": round(budget_dollars, 2),
                "contracts_affordable": round(budget_dollars / risk_per_contract, 3),
            })
            continue
        # One bar after entry decides the outcome.
        j = i + 1
        b = bars[j]
        if b["l"] <= stop:
            exit_, reason, res = stop, "stop hit", (stop - entry) * CL_MULTIPLIER
        elif b["h"] >= target:
            exit_, reason, res = target, "target hit", (target - entry) * CL_MULTIPLIER
        else:
            exit_, reason, res = b["c"], "time exit", (b["c"] - entry) * CL_MULTIPLIER

        await session.execute(sql_text("""
            INSERT INTO trade_log
                (org_id, traded_at, instrument, symbol, direction, setup,
                 entry, contracts, stop, atr_at_entry, target, exit,
                 exit_reason, result, signal, source)
            VALUES
                (:o, :t, :inst, :sym, 'long', :setup, :entry, 1, :stop,
                 :atr, :target, :exit, :reason, :res, :sig, 'signal')
        """), {
            "o": org_id, "t": _trade_datetime(nxt["d"]),
            "inst": symbol, "sym": symbol,
            "setup": f"Long on {sig} (spread state logged {s['observed_on']})",
            "entry": entry, "stop": stop, "atr": atr, "target": target,
            "exit": exit_, "reason": reason, "res": res, "sig": sig,
        })
        made.append({"signal": sig, "entry": entry, "stop": stop,
                     "exit": exit_, "reason": reason, "result": res})

    await session.commit()
    return made
