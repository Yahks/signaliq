"""Alpaca fills fetcher for the trade-monitor pipeline.

Reads the org's Alpaca credentials from broker_credentials
first; falls back to env vars (ALPACA_API_KEY / ALPACA_SECRET_KEY
/ ALPACA_PAPER) when no DB row exists.

This file is intentionally thin — it delegates to
packages/trading/brokers/alpaca.fetch_fills. The pipeline
only needs fills; the Paper Account tab uses the full client
in packages/trading/brokers/alpaca.py directly.
"""
from __future__ import annotations

import os
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.trading.brokers import alpaca as alpaca_client
from packages.trading.brokers.credentials import load_broker_credentials

log = get_logger(__name__)


def _creds_from_env() -> tuple[str, str, bool] | None:
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        return None
    paper = os.environ.get("ALPACA_PAPER", "true").lower() in (
        "1", "true", "yes", "on",
    )
    return key, secret, paper


async def fetch_today_fills(
    session: AsyncSession | None = None,
    org_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return today's fills.

    Credentials resolution order:
      1. broker_credentials row for (org_id, 'alpaca') if session
         and org_id are provided
      2. env vars ALPACA_API_KEY / ALPACA_SECRET_KEY

    Returns [] if neither source has credentials.
    """
    creds: tuple[str, str, bool] | None = None

    if session is not None and org_id:
        loaded = await load_broker_credentials(session, org_id, "alpaca")
        if loaded:
            creds = (loaded["api_key"], loaded["api_secret"], loaded["paper"])
            log.info("alpaca.creds_source", source="db", org_id=org_id[:8])

    if creds is None:
        creds = _creds_from_env()
        if creds:
            log.info("alpaca.creds_source", source="env")

    if creds is None:
        log.info("alpaca.no_creds")
        return []

    api_key, api_secret, paper = creds
    fills = await alpaca_client.fetch_fills(api_key, api_secret, paper=paper)
    log.info("alpaca.fetched", count=len(fills), paper=paper)
    return fills
