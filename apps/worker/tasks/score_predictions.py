"""Score predictions against completed matches.

Run every hour. For each prediction whose match is now 'finished'
and not yet scored, compute the metrics and store prediction_results.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)


async def _score_pending_async() -> dict:
    """Find unscored predictions on finished matches, score them."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.core.config import get_settings
    from packages.prediction.scoring import score_prediction

    settings = get_settings()
    factory = get_session_factory()

    scored = 0
    failed = 0

    async with factory() as session:
        # Find predictions on finished matches that haven't been scored yet
        rows = (await session.execute(sql_text("""
            SELECT
                p.id::text, p.prob_home_win, p.prob_draw, p.prob_away_win,
                p.prob_over_2_5,
                m.home_goals, m.away_goals,
                m.home_team, m.away_team
            FROM predictions p
            JOIN matches m ON m.id = p.match_id
            LEFT JOIN prediction_results r ON r.prediction_id = p.id
            WHERE m.status = 'finished'
              AND m.home_goals IS NOT NULL
              AND m.away_goals IS NOT NULL
              AND r.id IS NULL
        """))).all()

        log.info("score.pending", count=len(rows))

        for r in rows:
            pred_id = r[0]
            try:
                result = score_prediction(
                    prob_home_win=float(r[1]),
                    prob_draw=float(r[2]),
                    prob_away_win=float(r[3]),
                    prob_over_2_5=float(r[4]),
                    actual_home_goals=int(r[5]),
                    actual_away_goals=int(r[6]),
                )

                await session.execute(sql_text("""
                    INSERT INTO prediction_results (
                        prediction_id, actual_result,
                        actual_home_goals, actual_away_goals, actual_total_goals,
                        actual_over_2_5, result_correct, over_under_correct,
                        brier_score, log_loss
                    ) VALUES (
                        :pid, :ar,
                        :ahg, :aag, :atg,
                        :aou, :rc, :ouc,
                        :bs, :ll
                    )
                    ON CONFLICT (prediction_id) DO NOTHING
                """), {
                    "pid": pred_id,
                    "ar": result.actual_result,
                    "ahg": result.actual_home_goals,
                    "aag": result.actual_away_goals,
                    "atg": result.actual_total_goals,
                    "aou": result.actual_over_2_5,
                    "rc": result.result_correct,
                    "ouc": result.over_under_correct,
                    "bs": result.brier_score,
                    "ll": result.log_loss,
                })

                await session.commit()
                scored += 1

                log.info(
                    "score.done",
                    prediction_id=pred_id[:8],
                    home=r[7][:20],
                    away=r[8][:20],
                    score=f"{r[5]}-{r[6]}",
                    correct=result.result_correct,
                    brier=round(result.brier_score, 3),
                )
            except Exception as exc:
                failed += 1
                log.warning("score.failed", prediction_id=pred_id[:8], error=str(exc)[:200])

    return {"scored": scored, "failed": failed, "pending": len(rows)}


@celery_app.task(name="tasks.score_predictions", bind=True)
def score_predictions(self) -> dict:  # type: ignore[no-untyped-def]
    """Score all unscored predictions on finished matches."""
    return _run(_score_pending_async())
