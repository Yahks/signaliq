"""External prediction source reliability endpoints.

Read-only leaderboard of external sources (bookmakers, internal models,
prediction sites) and how well they have actually performed against
finished match results.

Public data — no tenant scope. Any authenticated user can see this.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_user_id
from apps.api.schemas.prediction import (
    SourceReliabilityItem,
    SourceReliabilityList,
)
from packages.db.session import get_session

router = APIRouter(prefix="/api/predictions", tags=["predictions"])

LOW_SAMPLE_THRESHOLD = 10


@router.get("/sources", response_model=SourceReliabilityList)
async def list_sources(
    window_days: int = Query(default=30, ge=1, le=365),
    _user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(get_session),
) -> SourceReliabilityList:
    """Reliability leaderboard for all enabled external sources.

    Sources with no reliability row yet appear with null metrics so the
    UI can show them as 'awaiting data' rather than hiding them.
    """
    rows = (await session.execute(sql_text("""
        SELECT
            eps.slug,
            eps.name,
            eps.kind,
            eps.enabled,
            sr.predictions_scored,
            sr.result_accuracy,
            sr.avg_brier_score,
            sr.reliability_weight,
            sr.computed_at
        FROM external_prediction_sources eps
        LEFT JOIN LATERAL (
            SELECT predictions_scored, result_accuracy,
                   avg_brier_score, reliability_weight, computed_at
            FROM source_reliability
            WHERE source_id = eps.id
              AND window_days = :wd
            ORDER BY computed_at DESC
            LIMIT 1
        ) sr ON true
        WHERE eps.enabled = true
        ORDER BY
            sr.reliability_weight DESC NULLS LAST,
            eps.slug ASC
    """), {"wd": window_days})).all()

    items = []
    for r in rows:
        scored = int(r[4]) if r[4] is not None else None
        items.append(SourceReliabilityItem(
            slug=r[0],
            name=r[1],
            kind=r[2],
            enabled=bool(r[3]),
            predictions_scored=scored,
            result_accuracy=float(r[5]) if r[5] is not None else None,
            avg_brier_score=float(r[6]) if r[6] is not None else None,
            reliability_weight=float(r[7]) if r[7] is not None else None,
            computed_at=r[8],
            low_sample=(scored is None or scored < LOW_SAMPLE_THRESHOLD),
        ))

    return SourceReliabilityList(window_days=window_days, items=items)
