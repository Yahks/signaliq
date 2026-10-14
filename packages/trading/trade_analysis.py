"""Compute derived facts about each trade.

The picker needs to know, before it picks, whether a trade has a
failure the article can write about. That means computing the
violations once and storing them — not re-deriving them on every
pick.

This module:

  1. Reads a trade row and the process document.
  2. Computes the violation codes, the headline violation, and
     two booleans (is_loss, has_stop).
  3. Upserts a row into trade_analysis keyed on trade_id.

The `backfill` function runs the same analysis for every trade in
the log. The `analyze_trade` function runs on individual trades as
they are inserted.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


DOLLARS_PER_POINT = 20

# Violation codes, in priority order (highest first). The headline
# is the first one that applies.
VIOLATION_PRIORITY = (
    "gap_skipped_stop",
    "stop_missing",
    "stop_distance_wrong",
    "clearance_low",
    "size_wrong",
    "atr_filter_violated",
)


def compute_facts(
    trade: dict[str, Any], process: dict[str, str],
) -> dict[str, Any]:
    """Return the derived facts for one trade."""
    entry = _num(trade.get("entry"))
    exit_p = _num(trade.get("exit"))
    contracts = _int(trade.get("contracts"))
    atr = _num(trade.get("atr_at_entry"))
    stop = _num(trade.get("stop"))
    result = _num(trade.get("result"))
    setup = (trade.get("setup") or "").lower()

    is_loss = result is not None and result < 0
    has_stop = stop is not None

    codes: set[str] = set()

    # --- stop_missing ---
    if not has_stop and entry is not None:
        codes.add("stop_missing")

    # --- stop_distance_wrong ---
    if (has_stop and atr is not None and entry is not None):
        implied = entry - 0.75 * atr
        if abs(stop - implied) > 0.5:
            codes.add("stop_distance_wrong")

    # --- size_wrong ---
    if (atr and contracts and atr > 0):
        formula = int(2000 / (atr * DOLLARS_PER_POINT))
        if formula > 0 and contracts != formula:
            codes.add("size_wrong")

    # --- clearance_low ---
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*clearance", setup)
    if m:
        try:
            if float(m.group(1)) < 0.5:
                codes.add("clearance_low")
        except ValueError:
            pass

    # --- atr_filter_violated ---
    if atr is not None and atr > 30:
        codes.add("atr_filter_violated")

    # --- gap_skipped_stop ---
    # Only fires when a stop was ACTUALLY placed and the market
    # moved past it. If no stop was placed, stop_missing covers
    # the failure — there is nothing to skip.
    if (is_loss and has_stop and entry is not None
            and exit_p is not None and stop is not None):
        adverse = abs(entry - exit_p)
        stop_distance = abs(entry - stop)
        if stop_distance and adverse > stop_distance * 1.05:
            codes.add("gap_skipped_stop")

    # Headline: first code in priority order that applies.
    headline = None
    for code in VIOLATION_PRIORITY:
        if code in codes:
            headline = code
            break

    return {
        "is_loss": is_loss,
        "has_stop": has_stop,
        "violation_codes": sorted(codes),
        "violation_count": len(codes),
        "headline_violation": headline,
    }


async def analyze_trade(
    session: AsyncSession,
    *,
    org_id: str,
    trade: dict[str, Any],
    process: dict[str, str],
) -> dict[str, Any]:
    """Compute and upsert the analysis for one trade."""
    facts = compute_facts(trade, process)
    await session.execute(sql_text("""
        INSERT INTO trade_analysis
            (trade_id, org_id, is_loss, has_stop,
             violation_codes, violation_count, headline_violation,
             analyzed_at)
        VALUES
            (:tid, :oid, :is_loss, :has_stop,
             :codes, :count, :headline,
             now())
        ON CONFLICT (trade_id) DO UPDATE SET
            is_loss = EXCLUDED.is_loss,
            has_stop = EXCLUDED.has_stop,
            violation_codes = EXCLUDED.violation_codes,
            violation_count = EXCLUDED.violation_count,
            headline_violation = EXCLUDED.headline_violation,
            analyzed_at = now()
    """), {
        "tid": trade["id"],
        "oid": org_id,
        "is_loss": facts["is_loss"],
        "has_stop": facts["has_stop"],
        "codes": facts["violation_codes"],
        "count": facts["violation_count"],
        "headline": facts["headline_violation"],
    })
    return facts


async def backfill(
    session: AsyncSession, org_id: str,
) -> dict[str, Any]:
    """Run analyze_trade for every trade in the log. Idempotent."""
    process = await _read_process(session, org_id)
    rows = (await session.execute(sql_text("""
        SELECT id::text, traded_at, instrument, direction, setup,
               entry, contracts, stop, atr_at_entry, target, exit,
               exit_reason, result, percent_of_equity, hold_time,
               lesson
        FROM trade_log
        WHERE org_id = :oid AND deleted_at IS NULL
    """), {"oid": org_id})).all()

    cols = (
        "id", "traded_at", "instrument", "direction", "setup",
        "entry", "contracts", "stop", "atr_at_entry", "target", "exit",
        "exit_reason", "result", "percent_of_equity", "hold_time",
        "lesson",
    )
    from decimal import Decimal
    analyzed = 0
    for row in rows:
        trade = dict(zip(cols, row))
        for k, v in list(trade.items()):
            if isinstance(v, Decimal):
                trade[k] = float(v)
        if trade.get("contracts") is not None:
            trade["contracts"] = int(trade["contracts"])
        await analyze_trade(
            session, org_id=org_id, trade=trade, process=process,
        )
        analyzed += 1
    await session.commit()
    log.info("trade_analysis.backfilled",
             org_id=org_id[:8], analyzed=analyzed)
    return {"analyzed": analyzed}


async def _read_process(
    session: AsyncSession, org_id: str,
) -> dict[str, str]:
    row = (await session.execute(sql_text("""
        SELECT daily_scan, entry_logic, position_sizing, exit_logic,
               trade_history_summary
        FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL LIMIT 1
    """), {"oid": org_id})).first()
    if not row:
        return {}
    return {
        "daily_scan": str(row[0] or ""),
        "entry_logic": str(row[1] or ""),
        "position_sizing": str(row[2] or ""),
        "exit_logic": str(row[3] or ""),
        "trade_history_summary": str(row[4] or ""),
    }


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
