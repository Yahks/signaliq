"""Pain points: list + detail."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id
from apps.api.schemas.pain_point import PainPointDetail, PainPointResponse
from packages.core.error import NotFoundError
from packages.db.models.pain_point import PainPoint
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/pain-points", tags=["pain-points"])


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.get("", response_model=list[PainPointResponse])
async def list_pain_points(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(_scoped_session),
) -> list[PainPointResponse]:
    stmt = select(PainPoint).order_by(PainPoint.severity.desc().nullslast()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [PainPointResponse.model_validate(p) for p in rows]


@router.get("/{pp_id}", response_model=PainPointDetail)
async def get_pain_point(
    pp_id: str,
    session: AsyncSession = Depends(_scoped_session),
) -> PainPointDetail:
    r = await session.execute(select(PainPoint).where(PainPoint.id == pp_id))
    pp = r.scalar_one_or_none()
    if not pp:
        raise NotFoundError("Pain point not found")

    # Sample records matching this pain point signal
    samples = (await session.execute(sql_text("""
        SELECT nr.id::text AS record_id, nr.normalized_text
        FROM ai_enrichments e
        JOIN normalized_records nr ON nr.id = e.normalized_record_id
        WHERE e.org_id = :org_id
          AND :title = ANY(e.pain_point_signals)
        LIMIT 10
    """), {"org_id": pp.org_id, "title": pp.title})).all()

    return PainPointDetail(
        id=pp.id,
        title=pp.title,
        description=pp.description,
        severity=pp.severity,
        first_seen_at=pp.first_seen_at,
        last_seen_at=pp.last_seen_at,
        sample_records=[{"record_id": s[0], "normalized_text": s[1]} for s in samples],
    )
