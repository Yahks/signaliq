"""Block registry and enablement helpers.

A "block" is a service the user selects at signup: football, trading,
news (gossip and jobs are declared here for forward compatibility but
are not yet onboardable). A block is enabled for an org when there is
an org_block_settings row with auto_enabled = true.

Missing row means DISABLED. This is the onboarding model: a brand-new
org has no rows, so no blocks. The picker (S.7.4/S.7.5) writes rows
when the user selects services.

The slug constants here are the canonical set. Every org_block_settings
row is FK-checked against blocks.slug (migration 0047), and blocks is
seeded with rows for football, trading, news. Adding a new block means:
add a blocks row, add a constant here, add it to the picker.
"""
from __future__ import annotations

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

# Canonical slugs. These match blocks.slug exactly.
BLOCK_FOOTBALL = "football"
BLOCK_TRADING  = "trading"
BLOCK_NEWS     = "news"
BLOCK_GOSSIP   = "gossip"
BLOCK_JOBS     = "jobs"

# Blocks a new user can pick at signup. Gossip and jobs are excluded
# until their content pipelines exist.
ONBOARDABLE_BLOCKS: tuple[str, ...] = (
    BLOCK_FOOTBALL,
    BLOCK_TRADING,
    BLOCK_NEWS,
)

# The picker requires 1 <= N <= 2. Zero blocks is allowed after signup
# (a user can remove their last block), but not at signup.
MIN_BLOCKS_AT_SIGNUP = 1
MAX_BLOCKS_PER_ORG = 2


async def is_block_enabled(
    session: AsyncSession, org_id: str, block_slug: str,
) -> bool:
    """True iff org_id has an org_block_settings row for block_slug
    with auto_enabled = true.

    A missing row returns False — no implicit enablement. This is the
    opposite of the pre-S.7.2 behavior; see the migration note in
    alembic/versions/0047.
    """
    row = (await session.execute(sql_text("""
        SELECT auto_enabled FROM org_block_settings
        WHERE org_id = :oid AND block_slug = :slug
    """), {"oid": org_id, "slug": block_slug})).first()
    return bool(row and row[0])


def is_block_enabled_sync(session, org_id: str, block_slug: str) -> bool:
    """Sync variant for callers that use a sync Session (router gate,
    worker tasks that don't have an async session at the call site)."""
    row = session.execute(sql_text("""
        SELECT auto_enabled FROM org_block_settings
        WHERE org_id = :oid AND block_slug = :slug
    """), {"oid": org_id, "slug": block_slug}).first()
    return bool(row and row[0])


async def enabled_blocks(session: AsyncSession, org_id: str) -> list[str]:
    """All enabled block slugs for the org, in a stable order."""
    rows = (await session.execute(sql_text("""
        SELECT block_slug FROM org_block_settings
        WHERE org_id = :oid AND auto_enabled = true
        ORDER BY block_slug
    """), {"oid": org_id})).all()
    return [r[0] for r in rows]
