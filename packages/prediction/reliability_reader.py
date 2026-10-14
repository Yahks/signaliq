"""Read helpers for source_reliability.

Pure read-only access to the reliability table populated daily by
apps/worker/tasks/external_reliability.py. No side effects.

Two functions:
  - load_source_reliability(session, source_slug, window_days=30)
      → float | None  (None if not yet computed for this source/window)
  - load_reliability_map(session, window_days=30)
      → dict[slug, weight]  (all sources with a row in this window)

Both return None / empty dict when the table is empty. Callers are
expected to treat missing reliability as "neutral" (fall back to flat
blend or default weighting).
"""
from __future__ import annotations

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


async def load_source_reliability(
    session: AsyncSession,
    *,
    source_slug: str,
    window_days: int = 30,
) -> float | None:
    """Return reliability_weight (0..1) for a source, or None if not computed.

    Joins source_reliability to external_prediction_sources by slug.
    Uses the most recent computed_at row for the requested window.
    """
    row = (await session.execute(sql_text("""
        SELECT sr.reliability_weight
        FROM source_reliability sr
        JOIN external_prediction_sources eps ON eps.id = sr.source_id
        WHERE eps.slug = :slug
          AND sr.window_days = :wd
        ORDER BY sr.computed_at DESC
        LIMIT 1
    """), {"slug": source_slug, "wd": window_days})).first()

    if not row or row[0] is None:
        return None
    return float(row[0])


async def load_reliability_map(
    session: AsyncSession,
    *,
    window_days: int = 30,
) -> dict[str, float]:
    """Return {source_slug: reliability_weight} for all sources in this window.

    Empty dict if no rows exist. Includes disabled sources? No — we filter
    on external_prediction_sources.enabled = true so retired sources fade
    out of the leaderboard automatically.
    """
    rows = (await session.execute(sql_text("""
        SELECT DISTINCT ON (eps.slug)
            eps.slug, sr.reliability_weight
        FROM source_reliability sr
        JOIN external_prediction_sources eps ON eps.id = sr.source_id
        WHERE sr.window_days = :wd
          AND eps.enabled = true
          AND sr.reliability_weight IS NOT NULL
        ORDER BY eps.slug, sr.computed_at DESC
    """), {"wd": window_days})).all()

    return {r[0]: float(r[1]) for r in rows}
