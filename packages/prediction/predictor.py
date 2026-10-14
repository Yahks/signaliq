"""Predict upcoming fixtures using the Dixon-Coles model.

Flow:
  1. Load training data from DB (finished matches)
  2. Fit model (in-memory, cached by config hash)
  3. Find upcoming fixtures for selected competitions
  4. For each fixture with known teams, generate a prediction
  5. Persist to `predictions` table with full provenance
  6. Never re-predict the same match with the same model_version
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.prediction.blend import blend_prediction
from packages.prediction.dixon_coles import (
    DEFAULT_REG_STRENGTH,
    DEFAULT_XI,
    FitResult,
    Prediction,
    fit_model,
    predict_match,
)
from packages.prediction.loader import load_training_data

log = get_logger(__name__)

MODEL_VERSION = "blend_50_50_v1"
MIN_TRAINING_MATCHES = 200  # refuse to fit on less than this


@dataclass
class StoredPrediction:
    match_id: str
    prediction_id: str
    was_created: bool
    outcome: Prediction


def _config_hash(
    xi: float,
    reg_strength: float,
    training_count: int,
    as_of: datetime,
) -> str:
    """Deterministic hash of model config + training window.

    Same inputs → same hash → same prediction. Enables dedup and reproducibility.
    """
    payload = json.dumps({
        "model": MODEL_VERSION,
        "xi": xi,
        "reg_strength": reg_strength,
        "training_count": training_count,
        "as_of_day": as_of.date().isoformat(),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _confidence_from_quality(data_quality: float, prob_range: float) -> str:
    """High confidence when data is good and the outcome is clear."""
    score = data_quality * 0.6 + min(prob_range / 0.5, 1.0) * 0.4
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


async def fit_and_cache(
    session: AsyncSession,
    *,
    competition: str,
    xi: float = DEFAULT_XI,
    reg_strength: float = DEFAULT_REG_STRENGTH,
) -> tuple[FitResult, str]:
    """Load training data, fit the model, return (fit, config_hash)."""
    training = await load_training_data(session, competition=competition, max_matches=5000)
    if len(training) < MIN_TRAINING_MATCHES:
        raise ValueError(
            f"Not enough training data for {competition}: {len(training)} matches "
            f"(min {MIN_TRAINING_MATCHES})"
        )

    as_of = datetime.now(UTC)
    fit = fit_model(training, as_of=as_of, xi=xi, reg_strength=reg_strength)
    config_hash = _config_hash(xi, reg_strength, len(training), as_of)

    log.info(
        "predictor.fit_done",
        competition=competition,
        teams=len(fit.teams),
        matches=len(training),
        home_advantage=round(fit.home_advantage, 3),
        rho=round(fit.rho, 3),
        config_hash=config_hash[:12],
    )
    return fit, config_hash


async def predict_upcoming_fixtures(
    session: AsyncSession,
    *,
    competition: str = "EPL",
    days_ahead: int = 14,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Predict all upcoming fixtures in the next `days_ahead` days.

    - Fits the model on the latest training data.
    - For each fixture with both teams in the training set, generates a prediction.
    - Skips fixtures already predicted with the same config_hash (unless force_refresh).
    """
    now = datetime.now(UTC)
    cutoff = now + timedelta(days=days_ahead)

    # Fit model
    try:
        fit, config_hash = await fit_and_cache(session, competition=competition)
    except ValueError as exc:
        log.warning("predictor.no_training_data", error=str(exc))
        return {"status": "no_data", "error": str(exc), "predictions_created": 0}

    # Find upcoming fixtures
    fixtures = (await session.execute(sql_text("""
        SELECT id::text, home_team, away_team, kickoff_at, external_id
        FROM matches
        WHERE status = 'scheduled'
          AND competition = :comp
          AND kickoff_at >= :now
          AND kickoff_at <= :cutoff
        ORDER BY kickoff_at
    """), {"comp": competition, "now": now, "cutoff": cutoff})).all()

    log.info("predictor.fixtures_found", competition=competition, count=len(fixtures))

    created = 0
    skipped_no_teams = 0
    skipped_existing = 0
    failed = 0

    for match_id, home, away, kickoff, external_id in fixtures:
        # Skip fixtures with teams not in training data (promoted or new)
        if home not in fit.attack or away not in fit.attack:
            skipped_no_teams += 1
            log.info("predictor.unknown_team", home=home, away=away)
            continue

        # Skip if already predicted with same config_hash
        if not force_refresh:
            existing = (await session.execute(sql_text("""
                SELECT id::text FROM predictions
                WHERE match_id = :mid
                  AND model_version = :mv
                  AND model_config_hash = :ch
                LIMIT 1
            """), {"mid": match_id, "mv": MODEL_VERSION, "ch": config_hash})).first()
            if existing:
                skipped_existing += 1
                continue

        # Generate blend prediction (50% model + 50% market)
        try:
            bp = await blend_prediction(
                session, fit,
                match_id=match_id,
                home_team=home,
                away_team=away,
                competition=competition,
            )
        except Exception as exc:
            failed += 1
            log.warning("predictor.predict_failed", match_id=match_id[:8], error=str(exc)[:200])
            continue

        # Convert blended probabilities to fair odds
        fair_home = 1.0 / bp.prob_home_win if bp.prob_home_win > 0 else None
        fair_draw = 1.0 / bp.prob_draw if bp.prob_draw > 0 else None
        fair_away = 1.0 / bp.prob_away_win if bp.prob_away_win > 0 else None

        confidence = bp.confidence

        # Snapshot of inputs for reproducibility — includes market & model details
        inputs_snapshot = {
            "home_team": home,
            "away_team": away,
            "home_attack": fit.attack[home],
            "home_defense": fit.defense[home],
            "away_attack": fit.attack[away],
            "away_defense": fit.defense[away],
            "home_advantage": fit.home_advantage,
            "rho": fit.rho,
            "n_training": fit.n_matches,
            "blend_mode": bp.blend_mode,
            "model_probs": {
                "home": bp.model.prob_home_win if bp.model else None,
                "draw": bp.model.prob_draw if bp.model else None,
                "away": bp.model.prob_away_win if bp.model else None,
            },
            "market_probs": {
                "home": bp.market.prob_home_win if bp.market else None,
                "draw": bp.market.prob_draw if bp.market else None,
                "away": bp.market.prob_away_win if bp.market else None,
                "bookmakers": bp.market.bookmakers_used if bp.market else None,
            },
            # Phase 10.9a/b — extended market + corner provenance
            "extended_markets": {
                "source": "scoreline_model",
                "prob_over_1_5": bp.prob_over_1_5,
                "prob_over_3_5": bp.prob_over_3_5,
                "prob_btts_yes": bp.prob_btts_yes,
            },
            "corners": {
                "confidence": getattr(bp, "_corners_confidence", None),
                "expected_home": bp.expected_home_corners,
                "expected_away": bp.expected_away_corners,
                "expected_total": bp.expected_total_corners,
            },
        }

        # Insert prediction
        await session.execute(sql_text("""
            INSERT INTO predictions (
                match_id, model_version, model_config_hash,
                prob_home_win, prob_draw, prob_away_win,
                expected_home_goals, expected_away_goals,
                prob_over_2_5, prob_under_2_5,
                prob_over_1_5, prob_under_1_5, prob_over_3_5, prob_under_3_5,
                prob_btts_yes, prob_btts_no,
                home_goals_dist, away_goals_dist, top_scorelines,
                expected_home_corners, expected_away_corners, expected_total_corners,
                confidence, data_quality,
                fair_odds_home, fair_odds_draw, fair_odds_away,
                inputs_snapshot, is_published, published_at
            ) VALUES (
                :mid, :mv, :ch,
                :ph, :pd, :pa,
                :ehg, :eag,
                :pov, :pun,
                :pov15, :pun15, :pov35, :pun35, :pbtts, :pbttsno,
                CAST(:hgd AS jsonb), CAST(:agd AS jsonb), CAST(:tops AS jsonb),
                :ehc, :eac, :etc,
                :conf, :dq,
                :fh, :fd, :fa,
                CAST(:snap AS jsonb), true, now()
            )
            ON CONFLICT (match_id, model_version) DO UPDATE
            SET model_config_hash = EXCLUDED.model_config_hash,
                prob_home_win = EXCLUDED.prob_home_win,
                prob_draw = EXCLUDED.prob_draw,
                prob_away_win = EXCLUDED.prob_away_win,
                expected_home_goals = EXCLUDED.expected_home_goals,
                expected_away_goals = EXCLUDED.expected_away_goals,
                prob_over_2_5 = EXCLUDED.prob_over_2_5,
                prob_under_2_5 = EXCLUDED.prob_under_2_5,
                prob_over_1_5 = EXCLUDED.prob_over_1_5,
                prob_under_1_5 = EXCLUDED.prob_under_1_5,
                prob_over_3_5 = EXCLUDED.prob_over_3_5,
                prob_under_3_5 = EXCLUDED.prob_under_3_5,
                prob_btts_yes = EXCLUDED.prob_btts_yes,
                prob_btts_no = EXCLUDED.prob_btts_no,
                home_goals_dist = EXCLUDED.home_goals_dist,
                away_goals_dist = EXCLUDED.away_goals_dist,
                top_scorelines = EXCLUDED.top_scorelines,
                expected_home_corners = EXCLUDED.expected_home_corners,
                expected_away_corners = EXCLUDED.expected_away_corners,
                expected_total_corners = EXCLUDED.expected_total_corners,
                confidence = EXCLUDED.confidence,
                data_quality = EXCLUDED.data_quality,
                fair_odds_home = EXCLUDED.fair_odds_home,
                fair_odds_draw = EXCLUDED.fair_odds_draw,
                fair_odds_away = EXCLUDED.fair_odds_away,
                inputs_snapshot = EXCLUDED.inputs_snapshot,
                published_at = now()
        """), {
            "mid": match_id, "mv": MODEL_VERSION, "ch": config_hash,
            "ph": bp.prob_home_win, "pd": bp.prob_draw, "pa": bp.prob_away_win,
            "ehg": bp.model.expected_home_goals if bp.model else None,
            "eag": bp.model.expected_away_goals if bp.model else None,
            "pov": bp.prob_over_2_5, "pun": bp.prob_under_2_5,
            "pov15": bp.prob_over_1_5,
            "pun15": bp.prob_under_1_5,
            "pov35": bp.prob_over_3_5,
            "pun35": bp.prob_under_3_5,
            "pbtts": bp.prob_btts_yes,
            "pbttsno": bp.prob_btts_no,
            "hgd": json.dumps(bp.home_goals_dist or []),
            "agd": json.dumps(bp.away_goals_dist or []),
            "tops": json.dumps(bp.top_scorelines or []),
            "ehc": bp.expected_home_corners,
            "eac": bp.expected_away_corners,
            "etc": bp.expected_total_corners,
            "conf": confidence,
            "dq": bp.data_quality,
            "fh": fair_home, "fd": fair_draw, "fa": fair_away,
            "snap": json.dumps(inputs_snapshot),
        })
        created += 1

    await session.commit()

    log.info(
        "predictor.predictions_created",
        competition=competition,
        created=created,
        skipped_no_teams=skipped_no_teams,
        skipped_existing=skipped_existing,
        failed=failed,
    )

    return {
        "status": "ok",
        "competition": competition,
        "fixtures_found": len(fixtures),
        "predictions_created": created,
        "skipped_unknown_teams": skipped_no_teams,
        "skipped_existing": skipped_existing,
        "failed": failed,
        "config_hash": config_hash[:12],
    }
