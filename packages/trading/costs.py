"""Net P&L: trade_log.result is gross of slippage and commission.

result = (exit-entry)*contracts for a long, (entry-exit)*contracts
for a short. It excludes both costs.

entry_slippage / exit_slippage are signed USD P&L contributions
(positive = favorable, negative = cost), measured per trade.
commission_usd is a round-turn dollar amount — 0 for new rows, an
ESTIMATE ($2) for rows predating the cost model.

    net = result + entry_slippage + exit_slippage - commission_usd

Verified on 678 article-linked trades 2026-09-28..10-18:
gross -110,842.73, slippage -179.31, commission -1,356.00,
net -112,378.04.
"""
from __future__ import annotations

from typing import Any


def net_result(trade: dict[str, Any]) -> float:
    """The trade's P&L after measured slippage and commission."""
    try:
        r = float(trade.get("result") or 0.0)
    except (TypeError, ValueError):
        r = 0.0
    for k in ("entry_slippage", "exit_slippage"):
        v = trade.get(k)
        if v is not None:
            try:
                r += float(v)
            except (TypeError, ValueError):
                pass
    c = trade.get("commission_usd")
    if c is not None:
        try:
            r -= float(c)
        except (TypeError, ValueError):
            pass
    return r
