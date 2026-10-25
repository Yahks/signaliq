"""Daily job: record the fundamental signals.

Idempotent per day — the table's unique index on
(org, observed_on, signal) means a second run overwrites the first
rather than duplicating.

Spread is real (two CL contracts). API draw and OPEC cut record
'unavailable' until a release feed exists; the article states that
rather than inventing a value.
"""
from __future__ import annotations

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.trading.signal_recorder import record_spread, record_unavailable

log = get_logger(__name__)


async def record_all(session: AsyncSession, org_id: str) -> dict:
    out: dict = {}
    try:
        out["spread"] = await record_spread(session, org_id)
    except Exception as exc:
        log.warning("signal_job.spread_failed", error=str(exc)[:150])
        out["spread"] = {"status": "error"}
    for sig, why in (("api_draw", "no API release feed"),
                     ("opec_cut", "no MOMR feed")):
        try:
            await record_unavailable(session, org_id, sig, why)
            out[sig] = "unavailable"
        except Exception as exc:
            log.warning("signal_job.record_failed", signal=sig,
                        error=str(exc)[:150])
    return out
