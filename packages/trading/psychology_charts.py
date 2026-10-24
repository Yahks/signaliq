"""The chart block for trading_psychology.

Gives the model what the price chart of the cited trade's instrument
shows — the bars around the entry, the entry/stop/exit levels, and
the price path between them — and tells it to place [CHART:daily]
where it discusses them. Post-processing replaces the tag with the
rendered image, exactly as market_analysis does.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_psychology_charts_block(
    session: AsyncSession, *, org_id: str, symbol: str,
    entry: float | None, stop: float | None, exit_price: float | None,
    traded_at: Any,
) -> str:
    if not symbol:
        return ""
    as_of = traded_at.date() if isinstance(traded_at, datetime) else traded_at
    params: dict[str, Any] = {"oid": org_id, "sym": symbol}
    clause = ""
    if as_of is not None:
        clause = "AND trade_date <= :asof "
        params["asof"] = as_of

    bars = (await session.execute(sql_text(
        "SELECT trade_date, open, high, low, close FROM market_history "
        "WHERE org_id = :oid AND symbol = :sym AND timeframe = '1d' "
        "AND deleted_at IS NULL " + clause +
        "ORDER BY trade_date ASC"
    ), params)).mappings().all()
    if len(bars) < 3:
        return ""

    closes = [float(b["close"]) for b in bars]
    dates = [str(b["trade_date"]) for b in bars]
    recent = closes[-30:]
    recent_dates = dates[-30:]
    hi = max(recent); lo = min(recent)

    lines = [
        "--- THE PRICE CHART (the article references it) ---",
        "",
        f"A daily candlestick chart of {symbol} accompanies this "
        f"article. It is placed where you write [CHART:daily] on its "
        f"own line.",
        "",
        f"{symbol} around the trade:",
        f"  Entry: {entry:.2f}" if entry is not None else "  Entry: n/a",
        f"  Stop: {stop:.2f}" if stop is not None else "  Stop: n/a",
        f"  Exit: {exit_price:.2f}" if exit_price is not None else "  Exit: n/a",
        f"  High in the 30 bars to the entry date: {hi:.2f}",
        f"  Low in the 30 bars to the entry date: {lo:.2f}",
        "",
        "  Last closes (oldest to newest): "
        + ", ".join(f"{c:.2f}" for c in recent),
        "  Dates: " + ", ".join(d[5:] for d in recent_dates),
        "",
        "PLACEMENT: write [CHART:daily] on its own line at the END of "
        "the diagnostics bullet that describes the cited trade. In "
        "that bullet, state the entry, the stop, the exit, and what "
        "the price did between them — the chart shows exactly that. "
        "Do not place the tag anywhere else. Do not write any other "
        "chart tag.",
        "",
    ]
    return "\n".join(lines)
