"""Production blend predictor: 50% model + 50% market.

Empirically validated on 380 real EPL matches (Phase 10.5b):
- Pure model:  51.32% accuracy, 0.5830 Brier
- Pure market: 54.21% accuracy, 0.5787 Brier
- 50/50 blend: 55.26% accuracy, 0.5718 Brier ← production config

Blend formula:
    p_blend[k] = 0.5 * p_model[k] + 0.5 * p_market[k]  for each outcome k

Then renormalize to guard against floating-point drift.

If market odds aren't available for a match, we fall back to pure model
and flag the prediction as "model_only" in the audit trail.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.prediction.dixon_coles import FitResult, Prediction, predict_match

log = get_logger(__name__)

MODEL_WEIGHT = 0.5
MARKET_WEIGHT = 0.5


@dataclass
class MarketOdds:
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    prob_over_2_5: float | None
    bookmakers_used: int
    source_slug: str


@dataclass
class BlendPrediction:
    # Final blended probabilities
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    prob_over_2_5: float
    prob_under_2_5: float
    # Extended markets (from model, not blended with market — market
    # rarely offers these lines on free tier)
    prob_over_1_5: float | None = None
    prob_under_1_5: float | None = None
    prob_over_3_5: float | None = None
    prob_under_3_5: float | None = None
    prob_btts_yes: float | None = None
    prob_btts_no: float | None = None
    home_goals_dist: list[float] | None = None
    away_goals_dist: list[float] | None = None
    top_scorelines: list | None = None
    # Corner prediction (from corners.py, blended in separately)
    expected_home_corners: float | None = None
    expected_away_corners: float | None = None
    expected_total_corners: float | None = None
    # Provenance
    model: Prediction | None = None
    market: MarketOdds | None = None
    blend_mode: str = "model_only"
    confidence: str = "low"
    data_quality: float | None = None
    # Internal — surfaces corner prediction confidence for snapshots/UI
    _corners_confidence: str | None = None


def _blend_two(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    w_a: float,
    w_b: float,
) -> tuple[float, float, float]:
    """Weighted average of two W/D/L triples, then renormalize."""
    p_h = w_a * a[0] + w_b * b[0]
    p_d = w_a * a[1] + w_b * b[1]
    p_a = w_a * a[2] + w_b * b[2]
    total = p_h + p_d + p_a
    if total > 0:
        p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
    return p_h, p_d, p_a


def _confidence_from_blend(
    *,
    prob_range: float,
    data_quality: float,
    market_bookmakers: int,
) -> str:
    """Confidence is higher when the outcome is clear and the market is deep."""
    clarity = min(prob_range / 0.5, 1.0)
    market_depth = min(market_bookmakers / 30.0, 1.0)
    score = 0.4 * clarity + 0.4 * market_depth + 0.2 * data_quality
    if score >= 0.7:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


async def load_market_odds(
    session: AsyncSession,
    *,
    match_id: str,
) -> MarketOdds | None:
    """Look up the most recent market odds for a match from external_predictions."""
    row = (await session.execute(sql_text("""
        SELECT
            ep.prob_home_win, ep.prob_draw, ep.prob_away_win,
            ep.prob_over_2_5,
            eps.slug,
            COALESCE((ep.raw_payload->>'bookmaker_count')::int, 0) AS bookmaker_count
        FROM external_predictions ep
        JOIN external_prediction_sources eps ON eps.id = ep.source_id
        WHERE ep.match_id = :mid
          AND eps.kind = 'bookmaker'
        ORDER BY ep.fetched_at DESC
        LIMIT 1
    """), {"mid": match_id})).first()

    if not row:
        return None

    return MarketOdds(
        prob_home_win=float(row[0]),
        prob_draw=float(row[1]),
        prob_away_win=float(row[2]),
        prob_over_2_5=float(row[3]) if row[3] is not None else None,
        bookmakers_used=int(row[5] or 0),
        source_slug=row[4],
    )


async def blend_prediction(
    session: AsyncSession,
    fit: FitResult,
    *,
    match_id: str,
    home_team: str,
    away_team: str,
    competition: str,
) -> BlendPrediction:
    """Production prediction: 50/50 blend of model + market if both exist."""
    from packages.prediction.scoreline import predict_scoreline
    from packages.prediction.corners import predict_corners

    # Model prediction (extended — includes O/U 1.5, 3.5, BTTS, goals dist)
    sc = predict_scoreline(fit, home_team=home_team, away_team=away_team)
    model = predict_match(fit, home_team=home_team, away_team=away_team)

    # Market prediction
    market = await load_market_odds(session, match_id=match_id)

    # Corner prediction (separate signal, best-effort)
    corner_pred = None
    try:
        corner_pred = await predict_corners(
            session, home_team=home_team, away_team=away_team, competition=competition,
        )
    except Exception as exc:
        log.warning(
            "blend.corners_failed",
            home=home_team, away=away_team,
            error=str(exc)[:200],
        )

    if market is None:
        # Fall back to model only
        p_h, p_d, p_a = model.prob_home_win, model.prob_draw, model.prob_away_win
        p_over = model.prob_over_2_5
        mode = "model_only"
    else:
        p_h, p_d, p_a = _blend_two(
            (model.prob_home_win, model.prob_draw, model.prob_away_win),
            (market.prob_home_win, market.prob_draw, market.prob_away_win),
            MODEL_WEIGHT, MARKET_WEIGHT,
        )
        # Blend O/U if market has it, else use model
        if market.prob_over_2_5 is not None:
            p_over = 0.5 * model.prob_over_2_5 + 0.5 * market.prob_over_2_5
        else:
            p_over = model.prob_over_2_5
        mode = "50_50"

    prob_range = max(p_h, p_d, p_a) - min(p_h, p_d, p_a)
    bookmakers = market.bookmakers_used if market else 0
    confidence = _confidence_from_blend(
        prob_range=prob_range,
        data_quality=model.data_quality,
        market_bookmakers=bookmakers,
    )

    return BlendPrediction(
        prob_home_win=p_h,
        prob_draw=p_d,
        prob_away_win=p_a,
        prob_over_2_5=p_over,
        prob_under_2_5=1.0 - p_over,
        # Extended markets — model-only (market rarely offers these on free tier)
        prob_over_1_5=sc.prob_over_1_5,
        prob_under_1_5=sc.prob_under_1_5,
        prob_over_3_5=sc.prob_over_3_5,
        prob_under_3_5=sc.prob_under_3_5,
        prob_btts_yes=sc.prob_btts_yes,
        prob_btts_no=sc.prob_btts_no,
        home_goals_dist=sc.home_goals_dist,
        away_goals_dist=sc.away_goals_dist,
        top_scorelines=sc.top_scorelines,
        # Corner prediction
        expected_home_corners=corner_pred.expected_home_corners if corner_pred else None,
        expected_away_corners=corner_pred.expected_away_corners if corner_pred else None,
        expected_total_corners=corner_pred.expected_total_corners if corner_pred else None,
        # Store corner confidence for snapshot / UI low-confidence badge
        _corners_confidence=(corner_pred.confidence if corner_pred else None),
        model=model,
        market=market,
        blend_mode=mode,
        confidence=confidence,
        data_quality=model.data_quality,
    )
