"""Read the trader's fundamental-metrics section.

`trader_process.fundamental_metrics` is a single text column where
the trader writes which fundamental checks they run before
entering, why, and what they do with them. It is prose — not a
signal, not a value.

The fundamental_metrics subblock reads this column as its source
material and pairs it with a structural-explainer coordinate.
"""
from __future__ import annotations

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


async def get_fundamental_metrics_section(
    session: AsyncSession, org_id: str,
) -> str:
    """Return the trader's fundamental-metrics section, or ''."""
    row = (await session.execute(sql_text("""
        SELECT fundamental_metrics
        FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL
        LIMIT 1
    """), {"oid": org_id})).first()
    if not row or not row[0]:
        return ""
    return (row[0] or "").strip()
