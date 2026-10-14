"""Broker integrations — dispatcher.

Each broker module implements the same four functions:
fetch_account, fetch_positions, fetch_orders, fetch_fills.
The caller picks the module by name.
"""
from __future__ import annotations

from types import ModuleType

from packages.trading.brokers import alpaca


_SUPPORTED = {
    "alpaca": alpaca,
    # 'ibkr': ibkr,       # not yet
    # 'tradier': tradier, # not yet
}


def get_broker_client(name: str) -> ModuleType:
    """Return the module for a broker. Raises ValueError if unknown."""
    n = name.strip().lower()
    if n not in _SUPPORTED:
        raise ValueError(f"Unsupported broker: {name}")
    return _SUPPORTED[n]


def supported_brokers() -> list[str]:
    return sorted(_SUPPORTED.keys())
