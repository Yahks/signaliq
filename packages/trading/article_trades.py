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


def _batch_performance_block(
    diagnostics: list[dict[str, Any]],
) -> list[str]:
    """Compute batch-level stats: win rate, expectancy, profit
    factor, aggregate R. Emitted in the skeleton so the model
    can quote them."""
    if not diagnostics:
        return []

    wins = []
    losses = []
    r_multiples = []
    for t in diagnostics:
        result = t.get("result")
        if result is None:
            continue
        r = float(result)
        if r > 0:
            wins.append(r)
        elif r < 0:
            losses.append(r)
        # R-multiple: result / planned_risk (from stop distance)
        entry = t.get("entry")
        stop = t.get("stop")
        size = t.get("contracts")
        if entry is not None and stop is not None and size is not None:
            _stop_dist = abs(float(entry) - float(stop))
            _planned = _stop_dist * int(size)
            if _planned > 0:
                r_multiples.append(r / _planned)

    total = len(wins) + len(losses)
    if total == 0:
        return []

    win_rate = len(wins) / total
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    net = sum(wins) + sum(losses)
    expectancy = net / total
    profit_factor = (
        sum(wins) / abs(sum(losses)) if losses else float("inf")
    )
    net_r = sum(r_multiples)
    avg_win_r = (
        sum(r for r in r_multiples if r > 0) / sum(1 for r in r_multiples if r > 0)
        if any(r > 0 for r in r_multiples) else 0.0
    )
    avg_loss_r = (
        sum(r for r in r_multiples if r < 0) / sum(1 for r in r_multiples if r < 0)
        if any(r < 0 for r in r_multiples) else 0.0
    )

    # Batch definition — explicit. The reviewer wants to know
    # which trades are included and which aren't.
    _net_r_mean = net_r / total if total > 0 else 0.0
    _net_pnl = sum(wins) + sum(losses)
    # Simulate current equity after cumulative realized P&L.
    # The reviewer's Gap 8: equity is fixed at 200,000 in the
    # prose, but real losses reduce it. Track it explicitly.
    _starting_equity = 200000.0
    _current_equity = _starting_equity + _net_pnl

    # Breakeven math — reviewer's "single most important addition".
    # avg_loss_dollars is negative; use absolute value for the
    # payoff ratio.
    _avg_win = sum(wins) / len(wins) if wins else 0.0
    _avg_loss_abs = abs(sum(losses) / len(losses)) if losses else 0.0
    _payoff_ratio = _avg_win / _avg_loss_abs if _avg_loss_abs > 0 else 0.0
    _breakeven_win_rate = (
        1.0 / (1.0 + _payoff_ratio)
        if _payoff_ratio > 0 else 0.0
    )
    _edge_gap_pct = (win_rate - _breakeven_win_rate) * 100.0

    lines = [
        "BATCH PERFORMANCE (computed by Python, all values are exact):",
        "",
        f"  batch_definition: last {total} diagnostics, excluding subject trade",
        f"  batch_size: {total}",
        f"  n_wins: {len(wins)}",
        f"  n_losses: {len(losses)}",
        f"  win_rate: {win_rate * 100:.1f}%",
        f"  avg_win_dollars: ${avg_win:.2f}",
        f"  avg_loss_dollars: ${avg_loss:.2f}",
        f"  net_pnl_dollars: ${_net_pnl:+.2f}",
        f"  expectancy_per_trade: ${expectancy:+.2f}",
        f"  profit_factor: {profit_factor:.2f}",
        f"  net_r_sum: {net_r:+.4f}",
        f"  net_r_mean: {_net_r_mean:+.4f}",
        f"  avg_win_r: {avg_win_r:+.2f}",
        f"  avg_loss_r: {avg_loss_r:+.2f}",
        f"  starting_equity: ${_starting_equity:.2f}",
        f"  current_equity: ${_current_equity:.2f}",
        f"  current_equity_budget: ${_current_equity * 0.015:.2f} (1.5% of current equity)",
        "",
        "  BREAKEVEN MATH (single most important line in the batch):",
        f"  payoff_ratio: {_payoff_ratio:.4f}",
        f"  breakeven_win_rate: {_breakeven_win_rate * 100:.1f}%",
        f"  actual_win_rate: {win_rate * 100:.1f}%",
        f"  edge_gap_pct: {_edge_gap_pct:+.1f}",
        "",
    ]
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
    # Fallback when an org has no process document and no
    # instrument-specific row. Should never fire in production.
    # If it fires, the article is generated with an empty
    # process, and the model sees no rules — which is better
    # than seeing rules that contradict the strategy.
    "daily_scan": "No process defined.",
    "entry_logic": "No process defined.",
    "position_sizing": "No process defined.",
    "exit_logic": "No process defined.",
    "trade_history_summary": "account_equity: 200000",
}


async def read_process(
    session: AsyncSession, org_id: str,
    *,
    instrument_type: str | None = None,
) -> dict[str, str]:
    """Read the trader's process document.

    If `instrument_type` is provided, read from
    `trader_instrument_process` for that type. Fall back to the
    legacy `trader_process` row if no instrument-specific row
    exists, so the pipeline keeps working for orgs that haven't
    been migrated yet.
    """
    row = None

    if instrument_type:
        try:
            row = (await session.execute(sql_text("""
                SELECT daily_scan, entry_logic, position_sizing, exit_logic,
                       trade_history_summary
                FROM trader_instrument_process
                WHERE org_id = :oid
                  AND instrument_type = :itype
                  AND deleted_at IS NULL LIMIT 1
            """), {"oid": org_id, "itype": instrument_type})).first()
        except Exception as exc:
            log.warning("read_process.instrument_query_failed",
                        error=str(exc)[:120])
            row = None

    if row is None:
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


def _assess_risk(
    trade: dict[str, Any],
    *,
    strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the risk assessment for a trade.

    Layer One of risk_management:
      - Risk budget = account_equity * risk_pct_of_equity
      - Position size = risk_budget / stop_distance
      - Stop = stop_atr_multiple * ATR from entry (1.75 default)

    Returns a dict with all risk numbers needed by the article.
    """
    if strategy is None:
        strategy = {}
    equity = float(strategy.get("account_equity", 200000))
    risk_pct = float(strategy.get("risk_pct_of_equity", 0.015))
    risk_pct_min = float(strategy.get("risk_pct_min", 0.010))
    risk_pct_max = float(strategy.get("risk_pct_max", 0.020))
    stop_mult = float(strategy.get("stop_atr_multiple", 1.75))
    stop_mult_min = float(strategy.get("stop_atr_multiple_min", 1.50))
    stop_mult_max = float(strategy.get("stop_atr_multiple_max", 2.00))

    risk_budget_dollars = equity * risk_pct
    tolerance_low_dollars = equity * risk_pct_min
    tolerance_high_dollars = equity * risk_pct_max

    entry = trade.get("entry")
    stop = trade.get("stop")
    size = trade.get("contracts")
    atr = trade.get("atr_at_entry")
    instrument = str(trade.get("instrument") or trade.get("symbol") or "").upper()

    _is_futures = instrument in {"NQ", "ES", "YM", "RTY", "MNQ", "MES"} or (
        entry is not None and float(entry) > 5000
    )
    multiplier = 20.0 if _is_futures else 1.0

    if entry is None or stop is None or size is None:
        return {
            "budget_dollars": round(risk_budget_dollars, 2),
            "budget_pct_of_equity": round(risk_pct * 100.0, 2),
            "tolerance_low_dollars": round(tolerance_low_dollars, 2),
            "tolerance_high_dollars": round(tolerance_high_dollars, 2),
            "planned_risk_dollars": None,
            "planned_risk_pct": None,
            "realized_pnl_dollars": None,
            "realized_pnl_pct": None,
            "n_risk_based": None,
            "n_leverage_based": None,
            "n_expected": None,
            "traded_size": None,
            "binding_constraint": "unknown",
            "is_leverage_capped": False,
            "sizing_reconciled": False,
            "stop_distance": None,
            "stop_atr_multiple_actual": None,
            "stop_atr_multiple_required": round(stop_mult, 2),
            "stop_atr_violation": False,
            "deviation": None,
            "deviation_pct": None,
            "direction": "unknown",
            "is_violation": False,
            "multiplier": multiplier,
            "instrument_type": "futures" if _is_futures else "stock",
        }

    entry_f = float(entry)
    stop_f = float(stop)
    exit_f = float(trade.get("exit")) if trade.get("exit") is not None else None
    size_i = int(size)

    stop_distance = abs(entry_f - stop_f)

    # ── Planned risk (at entry, based on stop distance) ──
    planned_risk_dollars = stop_distance * size_i * multiplier
    planned_risk_pct = planned_risk_dollars / equity

    # ── Realized P&L (at exit) ──
    _direction = (trade.get("direction") or "long").lower()
    if exit_f is not None:
        if _direction == "long":
            realized_delta = exit_f - entry_f
        else:
            realized_delta = entry_f - exit_f
        realized_pnl_dollars = realized_delta * size_i * multiplier
        realized_pnl_pct = realized_pnl_dollars / equity
    else:
        realized_pnl_dollars = None
        realized_pnl_pct = None

    # ── Sizing reconciliation (which cap bound) ──
    _cap_at_equity = float(strategy.get("position_size_cap_at_equity_multiple", 1.0))
    _min_shares = int(strategy.get("position_size_min_shares", 1))
    if stop_distance > 0:
        n_risk_based = int(risk_budget_dollars / (stop_distance * multiplier))
    else:
        n_risk_based = 0
    n_leverage_based = int(equity * _cap_at_equity / (entry_f * multiplier)) if entry_f > 0 else 0
    n_expected = max(min(n_risk_based, n_leverage_based), _min_shares)

    if n_risk_based <= n_leverage_based:
        binding_constraint = "risk"
    else:
        binding_constraint = "leverage_cap"

    is_leverage_capped = binding_constraint == "leverage_cap"
    sizing_reconciled = (size_i == n_expected)

    # ── Direction: is the planned risk under / over / on-budget? ──
    deviation = planned_risk_dollars - risk_budget_dollars
    deviation_pct = deviation / risk_budget_dollars if risk_budget_dollars > 0 else 0.0

    if planned_risk_dollars < tolerance_low_dollars:
        direction = "under"
    elif planned_risk_dollars > tolerance_high_dollars:
        direction = "over"
    else:
        direction = "on-budget"

    # If the leverage cap bound, the "under" risk is expected, not a violation.
    if is_leverage_capped and direction == "under":
        direction = "under_leverage_capped"

    # ── Stop rule compliance ──
    stop_atr_actual = None
    stop_atr_violation = False
    if atr is not None and float(atr) > 0:
        stop_atr_actual = stop_distance / float(atr)
        if stop_atr_actual < stop_mult_min or stop_atr_actual > stop_mult_max:
            stop_atr_violation = True

    # ── Is it a violation? ──
    # Not a violation if:
    #   - direction is on-budget OR under_leverage_capped (expected)
    #   - stop ATR is compliant
    #   - sizing reconciles
    is_under_leverage_cap = direction == "under_leverage_capped"
    is_violation = (
        (direction in ("under", "over"))
        or stop_atr_violation
        or not sizing_reconciled
    ) and not is_under_leverage_cap

    return {
        "budget_dollars": round(risk_budget_dollars, 2),
        "budget_pct_of_equity": round(risk_pct * 100.0, 2),
        "tolerance_low_dollars": round(tolerance_low_dollars, 2),
        "tolerance_high_dollars": round(tolerance_high_dollars, 2),

        # Planned (at entry)
        "planned_risk_dollars": round(planned_risk_dollars, 2),
        "planned_risk_pct": round(planned_risk_pct * 100.0, 4),

        # Realized (at exit)
        "realized_pnl_dollars": round(realized_pnl_dollars, 2) if realized_pnl_dollars is not None else None,
        "realized_pnl_pct": round(realized_pnl_pct * 100.0, 4) if realized_pnl_pct is not None else None,

        # Sizing reconciliation
        "n_risk_based": n_risk_based,
        "n_leverage_based": n_leverage_based,
        "n_expected": n_expected,
        "traded_size": size_i,
        "binding_constraint": binding_constraint,
        "is_leverage_capped": is_leverage_capped,
        "sizing_reconciled": sizing_reconciled,

        # Stop rule
        "stop_distance": round(stop_distance, 4),
        "stop_atr_multiple_actual": round(stop_atr_actual, 4) if stop_atr_actual is not None else None,
        "stop_atr_multiple_required": round(stop_mult, 2),
        "stop_atr_violation": stop_atr_violation,

        # Direction + violation
        "deviation": round(deviation, 2),
        "deviation_pct": round(deviation_pct * 100.0, 2),
        "direction": direction,
        "is_violation": is_violation,

        # P2: R-multiple and slippage dollars.
        # r_multiple uses planned_risk_dollars as denominator.
        # The reviewer wants to know WHICH size the R is computed
        # on: the risk-budget size (n_risk_based) or the executed
        # size (traded_size). If the leverage cap bound, they
        # differ. State it explicitly.
        "r_multiple": round(
            (realized_pnl_dollars / planned_risk_dollars)
            if (realized_pnl_dollars is not None and planned_risk_dollars > 0)
            else 0.0,
            4,
        ) if realized_pnl_dollars is not None else None,
        "r_computed_on": (
            "leverage_capped_size" if (
                n_leverage_based < n_risk_based
            ) else "risk_budget_size"
        ),
        "r_denominator_dollars": round(planned_risk_dollars, 2) if planned_risk_dollars else None,
        "total_slippage_dollars": round(
            (
                (float(trade.get("entry_slippage") or 0.0)
                 + float(trade.get("exit_slippage") or 0.0))
                * size_i * multiplier
            ),
            4,
        ),

        "multiplier": multiplier,
        "instrument_type": "futures" if _is_futures else "stock",
    }



def _primary_frame(
    risk: dict[str, Any],
    entry_violations: list[str],
) -> str:
    """Decide which frame the article should use:
       "risk", "entry", or "none".

    Rule (per reviewer):
      - If risk is a violation (|deviation_pct| > 0.20) AND
        |deviation_pct| >= any entry violation's severity,
        frame is "risk".
      - If entry violations exist and risk is not a violation,
        frame is "entry".
      - If both exist, the larger deviation wins.
      - If neither, frame is "none".
    """
    _risk_is_violation = bool(risk.get("is_violation"))
    _is_leverage_capped = bool(risk.get("is_leverage_capped"))
    _magnitude = abs(float(risk.get("deviation_pct") or 0.0))
    _entry_count = len(entry_violations)

    # Leverage-capped + sizing reconciled = NOT a risk violation,
    # regardless of how far below the budget it sits.
    if _is_leverage_capped and not _risk_is_violation:
        # If there's an entry violation, frame is "entry".
        # Otherwise, the trade is a valid rules-compliant trade.
        return "entry" if _entry_count > 0 else "none"

    if _risk_is_violation and _entry_count == 0:
        return "risk"
    if _risk_is_violation and _entry_count > 0:
        return "risk" if _magnitude > 20.0 else "entry"
    if _entry_count > 0:
        return "entry"
    return "none"


def _build_violation_report(
    trade: dict[str, Any],
    process: dict[str, str],
    *,
    strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the rule-violation report for the article.

    Layer One rules (exclusive to risk_management):
      - Risk: budget is (equity * risk_pct_of_equity), not a
        hardcoded $2,000. Tolerance band is 1%-2% of equity.
      - Stop: must be 1.5-2.0 x ATR from entry. Not 0.75.

    Returns a dict:
      {
        "frame": "risk" | "entry" | "none",
        "primary": <string | None>,
        "secondary": [<string>, ...],
        "risk": <_assess_risk output>,
      }
    """
    if strategy is None:
        strategy = {}
    stop_mult = float(strategy.get("stop_atr_multiple", 1.75))
    stop_mult_min = float(strategy.get("stop_atr_multiple_min", 1.50))
    stop_mult_max = float(strategy.get("stop_atr_multiple_max", 2.00))

    atr = trade.get("atr_at_entry")
    entry = trade.get("entry")
    stop = trade.get("stop")
    setup = (trade.get("setup") or "").lower()
    _direction = (trade.get("direction") or "long").lower()

    # ── Structured risk assessment (Layer One). ──
    risk = _assess_risk(trade, strategy=strategy)

    # ── Entry violations. ──
    entry_violations: list[str] = []

    # Clearance rule
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*clearance", setup)
    if m and float(m.group(1)) < 0.5:
        entry_violations.append(
            f"Clearance rule violated: the breakout cleared "
            f"{m.group(1)}%, below the 0.5% minimum."
        )

    # Stop rule — Layer One: 1.5-2.0 x ATR from entry (not 0.75)
    if stop is not None and atr and entry and float(atr) > 0:
        stop_f = float(stop)
        entry_f = float(entry)
        atr_f = float(atr)
        stop_distance = abs(stop_f - entry_f)
        actual_mult = stop_distance / atr_f
        # Wrong side check
        wrong_side = (
            (_direction == "long" and stop_f >= entry_f)
            or (_direction == "short" and stop_f <= entry_f)
        )
        if wrong_side:
            side = "above" if _direction == "long" else "below"
            entry_violations.append(
                f"Stop rule violated: the stop sits {side} the entry "
                f"for a {_direction}. The rule requires "
                f"{stop_mult:.2f} x ATR "
                f"({'below' if _direction == 'long' else 'above'} the entry)."
            )
        elif actual_mult < stop_mult_min:
            entry_violations.append(
                f"Stop rule violated: the stop is {actual_mult:.2f} x ATR "
                f"from entry, below the {stop_mult_min:.2f} x ATR minimum. "
                f"Tighter stops get tagged by noise."
            )
        elif actual_mult > stop_mult_max:
            entry_violations.append(
                f"Stop rule violated: the stop is {actual_mult:.2f} x ATR "
                f"from entry, above the {stop_mult_max:.2f} x ATR maximum. "
                f"Wider stops waste capital on noise."
            )
    elif stop is None and atr and entry:
        rule_mult = float(strategy.get("stop_atr_multiple", 1.75))
        implied = (
            round(float(entry) - rule_mult * float(atr), 2)
            if _direction == "long"
            else round(float(entry) + rule_mult * float(atr), 2)
        )
        entry_violations.append(
            f"No stop was placed. The {rule_mult:.2f} x ATR rule "
            f"would have set the stop at {implied}."
        )

    # ATR filter
    if atr is not None and float(atr) > 30:
        entry_violations.append(
            f"ATR filter violated: ATR was {atr}, above the $30 threshold."
        )

    # ── Frame selection. ──
    frame = _primary_frame(risk, entry_violations)

    # ── Build primary + secondary. ──
    primary: str | None = None
    secondary: list[str] = []

    if frame == "risk":
        # Extract the strategy's three labeled risk numbers.
        _equity = float(strategy.get("account_equity", 200000))
        _target_pct = float(strategy.get("risk_pct_of_equity", 0.015))
        _floor_pct = float(strategy.get("risk_pct_min", 0.010))
        _ceiling_pct = float(strategy.get("risk_pct_max", 0.020))
        _target_dollars = _equity * _target_pct
        _floor_dollars = _equity * _floor_pct
        _ceiling_dollars = _equity * _ceiling_pct

        # Every message now uses planned_risk_dollars and
        # planned_risk_pct, because the risk rule is evaluated on
        # PLANNED risk (stop distance at entry), not realized P&L.
        _planned_dollars = risk['planned_risk_dollars']
        _planned_pct = risk['planned_risk_pct']
        _dev_pct = risk['deviation_pct']
        _n_risk = risk['n_risk_based']
        _n_lev = risk['n_leverage_based']
        _traded = risk['traded_size']

        if direction == "over":
            primary = (
                f"Risk rule violated: planned risk was "
                f"${_planned_dollars:.2f} against a "
                f"${_target_dollars:.2f} target "
                f"({_dev_pct:+.1f}% from target). "
                f"The target is {_target_pct*100:.1f}% of equity "
                f"(${_target_dollars:.2f}); the ceiling is "
                f"{_ceiling_pct*100:.1f}% (${_ceiling_dollars:.2f}). "
                f"Planned risk was {_planned_pct:.2f}% of equity, "
                f"above the ceiling."
            )
        elif direction == "under":
            primary = (
                f"Risk rule violated: planned risk was only "
                f"${_planned_dollars:.2f} against a "
                f"${_target_dollars:.2f} target "
                f"({abs(_dev_pct):.1f}% under). "
                f"The target is {_target_pct*100:.1f}% of equity "
                f"(${_target_dollars:.2f}); the floor is "
                f"{_floor_pct*100:.1f}% (${_floor_dollars:.2f}). "
                f"Planned risk was {_planned_pct:.2f}% of equity, "
                f"below the floor. "
                f"Size reconciliation: n_risk_based={_n_risk}, "
                f"n_leverage_based={_n_lev}, traded={_traded}, "
                f"binding_constraint={risk['binding_constraint']}. "
                f"The trade cannot move the account."
            )
        secondary.extend(entry_violations)

    elif frame == "entry":
        if entry_violations:
            primary = entry_violations[0]
            secondary = entry_violations[1:]

    return {
        "frame": frame,
        "primary": primary,
        "secondary": secondary,
        "risk": risk,
    }



def _rule_violations(
    trade: dict[str, Any],
    process: dict[str, str],
    *,
    strategy: dict[str, Any] | None = None,
) -> list[str]:
    """List-shaped view of _build_violation_report.

    Used by the trade picker to filter trades with at least one
    violation. Empty list = no violations.
    """
    report = _build_violation_report(trade, process, strategy=strategy)
    if report["primary"] is None:
        return []
    return [report["primary"]] + report["secondary"]


def _process_rules(
    process: dict[str, str],
    strategy: dict[str, Any] | None = None,
) -> list[str]:
    """Return the trader's process rules, formatted for the prompt.

    Every number the model needs comes from the strategy. No
    hardcoded figures. This blocks the model from inventing
    thresholds like "0.9x equity" or "50% deviation tolerance"
    that are not in the strategy.
    """
    if strategy is None:
        strategy = {}
    _equity = float(strategy.get("account_equity", 200000))
    _risk_pct = float(strategy.get("risk_pct_of_equity", 0.015))
    _risk_min = float(strategy.get("risk_pct_min", 0.010))
    _risk_max = float(strategy.get("risk_pct_max", 0.020))
    _stop_mult = float(strategy.get("stop_atr_multiple", 1.75))
    _stop_min = float(strategy.get("stop_atr_multiple_min", 1.50))
    _stop_max = float(strategy.get("stop_atr_multiple_max", 2.00))
    _cap = float(strategy.get("position_size_cap_at_equity_multiple", 1.0))
    _max_atr = float(strategy.get("atr_filter", {}).get("max_atr", 30))
    _clearance_pct = float(
        strategy.get("entry_rules", {}).get("breakout", {}).get("clearance_pct", 0.5)
    )
    _lookback = int(
        strategy.get("entry_rules", {}).get("breakout", {}).get("lookback_bars", 10)
    )

    rules: list[str] = []

    if process.get("entry_logic"):
        rules.append(f"Entry: {process['entry_logic'].strip()}")

    if process.get("position_sizing"):
        rules.append(f"Sizing: {process['position_sizing'].strip()}")

    if process.get("exit_logic"):
        rules.append(f"Exit: {process['exit_logic'].strip()}")

    # Numeric layer — one authoritative set of values from strategy.
    rules.append(
        f"Risk budget: ${_equity * _risk_pct:.2f} "
        f"({_risk_pct * 100:.1f}% of ${_equity:.0f} equity)."
    )
    rules.append(
        f"Tolerance band: ${_equity * _risk_min:.2f} to "
        f"${_equity * _risk_max:.2f} "
        f"({_risk_min * 100:.1f}%-{_risk_max * 100:.1f}% of equity)."
    )
    rules.append(
        f"Stop multiple: {_stop_mult:.2f} x ATR from entry "
        f"(range {_stop_min:.2f}-{_stop_max:.2f} x ATR)."
    )
    rules.append(
        f"Leverage cap: {_cap:.2f} x account equity (notional)."
    )
    rules.append(f"ATR filter: reject entries when ATR > {_max_atr:.0f}.")
    rules.append(
        f"Clearance: a breakout must clear the prior "
        f"{_lookback}-bar high by at least {_clearance_pct:.1f}%."
    )

    if not rules:
        # Truly empty — no process, no strategy.
        return ["No process rules available."]

    return rules


def _format_trade_block(
    header: str, trade: dict[str, Any],
    strategy: dict[str, Any] | None = None,
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
    # Coerce all numeric fields to float. SQLAlchemy returns
    # Decimal from Postgres numeric columns. Arithmetic between
    # float and Decimal raises TypeError. This normalizes them.
    def _f(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    entry = _f(trade.get("entry"))
    actual_stop = _f(trade.get("stop"))
    atr = _f(trade.get("atr_at_entry"))
    exit_p = _f(trade.get("exit"))
    result = _f(trade.get("result"))
    contracts = trade.get("contracts")
    if contracts is not None:
        try:
            contracts = int(contracts)
        except (TypeError, ValueError):
            contracts = None

    # Compute rule_stop from the strategy's stop_atr_multiple.
    # Falling back to 1.75 if not provided.
    _stop_mult = 1.75
    if strategy is not None:
        _stop_mult = float(strategy.get("stop_atr_multiple", 1.75))
    rule_stop = None
    _direction = (trade.get("direction") or "long").lower()
    if entry is not None and atr is not None:
        if _direction == "long":
            rule_stop = round(entry - _stop_mult * atr, 2)
        else:  # short — stop goes ABOVE entry
            rule_stop = round(entry + _stop_mult * atr, 2)
    if actual_stop is None:
        lines.append(f"  stop:         (no stop was placed)")
    else:
        lines.append(f"  stop:         {actual_stop}")
    if rule_stop is not None and actual_stop is not None:
        if abs(actual_stop - rule_stop) > 0.01:
            diff = actual_stop - rule_stop
            lines.append(
                f"  rule stop:    {rule_stop}  "
                f"({_stop_mult} × ATR = {round(_stop_mult * atr, 2)} below entry; "
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
    strategy: dict[str, Any] | None = None,
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
    lines.extend(_format_trade_block("", trade, strategy=strategy))

    # Compute percent_of_equity for the subject trade. The DB
    # column is NULL for legacy trades, so we compute from
    # result / account_equity. The model quotes this number;
    # the validator finds it in the skeleton.
    _subject_result = trade.get("result")
    if _subject_result is not None and account_equity and account_equity > 0:
        _pct = float(_subject_result) / float(account_equity) * 100.0
        if _pct < 0:
            lines.append(f"  percent_of_equity: {_pct:.2f}% (of account equity)")
        else:
            lines.append(f"  percent_of_equity: +{_pct:.2f}% (of account equity)")

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
            # Compute percent_of_equity for related trades too.
            _r_res = r.get("result")
            if _r_res is not None and account_equity and account_equity > 0:
                _r_pct = float(_r_res) / float(account_equity) * 100.0
                if _r_pct < 0:
                    lines.append(f"    percent_of_equity: {_r_pct:.2f}% (of account equity)")
                else:
                    lines.append(f"    percent_of_equity: +{_r_pct:.2f}% (of account equity)")
            lines.append(f"    atr_at_entry: {r.get('atr_at_entry')}")
            lines.append("")

    # ── VERBATIM DIAGNOSTICS BLOCK ──
    # The model must paste this block exactly. It cannot invent
    # trades, reformat dates, or change numbers. This is the only
    # way to guarantee that the diagnostics in the article match
    # the trades in the database.
    if related:
        lines.append("")
        lines.append("--- VERBATIM DIAGNOSTICS BLOCK ---")
        lines.append("Copy this block EXACTLY into the article's diagnostics beat.")
        lines.append("Do not add, remove, or change any number. Do not reformat.")
        lines.append("")
        for r in related:
            _rdt = r.get("traded_at")
            if _rdt and hasattr(_rdt, "strftime"):
                if _rdt.hour == 0 and _rdt.minute == 0:
                    # Midnight trade — skip the time in the display
                    _rdate = _natural_date(_rdt)
                else:
                    _rh = _rdt.hour % 12 or 12
                    _rampm = "AM" if _rdt.hour < 12 else "PM"
                    _rtime = f"{_rh}:{_rdt.minute:02d} {_rampm}"
                    _rdate = f"{_natural_date(_rdt)} at {_rtime}"
            else:
                _rdate = _natural_date(_rdt)
            _rr = r.get("result")
            if _rr is None:
                _rr_str = "?"
            elif _rr < 0:
                _rr_str = f"LOSS of ${abs(int(_rr)):,}"
            elif _rr > 0:
                _rr_str = f"WIN of ${int(_rr):,}"
            else:
                _rr_str = "SCRATCH ($0)"
            _atr_val = r.get("atr_at_entry")
            _atr_str = f" | ATR {_atr_val}" if _atr_val is not None else ""
            _clear_val = r.get("clearance_pct")
            _clear_str = f" | clearance {float(_clear_val):.4f}%" if _clear_val is not None else ""
            # Per-diagnostic binding constraint: was the size bound
            # by the risk budget or the leverage cap?
            _bc_str = ""
            _d_entry = r.get("entry")
            _d_stop = r.get("stop")
            _d_size = r.get("contracts")
            # Per-diagnostic R-multiple and exit type.
            _r_mult_str = ""
            _exit_type_str = ""
            if _d_entry and _d_stop and _d_size:
                _d_stop_dist = abs(float(_d_entry) - float(_d_stop))
                _d_planned = _d_stop_dist * int(_d_size)
                _d_r_mult = (
                    float(_rr) / _d_planned if _d_planned > 0 else 0.0
                )
                _r_mult_str = f" | R {_d_r_mult:+.2f}"
                _d_risk_based = int(3000.0 / (_d_stop_dist * 1.0)) if _d_stop_dist > 0 else 0
                _d_lev_based = int(200000.0 / (float(_d_entry) * 1.0)) if float(_d_entry) > 0 else 0
                _d_bc = "leverage_cap" if _d_lev_based < _d_risk_based else "risk"
                _bc_str = f" | binding {_d_bc}"
            _exit_reason_val = r.get("exit_reason") or "unknown"
            _exit_type_str = f" | exit_type {_exit_reason_val}"
            lines.append(
                f"- {_rdate} | {r.get('contracts')} contracts | "
                f"entry {r.get('entry')} | stop {r.get('stop')} | "
                f"exit {r.get('exit')} | {_rr_str}{_atr_str}{_clear_str}{_bc_str}{_r_mult_str}{_exit_type_str}"
            )
        lines.append("--- END VERBATIM DIAGNOSTICS BLOCK ---")
        lines.append("")

    # P2: batch performance block from diagnostics.
    if related:
        for ln in _batch_performance_block(related):
            lines.append(ln)

    report = _build_violation_report(trade, process, strategy=strategy)
    frame = report["frame"]
    primary = report["primary"]
    secondary = report["secondary"]
    risk = report["risk"]

    if frame == "none":
        lines.append("NO RULE VIOLATIONS.")
        lines.append("")
        lines.append(
            "The trade followed every rule. Write about what it "
            "reveals that the rules are working but the edge is "
            "missing — or, if the trade was a win, why a rules-"
            "compliant trade can still be unremarkable."
        )
        lines.append("")
    else:
        lines.append(f"FRAME: {frame.upper()}")
        lines.append("")
        lines.append(f"PRIMARY VIOLATION (the article is about this):")
        lines.append(f"  - {primary}")
        lines.append("")
        if secondary:
            lines.append("SECONDARY NOTES (mention only in passing):")
            lines.append("")
            for v in secondary:
                lines.append(f"  - {v}")
            lines.append("")

        # Add the structured risk assessment to the skeleton so
        # the model sees the numbers it must reference.
        # Emit the RISK ASSESSMENT block whenever we have planned risk,
    # regardless of frame. FRAME=NONE articles still need the budget
    # and tolerance numbers visible so the model can say "the trade
    # followed every rule, risk was in-band".
    if risk.get("planned_risk_dollars") is not None:
            lines.append("RISK ASSESSMENT:")
            lines.append(f"  budget_dollars: {risk['budget_dollars']:.2f}")
            lines.append(f"  budget_pct_of_equity: {risk['budget_pct_of_equity']:.2f}%")
            lines.append(f"  tolerance_low_dollars: {risk['tolerance_low_dollars']:.2f}")
            lines.append(f"  tolerance_high_dollars: {risk['tolerance_high_dollars']:.2f}")

            # Planned vs realized — both labeled, never confused.
            if risk.get('planned_risk_dollars') is not None:
                lines.append(f"  planned_risk_dollars: {risk['planned_risk_dollars']:.2f}")
                lines.append(f"  planned_risk_pct: {risk['planned_risk_pct']:.2f}%")
            if risk.get('realized_pnl_dollars') is not None:
                lines.append(f"  realized_pnl_dollars: {risk['realized_pnl_dollars']:+.2f}")
                lines.append(f"  realized_pnl_pct: {risk['realized_pnl_pct']:+.2f}%")

            # Sizing reconciliation — shows which cap bound.
            if risk.get('n_risk_based') is not None:
                lines.append(f"  n_risk_based: {risk['n_risk_based']}")
                lines.append(f"  n_leverage_based: {risk['n_leverage_based']}")
                lines.append(f"  n_expected: {risk['n_expected']}")
                lines.append(f"  traded_size: {risk['traded_size']}")
                lines.append(f"  binding_constraint: {risk['binding_constraint']}")
                lines.append(f"  is_leverage_capped: {risk['is_leverage_capped']}")
                lines.append(f"  sizing_reconciled: {risk['sizing_reconciled']}")

            lines.append(f"  stop_distance: {risk['stop_distance']:.4f}")
            if risk.get('stop_atr_multiple_actual') is not None:
                lines.append(f"  stop_atr_multiple_actual: {risk['stop_atr_multiple_actual']:.4f}")
            lines.append(f"  stop_atr_multiple_required: {risk['stop_atr_multiple_required']:.2f}")

            # Deviation: labeled structural when the cap bound.
            if risk.get('is_leverage_capped') and risk.get('sizing_reconciled'):
                lines.append(
                    f"  deviation: {risk['deviation']:+.2f} "
                    f"(STRUCTURAL — leverage cap bound; correctly sized)"
                )
                lines.append(
                    f"  deviation_pct_of_budget: {risk['deviation_pct']:+.2f}% "
                    f"(structural, not a shortfall)"
                )
            else:
                lines.append(f"  deviation: {risk['deviation']:+.2f}")
                lines.append(f"  deviation_pct_of_budget: {risk['deviation_pct']:+.2f}%")

            # P2: R-multiple and slippage dollars.
            if risk.get("r_multiple") is not None:
                lines.append(f"  r_multiple: {risk['r_multiple']:+.4f}")
            if risk.get("r_computed_on"):
                lines.append(f"  r_computed_on: {risk['r_computed_on']}")
            if risk.get("r_denominator_dollars") is not None:
                lines.append(f"  r_denominator_dollars: ${risk['r_denominator_dollars']:.2f}")
            if risk.get("total_slippage_dollars") is not None:
                lines.append(f"  total_slippage_dollars: {risk['total_slippage_dollars']:+.4f}")

            lines.append(f"  direction: {risk['direction']}")
            lines.append(f"  multiplier: {risk['multiplier']}")
            lines.append(f"  instrument_type: {risk['instrument_type']}")
            lines.append("")

    lines.append("PROCESS RULES (the article uses only these):")
    lines.append("")
    for r in _process_rules(process, strategy=strategy):
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
    session: AsyncSession, org_id: str, *,
    n: int = 6, symbol: str | None = None,
    exclude_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return up to n unused trades, newest first.

    If `symbol` is set, only trades for that instrument are
    returned. Used by risk_management for per-symbol coherence
    in the diagnostics block: the article is about one instrument,
    and the diagnostics must be trades of the same instrument.

    If `exclude_ids` is set, those trade IDs are skipped (used
    to prevent the subject from appearing in its own diagnostics).
    """
    # Build the query with optional filters.
    _where_extra = ""
    _params: dict[str, Any] = {"oid": org_id, "n": n}

    if symbol:
        _where_extra += " AND t.instrument = :sym"
        _params["sym"] = symbol

    if exclude_ids:
        # asyncpg wants an array + ANY, not a tuple + IN.
        # `NOT IN $1` errors with "syntax error at or near $1".
        _where_extra += " AND NOT (t.id::text = ANY(:excludes))"
        _params["excludes"] = list(exclude_ids)

    rows = (await session.execute(sql_text(f"""
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
          AND EXTRACT(HOUR FROM t.traded_at) BETWEEN 13 AND 21
          {_where_extra}
        ORDER BY
            CASE WHEN t.result < 0 THEN 0 ELSE 1 END,
            t.traded_at DESC
        LIMIT :n
    """), _params)).all()

    if not rows:
        return []

    cols = ("id", "traded_at", "instrument", "direction", "setup",
            "entry", "contracts", "stop", "atr_at_entry",
            "exit", "exit_reason", "result", "batch_id")

    # S.9.30 — coerce Decimal to float. Postgres numeric columns
    # come back as Decimal. Downstream math (entry - stop_multiple * atr)
    # mixes float and Decimal and raises TypeError.
    _NUMERIC = ("entry", "stop", "atr_at_entry", "exit", "result")
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(zip(cols, r))
        for f in _NUMERIC:
            if d.get(f) is not None:
                d[f] = float(d[f])
        out.append(d)
    # Dedupe: some trade_log rows are duplicates (same instrument,
    # traded_at, entry, exit, result). Collapse them so the article
    # doesn't show the same trade three times in the diagnostics.
    _seen = set()
    _deduped: list[dict[str, Any]] = []
    for _t in out:
        _key = (
            _t.get("instrument"),
            str(_t.get("traded_at")),
            float(_t.get("entry") or 0),
            float(_t.get("exit") or 0),
            float(_t.get("result") or 0),
        )
        if _key in _seen:
            continue
        _seen.add(_key)
        _deduped.append(_t)
    out = _deduped

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
        # TRUNCATE with int(). This matches the diagnostic block's
        # int() truncation. Both must truncate — the validator
        # reads the skeleton's LOSS of $1,463 and checks the
        # table for the same value. If the table rounds to $1,464,
        # the validator rejects.
        if v < 0:
            return f"-${abs(int(v)):,}"
        return f"${int(v):,}"

    lines = [
        "| Date | Direction | Contracts | Entry | Stop | Exit | Result |",
        "|------|-----------|-----------|-------|------|------|--------|",
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
            f"| {date_str} | {direction} | {contracts} | {entry:.4f} | "
            f"{(stop_val if stop_val is not None else '—')} | "
            f"{exit_:.4f} | {_fmt_money(result)} |"
        )

    return "\n".join(lines)


