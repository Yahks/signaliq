"""Account equity curve for the market_analysis subblock.

Reads trade_log and computes the account's balance over time.
No starting-balance column exists in the schema, so the caller
supplies one. Default is 200,000.

Returns a dict that format_account_block turns into a paragraph
and a small table. The block is inserted into the article in
code, not written by the model.
"""
from __future__ import annotations

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_STARTING_BALANCE = 200_000.0
WEEKLY_WINDOW = 8


def _fmt_money(v: float) -> str:
    """Format a dollar amount with commas and no cents."""
    return f"${v:,.0f}"


async def summarize_account_equity(
    session: AsyncSession,
    *,
    org_id: str,
    starting_balance: float = DEFAULT_STARTING_BALANCE,
    weekly_window: int = WEEKLY_WINDOW,
) -> dict | None:
    """Compute the account equity curve.

    Returns None if there are no trades. Otherwise a dict with:
      starting_balance, current_balance, ytd_change, ytd_pct,
      peak_balance, current_drawdown, current_drawdown_pct,
      last_5_result, weekly_curve, trade_count
    """
    # 1. Total P&L and trade count.
    tot = (await session.execute(sql_text("""
        SELECT COUNT(*) AS n, COALESCE(SUM(result), 0) AS pnl
        FROM trade_log
        WHERE org_id = :oid AND deleted_at IS NULL
    """), {"oid": org_id})).mappings().first()
    if not tot or tot["n"] == 0:
        return None

    total_pnl = float(tot["pnl"])
    trade_count = int(tot["n"])
    current_balance = starting_balance + total_pnl

    # 2. Peak balance over all time (running max of cumulative P&L).
    peak_row = (await session.execute(sql_text("""
        SELECT MAX(running_total) AS peak
        FROM (
            SELECT SUM(result) OVER (ORDER BY traded_at, id) AS running_total
            FROM trade_log
            WHERE org_id = :oid AND deleted_at IS NULL
        ) t
    """), {"oid": org_id})).mappings().first()
    peak_pnl = float(peak_row["peak"]) if peak_row and peak_row["peak"] is not None else 0.0
    peak_balance = starting_balance + peak_pnl

    current_drawdown = current_balance - peak_balance
    current_drawdown_pct = (
        (current_drawdown / peak_balance * 100.0) if peak_balance else 0.0
    )

    # 3. Last 5 trades total.
    last5_row = (await session.execute(sql_text("""
        SELECT COALESCE(SUM(result), 0) AS pnl
        FROM (
            SELECT result FROM trade_log
            WHERE org_id = :oid AND deleted_at IS NULL
            ORDER BY traded_at DESC, id DESC
            LIMIT 5
        ) t
    """), {"oid": org_id})).mappings().first()
    last_5_result = float(last5_row["pnl"]) if last5_row else 0.0

    # 4. Weekly equity curve — last N weeks.
    weekly_rows = (await session.execute(sql_text("""
        SELECT week_end, week_pnl FROM (
            SELECT
                DATE_TRUNC('week', traded_at)::date AS week_end,
                COALESCE(SUM(result), 0) AS week_pnl
            FROM trade_log
            WHERE org_id = :oid
              AND deleted_at IS NULL
            GROUP BY 1
            ORDER BY 1 DESC
            LIMIT :w
        ) t
        ORDER BY week_end
    """), {"oid": org_id, "w": weekly_window})).mappings().all()

    # Cumulative: start from the balance at the beginning of the
    # window, then add each week's P&L. The beginning balance is
    # current_balance minus the sum of the window's P&L.
    window_pnl = sum(float(r["week_pnl"]) for r in weekly_rows)
    window_start_balance = current_balance - window_pnl

    weekly_curve = []
    running = window_start_balance
    for r in weekly_rows:
        running += float(r["week_pnl"])
        weekly_curve.append({
            "week_end": r["week_end"].isoformat() if r["week_end"] else "",
            "balance": running,
        })

    ytd_change = total_pnl
    ytd_pct = (
        (ytd_change / starting_balance * 100.0) if starting_balance else 0.0
    )

    return {
        "starting_balance": starting_balance,
        "current_balance": current_balance,
        "ytd_change": ytd_change,
        "ytd_pct": ytd_pct,
        "peak_balance": peak_balance,
        "current_drawdown": current_drawdown,
        "current_drawdown_pct": current_drawdown_pct,
        "last_5_result": last_5_result,
        "weekly_curve": weekly_curve,
        "trade_count": trade_count,
    }


def format_account_block(data: dict | None) -> str:
    """Render the account equity block as prose + a markdown table.

    Returns empty string if data is None.
    """
    if not data:
        return ""

    parts = []
    parts.append("--- THE ACCOUNT ---")
    parts.append(
        f"Starting balance: {_fmt_money(data['starting_balance'])}"
    )
    parts.append(
        f"Current balance: {_fmt_money(data['current_balance'])}"
    )
    parts.append(
        f"Change: {_fmt_money(data['ytd_change'])} "
        f"({data['ytd_pct']:+.2f}%)"
    )
    parts.append(
        f"Peak balance: {_fmt_money(data['peak_balance'])}"
    )
    parts.append(
        f"Current drawdown: {_fmt_money(data['current_drawdown'])} "
        f"({data['current_drawdown_pct']:+.2f}%)"
    )
    parts.append(
        f"Last 5 trades: {_fmt_money(data['last_5_result'])}"
    )
    parts.append("")
    parts.append("| Week ending | Balance |")
    parts.append("|-------------|---------|")
    for row in data["weekly_curve"]:
        parts.append(
            f"| {row['week_end']} | {_fmt_money(row['balance'])} |"
        )

    return "\n".join(parts)
