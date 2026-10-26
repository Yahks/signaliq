"""Every number the article needs, computed once, in Python.

The model does no arithmetic. It is handed finished values and
quotes them. Every failure in the fundamental_metrics series was an
arithmetic error the model made: a 40% breakeven that was really
65.3%, a 0.2% spread swing that was never in the source, a payoff
assumed at 1.5R when it was 0.175R.

This module takes the raw inputs — a trade row, the exit-structure
backtest, the signal log — and returns a dict of finished strings
and floats with labels. The prompt carries this dict verbatim.

Nothing here reads the model's output.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.trading.signal_backtest import backtest_exit_structure

log = get_logger(__name__)

CL_MULTIPLIER = 1000.0      # 1 CL contract = 1,000 barrels, $1/barrel
TARGET_PCT = 0.015
STOP_ATR_MULT = 1.75


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def trade_math(trade: dict[str, Any], account_equity: float,
               risk_budget_pct: float = 0.015) -> dict[str, Any]:
    """All per-trade numbers, finished.

    entry, stop, exit, contracts, atr, result in -> every derived
    value out. No field here is left for the model to compute.
    """
    entry = _f(trade.get("entry"))
    stop = _f(trade.get("stop"))
    exit_ = _f(trade.get("exit"))
    contracts = int(trade.get("contracts") or 0)
    atr = _f(trade.get("atr_at_entry"))
    result = _f(trade.get("result"))

    stop_dist = abs(entry - stop)
    planned_risk = stop_dist * contracts * CL_MULTIPLIER
    realized_pnl = result  # already in dollars
    budget = account_equity * risk_budget_pct

    r_mult = realized_pnl / planned_risk if planned_risk > 0 else 0.0
    pct_equity = realized_pnl / account_equity * 100.0 if account_equity else 0.0
    atr_mult = stop_dist / atr if atr > 0 else 0.0
    risk_pct = planned_risk / account_equity * 100.0 if account_equity else 0.0

    return {
        "entry": entry,
        "stop": stop,
        "exit": exit_,
        "contracts": contracts,
        "atr_at_entry": atr,
        "stop_distance_points": round(stop_dist, 4),
        "stop_atr_multiple": round(atr_mult, 2),
        "planned_risk_dollars": round(planned_risk, 2),
        "realized_pnl_dollars": round(realized_pnl, 2),
        "r_multiple": round(r_mult, 4),
        "percent_of_equity": round(pct_equity, 2),
        "planned_risk_pct_of_equity": round(risk_pct, 2),
        "account_equity": account_equity,
        "risk_budget_pct": risk_budget_pct * 100.0,
        "risk_budget_dollars": round(budget, 2),
    }


def payoff_math(backtest: dict[str, Any]) -> dict[str, Any]:
    """Payoff, breakeven, edge gap — from the backtest's own wins
    and losses, not from an assumed R:R.
    """
    if not backtest.get("available"):
        return {"available": False}
    avg_win = _f(backtest.get("avg_win_r"))
    avg_loss = abs(_f(backtest.get("avg_loss_r")))
    hit = _f(backtest.get("target_hit_rate_pct")) / 100.0
    if avg_loss <= 0:
        return {"available": False}
    payoff = avg_win / avg_loss
    breakeven = avg_loss / (avg_win + avg_loss)
    edge_gap_pp = (hit - breakeven) * 100.0
    exp = _f(backtest.get("expectancy_r"))
    return {
        "available": True,
        "n": backtest.get("n"),
        "target_hit_rate_pct": round(hit * 100.0, 2),
        "avg_win_r": round(avg_win, 3),
        "avg_loss_r": round(-avg_loss, 3),
        "payoff_ratio": round(payoff, 4),
        "breakeven_hit_rate_pct": round(breakeven * 100.0, 1),
        "edge_gap_pp": round(edge_gap_pp, 1),
        "expectancy_r": round(exp, 4),
    }


async def build_math_block(
    session: AsyncSession, org_id: str, symbol: str = "CL",
    account_equity: float = 200000.0,
) -> dict[str, Any]:
    """The full computed block the article quotes."""
    bt = await backtest_exit_structure(session, org_id, symbol)
    return {
        "exit_structure": payoff_math(bt),
    }
