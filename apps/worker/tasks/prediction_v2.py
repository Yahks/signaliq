"""Dixon-Coles v2 prediction task.

Writes dixon_coles_v2 predictions to external_predictions for every
scheduled match, so the model participates in the ensemble + reliability
system alongside the_odds_api.

Uses:
  - fit_and_cache() from predictor.py (same fit as the blend)
  - form_snapshots (team-level, from tasks.prediction_snapshots)
  - h2h_snapshots (pair-level, from tasks.prediction_snapshots)
  - predict_match_v2() from dixon_coles_v2.py

Idempotent: upserts per (source_id, match_id) via 0018's unique constraint.
"""
from __future__ import annotations

import asyncio
import json

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)

COMPETITIONS = ["EPL"]
SOURCE_SLUG = "dixon_coles_v2"


async def _run_v2_async() -> dict:
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.core.config import get_settings
    from packages.prediction.dixon_coles_v2 import (
        FormSignal,
        H2HSignal,
        predict_match_v2,
    )
    from packages.prediction.predictor import fit_and_cache

    settings = get_settings()
    factory = get_session_factory()

    total_upserts = 0
    by_comp: dict[str, int] = {}

    try:
        async with factory() as session:
            # Resolve source_id once
            source_id = (await session.execute(sql_text("""
                SELECT id::text FROM external_prediction_sources WHERE slug = :slug
            """), {"slug": SOURCE_SLUG})).scalar()

            if not source_id:
                log.error("v2.source_not_registered", slug=SOURCE_SLUG)
                return {"total_upserts": 0, "by_competition": {}, "error": "source not registered"}

            for comp in COMPETITIONS:
                # Fit base model (cached by config_hash)
                try:
                    fit, config_hash = await fit_and_cache(session, competition=comp)
                except Exception as exc:
                    log.warning("v2.fit_failed", competition=comp, error=str(exc)[:200])
                    by_comp[comp] = -1
                    continue

                # Load all form snapshots for this competition (today's rows)
                form_rows = (await session.execute(sql_text("""
                    SELECT DISTINCT ON (team)
                        team, attack_residual, defense_residual, matches_considered
                    FROM form_snapshots
                    WHERE competition = :comp
                    ORDER BY team, as_of_date DESC
                """), {"comp": comp})).all()
                form_by_team = {
                    r[0]: FormSignal(
                        team=r[0],
                        attack_residual=float(r[1]),
                        defense_residual=float(r[2]),
                        matches_considered=int(r[3]),
                    )
                    for r in form_rows
                }

                # Load all h2h snapshots for this competition (latest per pair)
                h2h_rows = (await session.execute(sql_text("""
                    SELECT DISTINCT ON (home_team, away_team)
                        home_team, away_team, h2h_multiplier, matches_considered
                    FROM h2h_snapshots
                    WHERE competition = :comp
                    ORDER BY home_team, away_team, as_of_date DESC
                """), {"comp": comp})).all()
                h2h_by_pair: dict[tuple[str, str], H2HSignal] = {
                    (r[0], r[1]): H2HSignal(
                        home_team=r[0],
                        away_team=r[1],
                        multiplier=float(r[2]),
                        matches_considered=int(r[3]),
                    )
                    for r in h2h_rows
                }

                # Upcoming matches
                fixtures = (await session.execute(sql_text("""
                    SELECT id::text, home_team, away_team, kickoff_at
                    FROM matches
                    WHERE status = 'scheduled'
                      AND competition = :comp
                    ORDER BY kickoff_at ASC
                """), {"comp": comp})).all()

                log.info(
                    "v2.fixtures_found",
                    competition=comp, count=len(fixtures),
                    form_teams=len(form_by_team),
                    h2h_pairs=len(h2h_by_pair),
                )

                saved = 0
                failed = 0
                for match_id, home, away, kickoff in fixtures:
                    try:
                        pred = predict_match_v2(
                            fit,
                            home_team=home,
                            away_team=away,
                            home_form=form_by_team.get(home),
                            away_form=form_by_team.get(away),
                            h2h=h2h_by_pair.get((home, away)),
                        )
                    except Exception as exc:
                        failed += 1
                        log.warning(
                            "v2.predict_failed",
                            match_id=match_id[:8], home=home, away=away,
                            error=str(exc)[:200],
                        )
                        continue

                    raw_payload = {
                        "config_hash": config_hash,
                        "form_home_matches": (
                            pred.home_form.matches_considered if pred.home_form else None
                        ),
                        "form_away_matches": (
                            pred.away_form.matches_considered if pred.away_form else None
                        ),
                        "h2h_matches": pred.h2h.matches_considered if pred.h2h else None,
                        "form_shift_home": pred.form_shift_home,
                        "form_shift_away": pred.form_shift_away,
                        "h2h_multiplier": pred.h2h_multiplier,
                        "base_home_goals": pred.base.expected_home_goals,
                        "base_away_goals": pred.base.expected_away_goals,
                        "adjusted_home_goals": pred.adjusted_expected_home_goals,
                        "adjusted_away_goals": pred.adjusted_expected_away_goals,
                        "most_likely_score": pred.most_likely_score,
                    }

                    await session.execute(sql_text("""
                        INSERT INTO external_predictions (
                            source_id, match_id,
                            prob_home_win, prob_draw, prob_away_win,
                            prob_over_2_5, raw_payload, fetched_at
                        ) VALUES (
                            :sid, :mid,
                            :ph, :pd, :pa,
                            :pou, CAST(:raw AS jsonb), now()
                        )
                        ON CONFLICT (source_id, match_id) DO UPDATE
                        SET prob_home_win = EXCLUDED.prob_home_win,
                            prob_draw = EXCLUDED.prob_draw,
                            prob_away_win = EXCLUDED.prob_away_win,
                            prob_over_2_5 = EXCLUDED.prob_over_2_5,
                            raw_payload = EXCLUDED.raw_payload,
                            fetched_at = now()
                    """), {
                        "sid": source_id, "mid": match_id,
                        "ph": pred.prob_home_win,
                        "pd": pred.prob_draw,
                        "pa": pred.prob_away_win,
                        "pou": pred.prob_over_2_5,
                        "raw": json.dumps(raw_payload),
                    })
                    saved += 1

                await session.commit()
                by_comp[comp] = saved
                total_upserts += saved
                log.info(
                    "v2.predictions_saved",
                    competition=comp, saved=saved, failed=failed,
                )
    finally:
        pass

    return {"total_upserts": total_upserts, "by_competition": by_comp}


@celery_app.task(name="tasks.run_v2_predictions", bind=True)
def run_v2_predictions(self) -> dict:  # type: ignore[no-untyped-def]
    """Daily: write dixon_coles_v2 predictions to external_predictions."""
    return _run(_run_v2_async())
