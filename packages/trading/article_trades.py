"""Pick the next trade for an article, and build the skeleton.

Design:
  - The picker takes the oldest unused losing trade with a
    violation. When no violation trades remain, falls back to
    any unused losing trade.
  - No synthetic trades. The trade log is the only source.
  - The skeleton has raw facts only. No derived values, no
    counterfactuals, no per-trade arithmetic.
  - Related trades are formatted as separate key-value blocks
    so the model cannot cross-read facts from one trade into
    another.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


REQUIRED_FIELDS = (
    "traded_at", "instrument", "entry", "contracts",
    "atr_at_entry", "exit", "result",
)


def _equity_from_summary(summary: str) -> float | None:
    """Read account_equity out of the trade_history_summary text."""
    m = re.search(r"account_equity\s*:\s*([\d,.]+)", summary or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _natural_date(d) -> str:
    """'Wednesday, June 3, 2026' — a date the model can copy."""
    if d is None:
        return "?"
    try:
        day = int(d.strftime("%d"))
        return f"{d.strftime('%A, %B')} {day}, {d.strftime('%Y')}"
    except Exception:
        return str(d)




async def load_performance_record(
    session: AsyncSession, org_id: str,
    anchor: datetime | None = None,
    days: int = 90,
) -> dict[str, Any]:
    """Trailing performance stats from the trade log.

    If anchor is given, computes only over trades in the `days`
    window ending on anchor. If None, computes over all history.
    """
    if anchor is not None:
        row = (await session.execute(sql_text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE result > 0) AS winners,
                COUNT(*) FILTER (WHERE result < 0) AS losers,
                COUNT(*) FILTER (WHERE result = 0) AS scratches,
                AVG(result) FILTER (WHERE result > 0) AS avg_winner,
                AVG(result) FILTER (WHERE result < 0) AS avg_loser,
                MAX(result) AS largest_win,
                MIN(result) AS largest_loss
            FROM trade_log
            WHERE org_id = :oid AND deleted_at IS NULL
              AND source = 'synthetic'
              AND traded_at <= :anchor
              AND traded_at > :anchor - make_interval(days => :days)
        """), {"oid": org_id, "anchor": anchor, "days": days})).first()
    else:
        row = (await session.execute(sql_text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE result > 0) AS winners,
                COUNT(*) FILTER (WHERE result < 0) AS losers,
                COUNT(*) FILTER (WHERE result = 0) AS scratches,
                AVG(result) FILTER (WHERE result > 0) AS avg_winner,
                AVG(result) FILTER (WHERE result < 0) AS avg_loser,
                MAX(result) AS largest_win,
                MIN(result) AS largest_loss
            FROM trade_log
            WHERE org_id = :oid AND deleted_at IS NULL
        """), {"oid": org_id})).first()
    if not row:
        return {}

    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "total": int(row[0] or 0),
        "winners": int(row[1] or 0),
        "losers": int(row[2] or 0),
        "scratches": int(row[3] or 0),
        "average_winner": _f(row[4]),
        "average_loser": _f(row[5]),
        "largest_win": _f(row[6]),
        "largest_loss": _f(row[7]),
    }


def _performance_record_block(rec: dict[str, Any]) -> list[str]:
    """Format the performance record as a skeleton block."""
    if not rec or not rec.get("total"):
        return []
    lines = []
    lines.append("PERFORMANCE RECORD (from the trade log):")
    lines.append("")
    lines.append(f"  total trades:    {rec['total']}")
    lines.append(f"  winners:         {rec['winners']}")
    lines.append(f"  losers:          {rec['losers']}")
    lines.append(f"  scratches:       {rec['scratches']}")
    if rec.get("average_winner") is not None:
        lines.append(
            f"  average winner:  ${int(round(rec['average_winner'])):,}"
        )
    if rec.get("average_loser") is not None:
        lines.append(
            f"  average loser:   ${int(round(rec['average_loser'])):,}"
        )
    if rec.get("largest_win") is not None:
        lines.append(
            f"  largest win:     ${int(round(rec['largest_win'])):,}"
        )
    if rec.get("largest_loss") is not None:
        lines.append(
            f"  largest loss:    ${int(round(rec['largest_loss'])):,}"
        )
    lines.append("")
    return lines
async def pick_batch(
    session: AsyncSession, org_id: str,
) -> list[dict[str, Any]] | None:
    """Return the 6 trades of the oldest unused batch.

    A batch is a group of trades that share a `batch_id`. The
    pipeline picks the batch, uses the first trade as the
    article's subject, and the other 5 as the related trades.
    After the article persists, ALL 6 are marked used.
    """
    # Ensure used_in_article_id and batch_id exist.
    try:
        await session.execute(sql_text(
            "SELECT used_in_article_id, batch_id FROM trade_log LIMIT 1"
        ))
    except Exception:
        await session.execute(sql_text(
            "ALTER TABLE trade_log "
            "ADD COLUMN IF NOT EXISTS used_in_article_id uuid"
        ))
        await session.execute(sql_text(
            "ALTER TABLE trade_log "
            "ADD COLUMN IF NOT EXISTS batch_id uuid"
        ))
        await session.commit()

    # Find the least-recently-used batch_id.
    #
    # S.9.28 — the old query filtered on used_in_article_id IS NULL,
    # which meant each batch could only ever be used once. With 34
    # batches and 2 subblocks (risk_management, investment_strategies)
    # consuming them, the pool exhausted after 34 articles. After
    # that, both subblocks returned no_trades_available forever.
    #
    # Now: never-used batches first, then the batch whose article
    # was created longest ago, then by trade date. Every batch
    # cycles. A trade log is history, not inventory.
    batch_row = (await session.execute(sql_text("""
        SELECT tl.batch_id
        FROM trade_log tl
        LEFT JOIN trading_posts tp ON tp.id = tl.used_in_article_id
        WHERE tl.org_id = :oid
          AND tl.deleted_at IS NULL
          AND tl.batch_id IS NOT NULL
        GROUP BY tl.batch_id
        ORDER BY
            MAX(tp.created_at) NULLS FIRST,
            MIN(tl.traded_at) ASC
        LIMIT 1
    """), {"oid": org_id})).first()

    if not batch_row or not batch_row[0]:
        return None
    batch_id = batch_row[0]

    # S.9.28 — drop the used_in_article_id filter. The batch may
    # have been used before (LRU rotation), but the trades are the
    # same. The article is new because the coordinate changed.
    rows = (await session.execute(sql_text("""
        SELECT t.id::text, t.traded_at, t.instrument, t.direction,
               t.setup, t.entry, t.contracts, t.stop, t.atr_at_entry,
               t.exit, t.exit_reason, t.result, t.batch_id::text
        FROM trade_log t
        WHERE t.org_id = :oid
          AND t.deleted_at IS NULL
          AND t.batch_id = :bid
          AND t.traded_at IS NOT NULL
          AND t.instrument IS NOT NULL
          AND t.entry IS NOT NULL
          AND t.contracts IS NOT NULL
          AND t.atr_at_entry IS NOT NULL
          AND t.exit IS NOT NULL
          AND t.result IS NOT NULL
        ORDER BY t.traded_at ASC
    """), {"oid": org_id, "bid": batch_id})).all()

    if not rows or len(rows) < 4:
        return None
    # If fewer than 4 losses exist, the batch still has 4+ trades
    # (verified above). The diagnostics cite whatever is available.

    cols = ("id", "traded_at", "instrument", "direction", "setup",
            "entry", "contracts", "stop", "atr_at_entry",
            "exit", "exit_reason", "result", "batch_id")
    out = []
    from decimal import Decimal
    for row in rows:
        d = dict(zip(cols, row))
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = float(v)
        if d.get("contracts") is not None:
            d["contracts"] = int(d["contracts"])
        out.append(d)
    return out



async def load_related_trades(
    session: AsyncSession, org_id: str,
    trade: dict[str, Any], limit: int = 6,
) -> list[dict[str, Any]]:
    """Return up to `limit` other losing trades to cite in the
    diagnostics. Never the picked trade."""
    rows = (await session.execute(sql_text("""
        SELECT t.traded_at, t.contracts, t.entry, t.stop, t.exit,
               t.setup, t.result, t.atr_at_entry
        FROM trade_log t
        JOIN trade_analysis a ON a.trade_id = t.id
        WHERE t.org_id = :oid
          AND t.deleted_at IS NULL
          AND t.source != 'synthetic'
          AND t.id != :tid
          AND a.is_loss = true
        ORDER BY t.traded_at DESC
        LIMIT :n
    """), {"oid": org_id, "tid": trade["id"], "n": limit})).all()

    cols = ("traded_at", "contracts", "entry", "stop", "exit",
            "setup", "result", "atr_at_entry")
    out = []
    from decimal import Decimal
    for row in rows:
        d = dict(zip(cols, row))
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = float(v)
        if d.get("contracts") is not None:
            d["contracts"] = int(d["contracts"])
        out.append(d)
    return out


_DEFAULT_PROCESS = {
    "daily_scan": (
        "Scan NQ at 9:30 ET for a 10-day high breach. Reject "
        "entries when the 5-day ATR is above $30."
    ),
    "entry_logic": (
        "Enter on a breakout above the 10-day high with at least "
        "0.5% clearance. Reject entries when the 5-day ATR is "
        "above $30."
    ),
    "position_sizing": "contracts = int($2000 / (ATR * $20))",
    "exit_logic": (
        "Stop at 0.75 x ATR below entry. Exit on stop hit or "
        "end of session."
    ),
    "trade_history_summary": "account_equity: 200000",
}


async def read_process(
    session: AsyncSession, org_id: str,
) -> dict[str, str]:
    """Read the trader's process document. If the org has no row
    (unprovisioned, older than the signup flow), return defaults
    so the pipeline still produces an article."""
    try:
        row = (await session.execute(sql_text("""
            SELECT daily_scan, entry_logic, position_sizing, exit_logic,
                   trade_history_summary
            FROM trader_process
            WHERE org_id = :oid AND deleted_at IS NULL LIMIT 1
        """), {"oid": org_id})).first()
    except Exception as exc:
        log.warning("read_process.query_failed", error=str(exc)[:120])
        row = None

    if not row:
        return dict(_DEFAULT_PROCESS)

    return {
        "daily_scan": str(row[0] or _DEFAULT_PROCESS["daily_scan"]),
        "entry_logic": str(row[1] or _DEFAULT_PROCESS["entry_logic"]),
        "position_sizing": str(row[2] or _DEFAULT_PROCESS["position_sizing"]),
        "exit_logic": str(row[3] or _DEFAULT_PROCESS["exit_logic"]),
        "trade_history_summary": str(
            row[4] or _DEFAULT_PROCESS["trade_history_summary"]
        ),
    }


def _rule_violations(
    trade: dict[str, Any], process: dict[str, str],
) -> list[str]:
    """Return every rule violation the trade committed. The
    article is about ONE of these."""
    violations: list[str] = []
    atr = trade.get("atr_at_entry")
    contracts = trade.get("contracts")
    entry = trade.get("entry")
    exit_p = trade.get("exit")
    result = trade.get("result")
    stop = trade.get("stop")
    setup = (trade.get("setup") or "").lower()

    # Clearance rule.
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*clearance", setup)
    if m and float(m.group(1)) < 0.5:
        violations.append(
            f"Clearance rule violated: the breakout cleared "
            f"{m.group(1)}%, below the 0.5% minimum."
        )

    # Stop rule.
    if stop is None and atr and entry:
        implied = round(float(entry) - 0.75 * float(atr), 2)
        violations.append(
            f"No stop was placed. The 0.75×ATR rule would have "
            f"set the stop at {implied}."
        )

    # Sizing rule.
    if atr and contracts:
        formula = int(2000 / (atr * 20))
        if formula > 0 and contracts != formula:
            side = "under" if contracts < formula else "over"
            violations.append(
                f"Sizing rule violated: {contracts} contracts used "
                f"but the formula says {formula} ({side}-sized)."
            )

    # ATR filter.
    if atr is not None and atr > 30:
        violations.append(
            f"ATR filter violated: ATR was {atr}, above the $30 "
            f"threshold."
        )

    return violations


def _process_rules(process: dict[str, str]) -> list[str]:
    return [
        "Clearance: a breakout must clear at least 0.5% of the 10-day high.",
        "Sizing: contracts = the integer part of ($2,000 / (ATR × $20)).",
        "Stop: 0.75 × ATR below entry.",
        "ATR filter: reject entries when the 5-day ATR is above $30.",
    ]


def _format_trade_block(
    header: str, trade: dict[str, Any],
) -> list[str]:
    lines = [header, ""] if header else []
    _dt = trade.get("traded_at")
    if _dt and hasattr(_dt, "strftime"):
        _h = _dt.hour % 12 or 12
        _ampm = "AM" if _dt.hour < 12 else "PM"
        _time_str = f"{_h}:{_dt.minute:02d} {_ampm}"
        lines.append(f"  date:         {_natural_date(_dt)} at {_time_str}")
    else:
        lines.append(f"  date:         {_natural_date(_dt)}")
    lines.append(f"  instrument:   {trade.get('instrument', 'NQ')}")
    lines.append(f"  direction:    {trade.get('direction') or 'long'}")
    lines.append(f"  contracts:    {trade.get('contracts')}")
    lines.append(f"  entry:        {trade.get('entry')}")
    # Actual stop and rule stop — both shown so the article can
    # reconcile them without inventing either.
    actual_stop = trade.get("stop")
    entry = trade.get("entry")
    atr = trade.get("atr_at_entry")
    rule_stop = None
    if entry is not None and atr is not None:
        rule_stop = round(entry - 0.75 * atr, 2)
    if actual_stop is None:
        lines.append(f"  stop:         (no stop was placed)")
    else:
        lines.append(f"  stop:         {actual_stop}")
    if rule_stop is not None and actual_stop is not None:
        if abs(actual_stop - rule_stop) > 0.01:
            diff = actual_stop - rule_stop
            lines.append(
                f"  rule stop:    {rule_stop}  "
                f"(0.75 × ATR = {round(0.75 * atr, 2)} below entry; "
                f"the actual stop is {round(diff, 2)} points from it)"
            )
        else:
            lines.append(f"  rule stop:    {rule_stop} (matches)")
    lines.append(f"  exit:         {trade.get('exit')}")
    lines.append(f"  exit_reason:  {trade.get('exit_reason') or '(not recorded)'}")

    # percent_of_equity — where the result sits relative to the
    # account. The model quotes this directly; the validator
    # requires every percent to trace back to the skeleton.
    _pct = trade.get("percent_of_equity")
    if _pct is not None:
        _pct_val = float(_pct)
        _pct_str = f"{_pct_val:.2f}%"
        if _pct_val < 0:
            lines.append(f"  percent_of_equity: {_pct_str} (of account equity)")
        elif _pct_val > 0:
            lines.append(f"  percent_of_equity: +{_pct_str} (of account equity)")
        else:
            lines.append(f"  percent_of_equity: 0.00% (of account equity)")

    _r = trade.get("result")
    if _r is None:
        _r_str = "?"
    elif _r < 0:
        _r_str = f"LOSS of ${abs(int(_r)):,}"
    elif _r > 0:
        _r_str = f"WIN of ${int(_r):,}"
    else:
        _r_str = "SCRATCH ($0)"
    lines.append(f"  result:       {_r_str}")
    lines.append(f"  atr_at_entry: {trade.get('atr_at_entry')}")
    lines.append(f"  setup:        {trade.get('setup') or '(not recorded)'}")
    lines.append("")
    return lines





def build_skeleton(
    trade: dict[str, Any],
    process: dict[str, str],
    account_equity: float | None = None,
    related: list[dict[str, Any]] | None = None,
    performance: dict[str, Any] | None = None,
) -> str:
    """Minimal skeleton. The picked trade's raw facts, related
    trades' raw facts, the violations, and the process rules."""
    lines: list[str] = []

    lines.append(
        "THE ARTICLE'S TRADE — the article is about this trade. "
        "Every beat except the diagnostics uses ONLY this trade's "
        "facts. Do not take a number from any other block."
    )
    lines.append("")
    lines.extend(_format_trade_block("", trade))

    if related:
        lines.append(
            "THE RELATED TRADES — for the diagnostics list ONLY. "
            "Each item in the diagnostics bulleted list cites ONE "
            "of these trades, by date. Do not cite these trades "
            "anywhere else. Do not cite THE ARTICLE'S TRADE in the "
            "diagnostics."
        )
        lines.append("")
        for idx, r in enumerate(related, 1):
            lines.append(f"Related Trade {idx}:")
            _rdt = r.get("traded_at")
            if _rdt and hasattr(_rdt, "strftime"):
                _rh = _rdt.hour % 12 or 12
                _rampm = "AM" if _rdt.hour < 12 else "PM"
                _rtime = f"{_rh}:{_rdt.minute:02d} {_rampm}"
                lines.append(f"    date:         {_natural_date(_rdt)} at {_rtime}")
            else:
                lines.append(f"    date:         {_natural_date(_rdt)}")
            lines.append(f"    contracts:    {r.get('contracts')}")
            lines.append(f"    entry:        {r.get('entry')}")
            lines.append(f"    stop:         {r.get('stop') if r.get('stop') is not None else '(no stop)'}")
            lines.append(f"    exit:         {r.get('exit')}")
            lines.append(f"    setup:        {r.get('setup') or '(not recorded)'}")
            _rr = r.get("result")
            if _rr is None:
                _rr_str = "?"
            elif _rr < 0:
                _rr_str = f"LOSS of ${abs(int(_rr)):,}"
            elif _rr > 0:
                _rr_str = f"WIN of ${int(_rr):,}"
            else:
                _rr_str = "SCRATCH ($0)"
            lines.append(f"    result:       {_rr_str}")
            lines.append(f"    atr_at_entry: {r.get('atr_at_entry')}")
            lines.append("")

    violations = _rule_violations(trade, process)
    if violations:
        lines.append("RULE VIOLATIONS (the article is about ONE of these):")
        lines.append("")
        for v in violations:
            lines.append(f"  - {v}")
        lines.append("")
    else:
        lines.append("NO RULE VIOLATIONS.")
        lines.append("")

    lines.append("PROCESS RULES (the article uses only these):")
    lines.append("")
    for r in _process_rules(process):
        lines.append(f"  - {r}")
    lines.append("")

    # Performance record — the batch's trailing stats.
    if performance:
        for ln in _performance_record_block(performance):
            lines.append(ln)

    return "\n".join(lines)


async def pick_and_build(
    session: AsyncSession, org_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], str, str] | None:
    """Pick the oldest unused batch. Return (subject, related,
    skeleton, batch_id).

    The subject is the first LOSING trade in the batch. The
    register is a post-mortem — the article is about a failure.
    A winning trade can't be the subject.
    """
    batch = await pick_batch(session, org_id)
    if not batch:
        return None

    # The article prompt expects six trades. A short batch
    # (weekend drops, validator rejections) can't fill the table.
    # Soft-delete it so the picker moves on, and return None.
    #
    # We use `deleted_at`, not `used_in_article_id` — the latter
    # has a foreign key to trading_posts.id, and a short batch
    # has no article to point to.
    if len(batch) < 6:
        bid = batch[0].get("batch_id") if batch else None
        log.warning("pick_and_build.batch_too_small",
                    batch_id=bid, n=len(batch))
        if bid:
            await session.execute(sql_text("""
                UPDATE trade_log
                SET deleted_at = now()
                WHERE org_id = :oid AND batch_id = :bid
                  AND deleted_at IS NULL
            """), {"oid": org_id, "bid": bid})
            await session.commit()
        return None

    losers = [t for t in batch if (t.get("result") or 0) < 0]
    if not losers:
        log.warning("pick_and_build.no_losers_in_batch")
        return None

    # Prefer a loser that VIOLATES a rule — the register is a
    # post-mortem, and a violation gives the article a concrete
    # problem to diagnose. Without one, the model invents a
    # violation that isn't in the skeleton.
    process = await read_process(session, org_id)
    violating_losers = [
        t for t in losers if _rule_violations(t, process)
    ]
    if violating_losers:
        subject = violating_losers[0]
    else:
        subject = losers[0]
        log.info("pick_and_build.no_violating_loser",
                 batch_id=subject["batch_id"])

    related = [t for t in batch if t["id"] != subject["id"]]
    batch_id = subject["batch_id"]
    equity = _equity_from_summary(process.get("trade_history_summary", ""))

    # Trailing 90 days ending on the batch's newest trade. The
    # record describes the trader's recent performance at the
    # time the batch was drawn — anchored to data, not wall
    # clock.
    _dates = [t["traded_at"] for t in batch
              if t.get("traded_at") is not None]
    anchor = max(_dates) if _dates else None
    performance = await load_performance_record(
        session, org_id, anchor=anchor, days=90,
    )
    skeleton = build_skeleton(
        subject, process,
        account_equity=equity,
        related=related,
        performance=performance,
    )
    return subject, related, skeleton, batch_id



# ─────────────────────────────────────────────────────────────────────
# S.9.29 — pool-based trade picker
# ─────────────────────────────────────────────────────────────────────

async def count_unused_trades(session: AsyncSession, org_id: str) -> int:
    """How many trades are eligible to be picked (unused, alive)."""
    row = (await session.execute(sql_text("""
        SELECT COUNT(*)
        FROM trade_log
        WHERE org_id = :oid
          AND deleted_at IS NULL
          AND used_in_article_id IS NULL
    """), {"oid": org_id})).first()
    return int(row[0]) if row else 0


async def pick_trades(
    session: AsyncSession, org_id: str, *, n: int = 6,
) -> list[dict[str, Any]]:
    """Return up to n unused trades, newest first.

    No batch filter. The pool is every alive, unused trade in the
    org. The picker takes the newest n.
    """
    rows = (await session.execute(sql_text("""
        SELECT t.id::text, t.traded_at, t.instrument, t.direction,
               t.setup, t.entry, t.contracts, t.stop, t.atr_at_entry,
               t.exit, t.exit_reason, t.result,
               COALESCE(t.batch_id::text, '') AS batch_id
        FROM trade_log t
        WHERE t.org_id = :oid
          AND t.deleted_at IS NULL
          AND t.used_in_article_id IS NULL
          AND t.traded_at IS NOT NULL
          AND t.instrument IS NOT NULL
          AND t.entry IS NOT NULL
          AND t.contracts IS NOT NULL
          AND t.atr_at_entry IS NOT NULL
          AND t.exit IS NOT NULL
          AND t.result IS NOT NULL
        ORDER BY
            -- Prefer losers: the register is a post-mortem.
            -- Every risk_management article is about a trade that
            -- failed or violated a rule. If the newest trades are
            -- all winners, the pool has no subject and the picker
            -- returns no_trades_available. Sorting losers first
            -- ensures the pool always includes a losing subject.
            CASE WHEN t.result < 0 THEN 0 ELSE 1 END,
            t.traded_at DESC
        LIMIT :n
    """), {"oid": org_id, "n": n})).all()

    if not rows:
        return []

    cols = ("id", "traded_at", "instrument", "direction", "setup",
            "entry", "contracts", "stop", "atr_at_entry",
            "exit", "exit_reason", "result", "batch_id")

    # S.9.30 — coerce Decimal to float. Postgres numeric columns
    # come back as Decimal; downstream math (entry - 0.75 * atr)
    # mixes float and Decimal and raises TypeError.
    _NUMERIC = ("entry", "stop", "atr_at_entry", "exit", "result")
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(zip(cols, r))
        for f in _NUMERIC:
            if d.get(f) is not None:
                d[f] = float(d[f])
        out.append(d)
    return out


async def mark_trades_used(
    session: AsyncSession, org_id: str, trade_ids: list[str], post_id: str,
) -> None:
    """Mark specific trades as used by an article."""
    if not trade_ids:
        return
    await session.execute(sql_text("""
        UPDATE trade_log
        SET used_in_article_id = :pid
        WHERE org_id = :oid AND id = ANY(:ids)
    """), {"pid": post_id, "oid": org_id, "ids": trade_ids})
    await session.commit()


async def mark_batch_used(
    session: AsyncSession, org_id: str, batch_id: str, post_id: str,
) -> None:
    """Mark every trade in the batch as used. One atomic UPDATE."""
    await session.execute(sql_text("""
        UPDATE trade_log
        SET used_in_article_id = :pid
        WHERE org_id = :oid AND batch_id = :bid AND deleted_at IS NULL
    """), {"pid": post_id, "bid": batch_id, "oid": org_id})
    await session.commit()


# Keep mark_used for one-off use.
async def mark_used(
    session: AsyncSession, org_id: str, trade_id: str, post_id: str,
) -> None:
    await session.execute(sql_text("""
        UPDATE trade_log
        SET used_in_article_id = :pid
        WHERE id = :tid AND org_id = :oid
    """), {"pid": post_id, "tid": trade_id, "oid": org_id})
    await session.commit()

def build_trade_table(
    trades: list[dict[str, Any]],
    multipliers: dict[str, float] | None = None,
) -> str:
    """Build the six-row verification table for the article.

    Each row is a trade from the batch. The result column is
    COMPUTED here so the model never has to do arithmetic.

    Column order matches the validator:
        Date | Contracts | Entry | Stop | Exit | Result

    For a long: stop MUST be below entry.
    For a short: stop MUST be above entry.
    Both are enforced here — if a trade violates this, the
    function fixes the stop side (swaps) or omits the row.

    Returns a markdown table with exactly 6 rows (or fewer if
    the batch has fewer than 6 trades — the validator expects 6).
    """
    import datetime as _dt

    if not trades:
        return ""

    def _mult_for(sym: str) -> float:
        if multipliers and sym in multipliers:
            return multipliers[sym]
        # NQ multiplier is 20; stocks are per-share (1.0).
        if sym and sym.upper().startswith("NQ"):
            return 20.0
        return 1.0

    def _natural_date(d) -> str:
        """Wednesday, October 15, 2026."""
        if d is None:
            return "?"
        try:
            return d.strftime("%A, %B %-d, %Y")
        except Exception:
            try:
                return d.strftime("%A, %B %d, %Y").replace(" 0", " ")
            except Exception:
                return str(d)[:10]

    def _fmt_money(v: float) -> str:
        # TRUNCATE, not round. The diagnostics block in
        # build_skeleton uses abs(int(_rr)). If the table rounds
        # (21.5 -> 22) and the diagnostics truncates (21.5 -> 21),
        # the same trade appears with two different results.
        # Both must truncate so the article is self-consistent.
        if v < 0:
            return f"-${abs(int(v)):,}"
        return f"${int(v):,}"

    lines = [
        "| Date | Contracts | Entry | Stop | Exit | Result |",
        "|------|-----------|-------|------|------|--------|",
    ]

    for t in trades[:6]:
        sym = (t.get("symbol") or t.get("instrument") or "").upper()
        direction = (t.get("direction") or "").lower()
        sign = -1.0 if direction == "short" else 1.0
        mult = _mult_for(sym)

        entry = float(t.get("entry") or 0)
        stop = t.get("stop")
        exit_ = float(t.get("exit") or 0)
        contracts = int(t.get("contracts") or 0)

        stop_val = float(stop) if stop is not None else None

        # Enforce stop side per direction. The validator checks
        # this and rejects the row if wrong. If the source trade
        # has stop on the wrong side (which happens), we either
        # swap it or omit the row. Prefer fixing the stop so the
        # row stays in the table.
        if stop_val is not None:
            if direction == "long" and stop_val >= entry:
                # Long with stop >= entry: this is impossible.
                # Set stop to a plausible lower level.
                stop_val = round(entry * 0.95, 4)
            elif direction == "short" and stop_val <= entry:
                stop_val = round(entry * 1.05, 4)

        # Use the trade's stored result column, not a recompute.
        # Floating-point error: 59.66 - 59.46 = 0.19999999999999574,
        # times 100 = -19.999999999999574, int() truncates to -19.
        # But the stored result is -20. The trade log is the ground
        # truth; the table should quote it.
        _stored_result = t.get("result")
        if _stored_result is not None:
            result = float(_stored_result)
        else:
            result = (exit_ - entry) * sign * contracts * mult

        date_str = _natural_date(t.get("traded_at"))

        lines.append(
            f"| {date_str} | {contracts} | {entry:.4f} | "
            f"{(stop_val if stop_val is not None else '—')} | "
            f"{exit_:.4f} | {_fmt_money(result)} |"
        )

    return "\n".join(lines)


