"""Distribution block for investment_strategies articles.

Computes a strategy's full sample — win rate, avg win, avg loss,
payoff, expectancy, net P&L — from trade_log. Pulls five recent
trades, the biggest win, and the biggest loss, in full.

Strategy detection: trade_log.setup is a descriptive sentence
like 'Long breakout — close above the prior 10-bar high on X
(+1.5% bar move, entry at Y)'. The strategy type is the prefix.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

# The two strategy types that `ensure_trades_for` writes today.
STRATEGIES = ("long_breakout", "short_breakdown")

STRATEGY_RULES = {
    "long_breakout": (
        "Entry: the close is above the highest close of the prior 10 bars. "
        "Stop: the lower of the prior 5-bar low and entry minus 1 ATR. "
        "Target: entry plus 2 times the risk. "
        "Exit: whichever of stop or target is hit first within 6 bars; "
        "otherwise time-exit at the 6-bar close."
    ),
    "short_breakdown": (
        "Entry: the close is below the lowest close of the prior 10 bars. "
        "Stop: the higher of the prior 5-bar high and entry plus 1 ATR. "
        "Target: entry minus 2 times the risk. "
        "Exit: whichever of stop or target is hit first within 6 bars; "
        "otherwise time-exit at the 6-bar close."
    ),
}

_STRATEGY_CASE = """
  CASE
    WHEN setup LIKE 'Long breakout%' THEN 'long_breakout'
    WHEN setup LIKE 'Short breakdown%' THEN 'short_breakdown'
    ELSE 'other'
  END
"""


def _fmt_money(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "−" if n < 0 else ""
    return f"{sign}${abs(n):,.2f}"


def _fmt_row(r: Any) -> str:
    """Render one trade row as a compact readable paragraph."""
    parts = []
    if r.get("symbol"):
        parts.append(str(r["symbol"]))
    if r.get("timeframe"):
        parts.append(f"({r['timeframe']})")
    direction = r.get("direction") or ""
    entry = r.get("entry")
    exit_ = r.get("exit")
    stop = r.get("stop")
    target = r.get("target")
    result = r.get("result")
    head = " ".join(parts)
    if direction and entry is not None:
        head += f" {direction} at {entry}"
    if exit_ is not None:
        head += f", exit {exit_}"
    if stop is not None:
        head += f", stop {stop}"
    if target is not None:
        head += f", target {target}"
    if result is not None:
        head += f" → {_fmt_money(result)}"
    traded = r.get("traded_at")
    if traded:
        head = f"{traded.strftime('%Y-%m-%d %H:%M')} UTC: {head}"
    return head


async def pick_strategy(
    session: AsyncSession, *, org_id: str, subblock_id: str,
) -> str:
    """Rotate between the two strategies based on article count."""
    row = (await session.execute(sql_text("""
        SELECT COUNT(*) FROM trading_posts
        WHERE subblock_id = :sid AND deleted_at IS NULL
    """), {"sid": subblock_id})).scalar() or 0
    return STRATEGIES[int(row) % len(STRATEGIES)]


async def build_distribution_block(
    session: AsyncSession, *, org_id: str, strategy: str,
) -> str:
    """Return a formatted distribution block for one strategy."""

    agg = (await session.execute(sql_text(f"""
        SELECT
          COUNT(*) AS n,
          COUNT(*) FILTER (WHERE result > 0) AS wins,
          COUNT(*) FILTER (WHERE result <= 0) AS losses,
          AVG(result) FILTER (WHERE result > 0) AS avg_win,
          AVG(result) FILTER (WHERE result <= 0) AS avg_loss,
          SUM(result) AS net_pnl,
          MIN(traded_at) AS oldest,
          MAX(traded_at) AS newest
        FROM trade_log
        WHERE org_id = :oid
          AND deleted_at IS NULL
          AND result IS NOT NULL
          AND ({_STRATEGY_CASE}) = :strat
    """), {"oid": org_id, "strat": strategy})).mappings().first()

    if not agg or not agg["n"]:
        log.warning("investment_strategies.no_trades", strategy=strategy)
        return ""

    n = int(agg["n"])
    wins = int(agg["wins"] or 0)
    losses = int(agg["losses"] or 0)
    avg_win = float(agg["avg_win"]) if agg["avg_win"] is not None else 0.0
    avg_loss = float(agg["avg_loss"]) if agg["avg_loss"] is not None else 0.0
    net_pnl = float(agg["net_pnl"]) if agg["net_pnl"] is not None else 0.0
    win_rate = wins / n if n else 0.0
    payoff = abs(avg_win / avg_loss) if avg_loss else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    recent = (await session.execute(sql_text(f"""
        SELECT symbol, timeframe, direction, entry, stop, target,
               exit, result, traded_at, exit_reason, hold_time
        FROM trade_log
        WHERE org_id = :oid
          AND deleted_at IS NULL
          AND result IS NOT NULL
          AND ({_STRATEGY_CASE}) = :strat
        ORDER BY traded_at DESC
        LIMIT 5
    """), {"oid": org_id, "strat": strategy})).mappings().all()

    biggest_win = (await session.execute(sql_text(f"""
        SELECT symbol, timeframe, direction, entry, stop, target,
               exit, result, traded_at, exit_reason, hold_time
        FROM trade_log
        WHERE org_id = :oid
          AND deleted_at IS NULL
          AND result IS NOT NULL
          AND ({_STRATEGY_CASE}) = :strat
        ORDER BY result DESC
        LIMIT 1
    """), {"oid": org_id, "strat": strategy})).mappings().first()

    biggest_loss = (await session.execute(sql_text(f"""
        SELECT symbol, timeframe, direction, entry, stop, target,
               exit, result, traded_at, exit_reason, hold_time
        FROM trade_log
        WHERE org_id = :oid
          AND deleted_at IS NULL
          AND result IS NOT NULL
          AND ({_STRATEGY_CASE}) = :strat
        ORDER BY result ASC
        LIMIT 1
    """), {"oid": org_id, "strat": strategy})).mappings().first()

    oldest = agg["oldest"].strftime("%Y-%m-%d") if agg["oldest"] else "—"
    newest = agg["newest"].strftime("%Y-%m-%d") if agg["newest"] else "—"

    lines = []
    lines.append("--- STRATEGY ---")
    lines.append(f"Name: {strategy}")
    lines.append(f"Rule: {STRATEGY_RULES.get(strategy, '(no rule text)')}")
    lines.append("")
    lines.append("--- SAMPLE ---")
    lines.append(f"Period: {oldest} to {newest}")
    lines.append(f"Trades: {n}")
    lines.append(f"Wins: {wins}    Losses: {losses}")
    lines.append(f"Win rate: {win_rate * 100:.1f}%")
    lines.append(f"Average win: {_fmt_money(avg_win)}")
    lines.append(f"Average loss: {_fmt_money(avg_loss)}")
    lines.append(f"Payoff ratio: {payoff:.2f}")
    lines.append(f"Expectancy per trade: {_fmt_money(expectancy)}")
    lines.append(f"Net P&L over the sample: {_fmt_money(net_pnl)}")
    lines.append("")
    lines.append("--- 5 MOST RECENT TRADES ---")
    for r in recent:
        lines.append(_fmt_row(dict(r)))
    lines.append("")
    lines.append("--- BIGGEST WIN ---")
    if biggest_win:
        lines.append(_fmt_row(dict(biggest_win)))
    lines.append("")
    lines.append("--- BIGGEST LOSS ---")
    if biggest_loss:
        lines.append(_fmt_row(dict(biggest_loss)))

    return "\n".join(lines)
