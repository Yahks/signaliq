"""External source reliability scorer.

For every external_prediction whose match is now finished, compare the
source's predicted probabilities against the actual outcome and upsert
a rolling-accuracy row into source_reliability.

Design:
  - Separate task from score_predictions (which scores *our* blend).
  - Windows: [7, 30, 90] days.
  - reliability_weight: 0.7 * accuracy + 0.3 * (1 - normalised_brier),
    clamped to [0, 1]. Missing/insufficient data → 0.5 (neutral).
  - Rows are upserted per (source_id, window_days) — 0018's unique constraint.
  - Safe to re-run: idempotent, cheap.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)

# 3-outcome multiclass Brier max = 2*(1 - 1/3)^2 = 0.888...
MAX_BRIER_3 = 0.88888888
WINDOWS = [7, 30, 90]
LOW_SAMPLE_THRESHOLD = 10  # informational — used by API/UI, not enforced here


def _actual_result(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home_win"
    if home_goals < away_goals:
        return "away_win"
    return "draw"


def _brier(ph: float, pd: float, pa: float, actual: str) -> float:
    """Multiclass Brier score: sum of (p_i - y_i)^2 over the 3 outcomes."""
    yh = 1.0 if actual == "home_win" else 0.0
    yd = 1.0 if actual == "draw" else 0.0
    ya = 1.0 if actual == "away_win" else 0.0
    return (ph - yh) ** 2 + (pd - yd) ** 2 + (pa - ya) ** 2


def _weight_from(accuracy: float, brier: float) -> float:
    """0.7 * accuracy + 0.3 * (1 - normalised_brier), clamped to [0, 1]."""
    acc_component = max(0.0, min(1.0, accuracy))
    brier_component = max(0.0, min(1.0, 1.0 - (brier / MAX_BRIER_3)))
    w = 0.7 * acc_component + 0.3 * brier_component
    return round(max(0.0, min(1.0, w)), 4)


async def _score_external_async() -> dict:
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.core.config import get_settings

    settings = get_settings()
    factory = get_session_factory()

    total_upserts = 0
    sources_scored: set[str] = set()

    try:
        async with factory() as session:
            # All (source, match, prediction, actual result) tuples for finished matches.
            rows = (await session.execute(sql_text("""
                SELECT
                    ep.source_id::text,
                    eps.slug,
                    ep.prob_home_win, ep.prob_draw, ep.prob_away_win,
                    m.home_goals, m.away_goals,
                    m.kickoff_at
                FROM external_predictions ep
                JOIN external_prediction_sources eps ON eps.id = ep.source_id
                JOIN matches m ON m.id = ep.match_id
                WHERE m.status = 'finished'
                  AND m.home_goals IS NOT NULL
                  AND m.away_goals IS NOT NULL
                  AND eps.enabled = true
            """))).all()

            log.info("external_reliability.rows_to_score", count=len(rows))

            # Bucket scores per (source_id, window_days), filtered by kickoff date.
            #
            # Each window (7/30/90 days) counts only matches with
            # kickoff_at >= now - window_days. This makes 7/30/90 genuinely
            # different — 7-day is most recent form, 90-day is season-scale.
            from collections import defaultdict

            buckets: dict[tuple[str, int], dict] = defaultdict(
                lambda: {"n": 0, "correct": 0, "brier_sum": 0.0}
            )
            slug_by_source: dict[str, str] = {}

            now = datetime.now(UTC)
            # Precompute each window's cutoff so we don't recompute per row
            window_cutoffs: dict[int, datetime] = {
                wd: now - timedelta(days=wd) for wd in WINDOWS
            }

            for r in rows:
                source_id, slug, ph, pd, pa, hg, ag, kickoff = r
                slug_by_source[source_id] = slug

                if kickoff is None:
                    # No kickoff time — cannot place in a window; skip.
                    continue

                actual = _actual_result(int(hg), int(ag))
                predicted = max(
                    (("home_win", float(ph)),
                     ("draw", float(pd)),
                     ("away_win", float(pa))),
                    key=lambda x: x[1],
                )[0]
                brier = _brier(float(ph), float(pd), float(pa), actual)

                for wd, cutoff in window_cutoffs.items():
                    if kickoff < cutoff:
                        continue  # outside this window — skip
                    b = buckets[(source_id, wd)]
                    b["n"] += 1
                    if predicted == actual:
                        b["correct"] += 1
                    b["brier_sum"] += brier

            # Upsert one row per (source_id, window_days)
            now = datetime.now(UTC)
            for (source_id, wd), b in buckets.items():
                n = b["n"]
                if n == 0:
                    continue
                accuracy = b["correct"] / n
                avg_brier = b["brier_sum"] / n
                weight = _weight_from(accuracy, avg_brier)

                await session.execute(sql_text("""
                    INSERT INTO source_reliability
                        (source_id, window_days, predictions_scored,
                         result_accuracy, avg_brier_score, reliability_weight, computed_at)
                    VALUES
                        (:sid, :wd, :n, :acc, :br, :w, now())
                    ON CONFLICT (source_id, window_days)
                    DO UPDATE SET
                        predictions_scored = EXCLUDED.predictions_scored,
                        result_accuracy = EXCLUDED.result_accuracy,
                        avg_brier_score = EXCLUDED.avg_brier_score,
                        reliability_weight = EXCLUDED.reliability_weight,
                        computed_at = now()
                """), {
                    "sid": source_id, "wd": wd, "n": n,
                    "acc": round(accuracy, 4),
                    "br": round(avg_brier, 5),
                    "w": weight,
                })
                total_upserts += 1
                sources_scored.add(slug_by_source.get(source_id, source_id[:8]))

            await session.commit()

    finally:
        pass

    return {
        "upserts": total_upserts,
        "sources_scored": sorted(sources_scored),
        "windows": WINDOWS,
    }


@celery_app.task(name="tasks.score_external_reliability", bind=True)
def score_external_reliability(self) -> dict:  # type: ignore[no-untyped-def]
    """Daily: recompute source_reliability rows for all enabled sources."""
    return _run(_score_external_async())
