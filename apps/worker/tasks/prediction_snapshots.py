"""Prediction snapshot refresh tasks.

Fills form_snapshots (and, in a future stage, h2h_snapshots) from finished
matches so dixon_coles_v2 can layer recent form on the base model.

Design:
  - One task per snapshot kind (form here; H2H is a separate task).
  - Ordered in beat_schedule BEFORE dixon_coles_v2 prediction task.
  - Idempotent: upserts per (team, competition, as_of_date).
  - No LLM, no external API — purely derived from matches table.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)

# TODO: derive from SELECT DISTINCT competition FROM matches WHERE status='finished'
COMPETITIONS = ["EPL"]


async def _refresh_form_async() -> dict:
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.core.config import get_settings
    from packages.prediction.form.recent import (
        compute_form_for_competition,
        save_form_snapshots,
    )

    settings = get_settings()
    factory = get_session_factory()

    total_saved = 0
    by_comp: dict[str, int] = {}

    try:
        async with factory() as session:
            # compute_form_for_* expects a datetime, not a date.
            # Internally it does kickoff_at < :as_of and s.as_of_date = as_of.date().
            as_of = datetime.now(UTC)
            for comp in COMPETITIONS:
                try:
                    by_team = await compute_form_for_competition(
                        session, competition=comp, as_of=as_of,
                    )
                    # Returns dict[team, FormSnapshot]; save_form_snapshots
                    # wants list[FormSnapshot].
                    saved = await save_form_snapshots(
                        session, snapshots=list(by_team.values()),
                    )
                    by_comp[comp] = saved
                    total_saved += saved
                    log.info(
                        "form_snapshots.saved",
                        competition=comp, as_of=str(as_of), count=saved,
                    )
                except Exception as exc:
                    log.warning(
                        "form_snapshots.failed",
                        competition=comp, error=str(exc)[:200],
                    )
                    by_comp[comp] = -1
            await session.commit()
    finally:
        pass

    return {"total_saved": total_saved, "by_competition": by_comp}


@celery_app.task(name="tasks.refresh_form_snapshots", bind=True)
def refresh_form_snapshots(self) -> dict:  # type: ignore[no-untyped-def]
    """Daily: recompute per-team rolling form snapshots."""
    return _run(_refresh_form_async())


async def _refresh_h2h_async() -> dict:
    """Refresh h2h_snapshots for every ordered team pair with any finished meeting."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.core.config import get_settings
    from packages.prediction.form.h2h import compute_h2h, save_h2h_snapshot

    settings = get_settings()
    factory = get_session_factory()

    total_saved = 0
    by_comp: dict[str, int] = {}

    try:
        async with factory() as session:
            # compute_h2h expects a datetime, not a date (mirrors form).
            as_of = datetime.now(UTC)

            for comp in COMPETITIONS:
                # Every pair that has met at least once, in either orientation.
                # We deduplicate the unordered set first, then feed both
                # orientations so whichever way an upcoming fixture is
                # scheduled, a row exists.
                pairs_raw = (await session.execute(sql_text("""
                    SELECT DISTINCT
                        LEAST(home_team, away_team)    AS a,
                        GREATEST(home_team, away_team) AS b
                    FROM matches
                    WHERE status = 'finished'
                      AND competition = :comp
                      AND home_goals IS NOT NULL
                """), {"comp": comp})).all()

                saved_for_comp = 0
                for (a, b) in pairs_raw:
                    for (home, away) in ((a, b), (b, a)):
                        try:
                            snap = await compute_h2h(
                                session,
                                home_team=home,
                                away_team=away,
                                competition=comp,
                                as_of=as_of,
                            )
                            if snap is None:
                                continue
                            await save_h2h_snapshot(session, snapshot=snap)
                            saved_for_comp += 1
                        except Exception as exc:
                            log.warning(
                                "h2h_snapshots.pair_failed",
                                home=home, away=away,
                                error=str(exc)[:200],
                            )

                by_comp[comp] = saved_for_comp
                total_saved += saved_for_comp
                log.info(
                    "h2h_snapshots.saved",
                    competition=comp, as_of=str(as_of), count=saved_for_comp,
                )
    finally:
        pass

    return {"total_saved": total_saved, "by_competition": by_comp}


@celery_app.task(name="tasks.refresh_h2h_snapshots", bind=True)
def refresh_h2h_snapshots(self) -> dict:  # type: ignore[no-untyped-def]
    """Daily: recompute per-pair H2H snapshots."""
    return _run(_refresh_h2h_async())
