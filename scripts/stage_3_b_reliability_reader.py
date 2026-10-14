"""
SignalIQ — Phase 10.9b Stage 3.B.

Reader helper for source_reliability.

Exposes two async functions that read from the source_reliability table
(populated by apps/worker/tasks/external_reliability.py).

  load_source_reliability(session, source_slug, window_days=30)
      → float | None  (None if no row exists)

  load_reliability_map(session, window_days=30)
      → dict[source_slug, float]

Read-only. No schema change. No behaviour change.
Used by:
  - blend.py  (3.C — future)
  - /api/predictions/sources endpoint (3.D)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
READER = ROOT / "packages" / "prediction" / "reliability_reader.py"


READER_SRC = '''"""Read helpers for source_reliability.

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
'''


def step1_write_reader() -> None:
    if READER.exists():
        print(f"  ↷ {READER.name} already exists")
        return
    READER.write_text(READER_SRC)
    print(f"  ✓ wrote {READER.relative_to(ROOT)}")


def step2_verify() -> bool:
    """Import both functions and call them against the DB."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    sys.path.insert(0, str(ROOT))
    from packages.core.config import get_settings
    from packages.prediction.reliability_reader import (
        load_source_reliability,
        load_reliability_map,
    )

    async def _run() -> bool:
        engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with factory() as session:
                print("  calling load_source_reliability('the_odds_api', 30)...")
                one = await load_source_reliability(
                    session, source_slug="the_odds_api", window_days=30,
                )
                print(f"    → {one}")

                print("  calling load_reliability_map(30)...")
                m = await load_reliability_map(session, window_days=30)
                print(f"    → {m}")

                # Missing source must return None, not raise
                print("  calling load_source_reliability('nonexistent', 30)...")
                missing = await load_source_reliability(
                    session, source_slug="nonexistent_source", window_days=30,
                )
                assert missing is None, "missing source should return None"
                print(f"    → {missing} (correct)")

                # Shape check
                assert isinstance(m, dict), "map must be a dict"
                print("  ✓ shapes correct")

            return True
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Stage 3.B: source reliability reader helper")
    print("=" * 70)

    print("\n[1/2] Write packages/prediction/reliability_reader.py")
    step1_write_reader()

    print("\n[2/2] Import + call against DB")
    ok = step2_verify()

    print("=" * 70)
    if ok:
        print("✅ Stage 3.B complete — reader helper ready for 3.C and 3.D")
        print("=" * 70)
        sys.exit(0)
    print("❌ Stage 3.B failed")
    print("=" * 70)
    sys.exit(1)


if __name__ == "__main__":
    main()
