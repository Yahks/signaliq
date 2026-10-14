"""Public prediction endpoints.

These endpoints expose predictions and accuracy data. Because predictions
are about PUBLIC football matches (not tenant-scoped data), these routes
are intentionally NOT tenant-restricted. Any authenticated user can see
all predictions — the underlying content is public knowledge.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, current_user_id
from apps.api.schemas.prediction import (
    PerformanceSummary,
    PredictionResponse,
    UpcomingPredictionsList,
)
from packages.db.session import get_session

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


async def _public_session(
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    """Session without tenant context — predictions are public data."""
    return session


@router.get("/upcoming", response_model=UpcomingPredictionsList)
async def list_upcoming(
    competition: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user_id: str = Depends(current_user_id),
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_public_session),
) -> UpcomingPredictionsList:
    """Upcoming published predictions, ordered by kickoff.

    Charges 50 site tokens per distinct match per UTC day (idempotent).
    """
    params: dict = {"limit": limit}
    comp_clause = ""
    if competition:
        comp_clause = "AND m.competition = :comp"
        params["comp"] = competition

    # Return the LATEST published prediction per match.
    # Old versions stay in the DB for backtest history but are hidden from the UI.
    rows = (await session.execute(sql_text(f"""
        WITH latest AS (
            SELECT DISTINCT ON (match_id)
                id, match_id, prob_home_win, prob_draw, prob_away_win,
                expected_home_goals, expected_away_goals,
                prob_over_2_5, prob_under_2_5,
                prob_over_1_5, prob_under_1_5,
                prob_over_3_5, prob_under_3_5,
                prob_btts_yes, prob_btts_no,
                home_goals_dist, away_goals_dist, top_scorelines,
                expected_home_corners, expected_away_corners, expected_total_corners,
                confidence, data_quality,
                fair_odds_home, fair_odds_draw, fair_odds_away,
                model_version, published_at, inputs_snapshot
            FROM predictions
            WHERE is_published = true
            ORDER BY match_id, published_at DESC NULLS LAST, created_at DESC
        )
        SELECT
            p.id::text, p.match_id::text,
            m.home_team, m.away_team, m.competition, m.kickoff_at, m.status,
            p.prob_home_win, p.prob_draw, p.prob_away_win,
            p.expected_home_goals, p.expected_away_goals,
            p.prob_over_2_5, p.prob_under_2_5,
            p.prob_over_1_5, p.prob_under_1_5,
            p.prob_over_3_5, p.prob_under_3_5,
            p.prob_btts_yes, p.prob_btts_no,
            p.home_goals_dist, p.away_goals_dist, p.top_scorelines,
            p.expected_home_corners, p.expected_away_corners, p.expected_total_corners,
            p.confidence, p.data_quality,
            p.fair_odds_home, p.fair_odds_draw, p.fair_odds_away,
            p.model_version, p.published_at,
            m.home_goals, m.away_goals,
            r.result_correct, r.brier_score,
            p.inputs_snapshot
        FROM latest p
        JOIN matches m ON m.id = p.match_id
        LEFT JOIN prediction_results r ON r.prediction_id = p.id
        WHERE (m.status = 'scheduled' OR m.status = 'finished')
          {comp_clause}
        ORDER BY m.kickoff_at ASC
        LIMIT :limit
    """), params)).all()

    # S.5.4a — charge per prediction block (idempotent per UTC day).
    from packages.core.tokens import charge_for_prediction_if_new
    for row in rows:
        match_id = row[1]
        if match_id:
            await charge_for_prediction_if_new(
                session, user_id, org_id=org_id, match_id=match_id,
            )

    from apps.api.schemas.prediction import MarketProbs, ModelProbs

    def _corners_confidence_from_snapshot(snapshot: dict | None) -> str | None:
        if not snapshot:
            return None
        # blend.py stores corner prediction metadata under "corners"
        c = snapshot.get("corners")
        if isinstance(c, dict):
            return c.get("confidence")
        return None

    def _parse_blend(snapshot: dict | None) -> dict:
        if not snapshot:
            return {}
        mp = snapshot.get("model_probs") or {}
        mkp = snapshot.get("market_probs") or {}
        return {
            "blend_mode": snapshot.get("blend_mode"),
            "model_probs": ModelProbs(
                home=mp.get("home"), draw=mp.get("draw"), away=mp.get("away"),
            ) if mp else None,
            "market_probs": MarketProbs(
                home=mkp.get("home"), draw=mkp.get("draw"), away=mkp.get("away"),
                bookmakers=mkp.get("bookmakers"),
            ) if mkp else None,
        }

    items = [
        PredictionResponse(
            id=r[0], match_id=r[1],
            home_team=r[2], away_team=r[3], competition=r[4],
            kickoff_at=r[5], match_status=r[6],
            prob_home_win=r[7], prob_draw=r[8], prob_away_win=r[9],
            expected_home_goals=r[10], expected_away_goals=r[11],
            prob_over_2_5=r[12], prob_under_2_5=r[13],
            prob_over_1_5=r[14], prob_under_1_5=r[15],
            prob_over_3_5=r[16], prob_under_3_5=r[17],
            prob_btts_yes=r[18], prob_btts_no=r[19],
            home_goals_dist=r[20], away_goals_dist=r[21], top_scorelines=r[22],
            expected_home_corners=r[23], expected_away_corners=r[24],
            expected_total_corners=r[25],
            confidence=r[26], data_quality=r[27],
            fair_odds_home=r[28], fair_odds_draw=r[29], fair_odds_away=r[30],
            model_version=r[31], published_at=r[32],
            actual_home_goals=r[33], actual_away_goals=r[34],
            result_correct=r[35], brier_score=r[36],
            corners_confidence=_corners_confidence_from_snapshot(r[37]),
            **_parse_blend(r[37]),
        )
        for r in rows
    ]

    return UpcomingPredictionsList(items=items, total=len(items))


@router.get("/performance", response_model=PerformanceSummary)
async def performance_summary(
    model_version: str | None = Query(default=None),
    session: AsyncSession = Depends(_public_session),
) -> PerformanceSummary:
    """Public accuracy metrics. Anyone can see how the model is doing."""
    where_clause = ""
    params: dict = {}
    if model_version:
        where_clause = "WHERE p.model_version = :mv"
        params["mv"] = model_version

    # Overall
    overall = (await session.execute(sql_text(f"""
        SELECT
            count(DISTINCT p.id) AS total,
            count(DISTINCT r.prediction_id) AS scored,
            avg(r.result_correct::int) AS result_acc,
            avg(r.over_under_correct::int) AS ou_acc,
            avg(r.brier_score) AS avg_brier,
            avg(r.log_loss) AS avg_ll
        FROM predictions p
        LEFT JOIN prediction_results r ON r.prediction_id = p.id
        {where_clause}
    """), params)).first()

    # Always-home baseline: fraction of scored matches where home won
    baseline_home = (await session.execute(sql_text("""
        SELECT
            avg((r.actual_result = 'home_win')::int)
        FROM prediction_results r
    """))).scalar()

    # Rolling 7-day and 30-day windows
    async def _window(days: int) -> dict | None:
        r = (await session.execute(sql_text(f"""
            SELECT
                count(*) AS scored,
                avg(r.result_correct::int) AS result_acc,
                avg(r.brier_score) AS avg_brier
            FROM prediction_results r
            WHERE r.scored_at >= now() - INTERVAL '{days} days'
        """))).first()
        if not r or r[0] == 0:
            return None
        return {
            "scored": int(r[0]),
            "result_accuracy": float(r[1]) if r[1] is not None else None,
            "avg_brier_score": float(r[2]) if r[2] is not None else None,
        }

    last_7 = await _window(7)
    last_30 = await _window(30)

    return PerformanceSummary(
        model_version=model_version or "all",
        predictions_total=int(overall[0] or 0),
        predictions_scored=int(overall[1] or 0),
        result_accuracy=float(overall[2]) if overall[2] is not None else None,
        over_under_accuracy=float(overall[3]) if overall[3] is not None else None,
        avg_brier_score=float(overall[4]) if overall[4] is not None else None,
        avg_log_loss=float(overall[5]) if overall[5] is not None else None,
        baseline_always_home=float(baseline_home) if baseline_home is not None else None,
        last_7_days=last_7,
        last_30_days=last_30,
    )
