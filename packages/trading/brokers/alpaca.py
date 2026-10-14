"""Alpaca broker client — account, positions, orders, fills.

Separate from packages/trading/sources/alpaca.py (which
serves the trade-monitor pipeline by fetching fills only).
This client is the full API surface used by the Paper
Account tab.

Every function takes the credentials as arguments. The
caller decides where the credentials come from — broker_
credentials table, env vars, whatever. This module does
not touch env vars or the DB.

Paper vs live: `paper=True` hits paper-api.alpaca.markets,
`paper=False` hits api.alpaca.markets.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from packages.core.logging import get_logger

log = get_logger(__name__)

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"
DEFAULT_TIMEOUT = 15.0


def _base_url(paper: bool) -> str:
    return PAPER_URL if paper else LIVE_URL


def _headers(api_key: str, api_secret: str) -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }


async def fetch_account(
    api_key: str, api_secret: str, paper: bool = True,
) -> dict[str, Any]:
    """Return the account summary.

    Fields: equity, buying_power, cash, portfolio_value,
    currency, status, pattern_day_trader.
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            f"{_base_url(paper)}/v2/account",
            headers=_headers(api_key, api_secret),
        )
        resp.raise_for_status()
        raw = resp.json()
    return {
        "equity": float(raw.get("equity") or 0),
        "buying_power": float(raw.get("buying_power") or 0),
        "cash": float(raw.get("cash") or 0),
        "portfolio_value": float(raw.get("portfolio_value") or 0),
        "currency": raw.get("currency") or "USD",
        "status": raw.get("status") or "unknown",
        "pattern_day_trader": bool(raw.get("pattern_day_trader")),
    }


async def fetch_positions(
    api_key: str, api_secret: str, paper: bool = True,
) -> list[dict[str, Any]]:
    """Return open positions.

    Each: symbol, qty, avg_entry_price, market_value,
    unrealized_pl, unrealized_plpc.
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            f"{_base_url(paper)}/v2/positions",
            headers=_headers(api_key, api_secret),
        )
        resp.raise_for_status()
        raw = resp.json()
    return [
        {
            "symbol": p.get("symbol") or "",
            "qty": float(p.get("qty") or 0),
            "avg_entry_price": float(p.get("avg_entry_price") or 0),
            "market_value": float(p.get("market_value") or 0),
            "unrealized_pl": float(p.get("unrealized_pl") or 0),
            "unrealized_plpc": float(p.get("unrealized_plpc") or 0),
        }
        for p in raw
    ]


async def fetch_orders(
    api_key: str,
    api_secret: str,
    paper: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recent orders (most recent first).

    Each: id, symbol, side, qty, filled_qty, type, status,
    submitted_at, filled_avg_price.
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            f"{_base_url(paper)}/v2/orders",
            params={"status": "all", "limit": limit, "direction": "desc"},
            headers=_headers(api_key, api_secret),
        )
        resp.raise_for_status()
        raw = resp.json()
    return [
        {
            "id": o.get("id") or "",
            "symbol": o.get("symbol") or "",
            "side": o.get("side") or "",
            "qty": o.get("qty") or "0",
            "filled_qty": o.get("filled_qty") or "0",
            "type": o.get("type") or "market",
            "status": o.get("status") or "",
            "submitted_at": o.get("submitted_at"),
            "filled_avg_price": (
                float(o["filled_avg_price"])
                if o.get("filled_avg_price") else None
            ),
        }
        for o in raw
    ]


async def fetch_fills(
    api_key: str,
    api_secret: str,
    paper: bool = True,
    day: str | None = None,
) -> list[dict[str, Any]]:
    """Return fills for a day (default: today, UTC).

    Each: id, symbol, side, qty, price, transaction_time.
    """
    if day is None:
        day = datetime.now(UTC).date().isoformat()
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            f"{_base_url(paper)}/v2/account/activities/FILL",
            params={
                "after": f"{day}T00:00:00Z",
                "direction": "asc",
                "page_size": 100,
            },
            headers=_headers(api_key, api_secret),
        )
        resp.raise_for_status()
        raw = resp.json()
    return [
        {
            "id": f.get("id") or "",
            "symbol": f.get("symbol") or "",
            "side": f.get("side") or "",
            "qty": float(f.get("qty") or 0),
            "price": float(f.get("price") or 0),
            "transaction_time": f.get("transaction_time"),
        }
        for f in raw
    ]

async def place_order(
    api_key: str,
    api_secret: str,
    *,
    symbol: str,
    side: str,
    qty: float,
    type: str = "market",
    limit_price: float | None = None,
    time_in_force: str = "day",
    paper: bool = True,
) -> dict[str, Any]:
    """Place an order. Returns the broker's order record.

    For 'market' orders, omit limit_price. For 'limit' orders,
    limit_price is required.
    """
    side = side.strip().lower()
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    type = type.strip().lower()
    if type not in ("market", "limit"):
        raise ValueError(f"type must be 'market' or 'limit', got {type!r}")
    if type == "limit" and limit_price is None:
        raise ValueError("limit_price is required for limit orders")
    if qty <= 0:
        raise ValueError("qty must be positive")

    body: dict[str, Any] = {
        "symbol": symbol.strip().upper(),
        "side": side,
        "qty": str(qty),
        "type": type,
        "time_in_force": time_in_force,
    }
    if type == "limit":
        body["limit_price"] = str(limit_price)

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.post(
            f"{_base_url(paper)}/v2/orders",
            headers=_headers(api_key, api_secret),
            json=body,
        )
        if resp.status_code >= 400:
            # Include the broker's message in the exception.
            try:
                err = resp.json()
                msg = err.get("message") or err.get("error") or resp.text
            except Exception:
                msg = resp.text
            raise RuntimeError(f"Alpaca rejected the order: {msg[:300]}")
        return resp.json()

