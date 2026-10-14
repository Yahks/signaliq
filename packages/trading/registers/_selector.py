"""Register selector — picks a register per article.

Mirrors pick_form_and_tone in packages/trading/forms.py: the
subblock's last N posts are inspected, and the next register is
the first eligible one not used recently. Serialized with the
same advisory-lock pattern so concurrent generations cannot pick
the same register.

Falls back to the whole pool if the subblock has no mapping yet.
"""
from __future__ import annotations

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from . import (
    DEFAULT_REGISTER,
    POOL_MARKET,
    SUBBLOCK_REGISTERS,
    eligible_registers,
)

log = get_logger(__name__)


async def pick_register(
    session: AsyncSession,
    *,
    org_id: str,
    subblock_id: str,
    subblock_slug: str | None,
    tier: str | None = None,
    recent_n: int | None = None,
) -> str:
    """Least-recently-used register for this subblock.

    Reads the last `recent_n` posts' recorded register names from
    source_payload->>'register' and returns the first eligible
    register in the pool that has not been used recently. Falls
    back to the least-recently used eligible register if every
    combination has been seen.

    Returns DEFAULT_REGISTER if the subblock has no mapping and
    no pool is available.
    """
    pool = eligible_registers(subblock_slug, tier=tier)
    if not pool:
        # No tier-mapped pool — fall back to the pre-tier map and
        # then to the whole market pool. This is the migration path
        # for subblocks not yet listed in SUBBLOCK_TIERS.
        pool = eligible_registers(subblock_slug)
    if not pool:
        pool = list(POOL_MARKET)
    if not pool:
        return DEFAULT_REGISTER

    # Adaptive lookback: a pool of N registers can rotate N-1 deep
    # before exhausting, so use min(5, len(pool) - 1) with a floor
    # of 1. Without this, a 2-register pool with recent_n=5 means
    # both registers are always "recent" and the selector falls back
    # to the oldest every time.
    if recent_n is None:
        recent_n = min(5, max(1, len(pool) - 1))

    # pg_advisory_xact_lock: same pattern as pick_form_and_tone.
    # Held until the caller's transaction commits, so a concurrent
    # generation of the same subblock cannot pick the same register.
    await session.execute(
        sql_text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"pick_register:{org_id}:{subblock_id}"},
    )

    rows = (await session.execute(sql_text("""
        SELECT source_payload->>'register' AS register
        FROM trading_posts
        WHERE org_id = :org_id
          AND subblock_id = :subblock_id
          AND source_payload ? 'register'
        ORDER BY created_at DESC
        LIMIT :n
    """), {
        "org_id": org_id,
        "subblock_id": subblock_id,
        "n": recent_n,
    })).all()

    recent = [r[0] for r in rows if r[0]]

    # Pick the first eligible register not in `recent`.
    for name in pool:
        if name not in recent:
            log.info(
                "register.picked",
                register=name,
                subblock=subblock_slug,
                recent_count=len(recent),
            )
            return name

    # All eligible registers used recently — fall back to the one
    # used longest ago (the last entry in `recent` order).
    fallback = recent[-1] if recent else pool[0]
    if fallback not in pool:
        fallback = pool[0]
    log.info(
        "register.picked_fallback",
        register=fallback,
        subblock=subblock_slug,
        recent_count=len(recent),
    )
    return fallback