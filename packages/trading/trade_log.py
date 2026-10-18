"""Trade log — the trader's actual trades.

Every article needs one real trade to open on. This module:
  - imports trades from YAML
  - reads the last N trades for the excerpt
  - generates synthetic trades from the process document
    (added in a follow-up session)

Sources:
  - manual entry via /trading/trade-log
  - YAML import
  - hourly synthetic trades
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import yaml
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


TRADE_FIELDS = (
    "traded_at", "instrument", "direction", "setup", "entry",
    "contracts", "stop", "atr_at_entry", "target", "exit",
    "exit_reason", "result", "percent_of_equity", "hold_time",
    "lesson",
)


def _parse_yaml(yaml_text: str) -> tuple[float | None, list[dict[str, Any]]]:
    """Parse a trade-history YAML string.

    Returns (account_equity, list_of_trade_dicts). Raises ValueError
    on malformed input.
    """
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML parse failed: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("YAML root must be a mapping")

    equity = data.get("account_equity")
    try:
        equity = float(equity) if equity is not None else None
    except (TypeError, ValueError):
        equity = None

    raw_trades = data.get("trades") or []
    if not isinstance(raw_trades, list):
        raise ValueError("'trades' must be a list")

    out: list[dict[str, Any]] = []
    for i, t in enumerate(raw_trades):
        if not isinstance(t, dict):
            continue
        # Date is required.
        d = t.get("date") or t.get("traded_at")
        if not d:
            log.warning("trade_log.skip_no_date", index=i)
            continue
        try:
            dt = datetime.fromisoformat(str(d))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            log.warning("trade_log.skip_bad_date", index=i, date=str(d))
            continue

        out.append({
            "traded_at": dt,
            "instrument": str(t.get("instrument") or "").strip()[:24],
            "direction": str(t.get("direction") or "long").strip()[:8],
            "setup": str(t.get("setup") or "").strip(),
            "entry": _num(t.get("entry")),
            "contracts": _int(t.get("contracts")),
            "stop": _num(t.get("stop")),
            "atr_at_entry": _num(t.get("atr_at_entry")),
            "target": _num(t.get("target")),
            "exit": _num(t.get("exit")),
            "exit_reason": str(t.get("exit_reason") or "").strip(),
            "result": _num(t.get("result")),
            "percent_of_equity": _num(t.get("percent_of_equity")),
            "hold_time": str(t.get("hold_time") or "")[:32],
            "lesson": str(t.get("lesson") or "").strip(),
        })
    return equity, out


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


async def import_yaml_trades(
    session: AsyncSession, org_id: str, yaml_text: str,
) -> dict[str, int]:
    """Import trades from YAML.

    Skips duplicates by (org_id, traded_at, instrument, entry).
    Returns {"imported": N, "skipped": M, "total": T}.
    """
    equity, trades = _parse_yaml(yaml_text)
    imported = 0
    skipped = 0

    for t in trades:
        # Duplicate check
        dup = (await session.execute(sql_text("""
            SELECT 1 FROM trade_log
            WHERE org_id = :oid
              AND traded_at = :ts
              AND instrument = :inst
              AND entry = :entry
              AND deleted_at IS NULL
            LIMIT 1
        """), {
            "oid": org_id,
            "ts": t["traded_at"],
            "inst": t["instrument"],
            "entry": t["entry"],
        })).first()
        if dup:
            skipped += 1
            continue

        # Derive the fields this INSERT needs that the YAML
        # trade record doesn't carry. Complete the row so every
        # market_analysis block gets a clean trade.
        sym = t.get("symbol") or t.get("instrument") or ""
        tf = t.get("timeframe") or "1d"
        traded_at = t.get("traded_at")
        entry_time = t.get("entry_time") or traded_at
        exit_time = t.get("exit_time")
        if exit_time is None and entry_time is not None:
            try:
                from datetime import timedelta as _td
                exit_time = entry_time + _td(days=1)
            except Exception:
                exit_time = entry_time
        setup_detail = t.get("setup_detail") or t.get("setup") or ""
        hesitation_note = t.get("hesitation_note") or ""

        # Completeness check.
        missing = []
        if not sym:
            missing.append("symbol")
        if not tf:
            missing.append("timeframe")
        for f in ("stop", "target", "exit", "atr_at_entry", "entry",
                  "result", "exit_reason", "traded_at"):
            if t.get(f) is None:
                missing.append(f)
        if missing:
            log.warning(
                "trade_log.incomplete_yaml_trade",
                symbol=sym, missing=missing,
            )
            skipped += 1
            continue

        await session.execute(sql_text("""
            INSERT INTO trade_log (
                org_id, traded_at, instrument, direction, setup,
                entry, contracts, stop, atr_at_entry, target, exit,
                exit_reason, result, percent_of_equity, hold_time,
                lesson, source, symbol, timeframe,
                entry_time, exit_time, setup_detail, hesitation_note
            ) VALUES (
                :oid, :traded_at, :instrument, :direction, :setup,
                :entry, :contracts, :stop, :atr_at_entry, :target, :exit,
                :exit_reason, :result, :percent_of_equity, :hold_time,
                :lesson, 'yaml', :symbol, :timeframe,
                :entry_time, :exit_time, :setup_detail, :hesitation_note
            )
        """), {
            "oid": org_id, **t,
            "symbol": sym, "timeframe": tf,
            "entry_time": entry_time, "exit_time": exit_time,
            "setup_detail": setup_detail,
            "hesitation_note": hesitation_note,
        })
        imported += 1

    # Write the account equity back to the process document so the
    # validator and the article prompt see the current value.
    if equity is not None:
        await session.execute(sql_text("""
            UPDATE trader_process
            SET trade_history_summary = regexp_replace(
                trade_history_summary,
                r'account_equity\s*:\s*[\d,.]+',
                'account_equity: ' || :eq,
                'g'
            )
            WHERE org_id = :oid AND deleted_at IS NULL
        """), {"oid": org_id, "eq": str(equity)})

    await session.commit()
    log.info("trade_log.imported",
             org_id=org_id[:8], imported=imported,
             skipped=skipped, total=len(trades),
             equity=equity)
    return {
        "imported": imported, "skipped": skipped,
        "total": len(trades), "equity": equity,
    }


async def get_recent_trades(
    session: AsyncSession, org_id: str, limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the last N live trades for the org, newest first."""
    rows = (await session.execute(sql_text("""
        SELECT id::text, traded_at, instrument, direction, setup,
               entry, contracts, stop, atr_at_entry, target, exit,
               exit_reason, result, percent_of_equity, hold_time,
               lesson, source
        FROM trade_log
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY traded_at DESC
        LIMIT :n
    """), {"oid": org_id, "n": limit})).all()

    cols = (
        "id", "traded_at", "instrument", "direction", "setup",
        "entry", "contracts", "stop", "atr_at_entry", "target", "exit",
        "exit_reason", "result", "percent_of_equity", "hold_time",
        "lesson", "source",
    )
    return [dict(zip(cols, r)) for r in rows]
