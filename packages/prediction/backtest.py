"""Backtest the Dixon-Coles model against historical data.

Splits the training window: fit on matches BEFORE a cutoff date, predict
matches AFTER it, compare against actual results.

This gives us real accuracy numbers on real football without waiting for
matches to finish.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.prediction.dixon_coles import MatchRecord, fit_model, predict_match
from packages.prediction.scoring import ScoringResult, score_prediction

log = get_logger(__name__)


@dataclass
class BacktestResult:
    competition: str
    train_cutoff: datetime
    train_count: int
    test_count: int
    skipped_unknown_teams: int
    # Aggregates
    result_accuracy: float
    over_under_accuracy: float
    avg_brier: float
    avg_log_loss: float
    # Per-confidence-band accuracy
    by_confidence: dict = field(default_factory=dict)
    # Raw scoring results (for further analysis)
    scores: list[ScoringResult] = field(default_factory=list)


async def backtest(
    session: AsyncSession,
    *,
    competition: str = "EPL",
    test_window_days: int = 90,
) -> BacktestResult:
    """Fit on matches older than `test_window_days`, predict the rest."""
    cutoff = datetime.now(UTC) - timedelta(days=test_window_days)

    # Load ALL matches (train + test)
    rows = (await session.execute(sql_text("""
        SELECT home_team, away_team, home_goals, away_goals, kickoff_at
        FROM matches
        WHERE status = 'finished'
          AND competition = :comp
          AND home_goals IS NOT NULL
        ORDER BY kickoff_at ASC
    """), {"comp": competition})).all()

    train: list[MatchRecord] = []
    test: list[MatchRecord] = []

    for r in rows:
        m = MatchRecord(
            home_team=r[0], away_team=r[1],
            home_goals=r[2], away_goals=r[3], played_at=r[4],
        )
        if r[4] < cutoff:
            train.append(m)
        else:
            test.append(m)

    log.info("backtest.data_split", train=len(train), test=len(test), cutoff=cutoff.date().isoformat())

    if len(train) < 100:
        raise ValueError(f"Not enough training data: {len(train)} matches (need 100+)")
    if len(test) < 20:
        raise ValueError(f"Not enough test data: {len(test)} matches (need 20+)")

    # Fit model on training window
    fit = fit_model(train, as_of=cutoff)
    log.info("backtest.fitted", teams=len(fit.teams), home_adv=round(fit.home_advantage, 3), rho=round(fit.rho, 3))

    # Predict + score each test match
    scores: list[ScoringResult] = []
    skipped = 0

    for m in test:
        if m.home_team not in fit.attack or m.away_team not in fit.attack:
            skipped += 1
            continue
        try:
            pred = predict_match(fit, home_team=m.home_team, away_team=m.away_team)
        except Exception:
            skipped += 1
            continue

        sc = score_prediction(
            prob_home_win=pred.prob_home_win,
            prob_draw=pred.prob_draw,
            prob_away_win=pred.prob_away_win,
            prob_over_2_5=pred.prob_over_2_5,
            actual_home_goals=m.home_goals,
            actual_away_goals=m.away_goals,
        )
        scores.append(sc)

    if not scores:
        raise ValueError("No test matches could be scored")

    # Aggregate
    result_correct = sum(1 for s in scores if s.result_correct)
    ou_correct = sum(1 for s in scores if s.over_under_correct)
    avg_brier = sum(s.brier_score for s in scores) / len(scores)
    avg_ll = sum(s.log_loss for s in scores) / len(scores)

    return BacktestResult(
        competition=competition,
        train_cutoff=cutoff,
        train_count=len(train),
        test_count=len(scores),
        skipped_unknown_teams=skipped,
        result_accuracy=result_correct / len(scores),
        over_under_accuracy=ou_correct / len(scores),
        avg_brier=avg_brier,
        avg_log_loss=avg_ll,
        scores=scores,
    )
