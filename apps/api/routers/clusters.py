"""Clusters: list and detail."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id
from apps.api.schemas.cluster import (
    ClusterDetailResponse,
    ClusterMemberSample,
    ClusterResponse,
)
from packages.core.error import NotFoundError
from packages.db.models.cluster import Cluster, ClusterMember
from packages.db.models.normalized_record import NormalizedRecord
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/clusters", tags=["clusters"])


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.get("", response_model=list[ClusterResponse])
async def list_clusters(
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(_scoped_session),
) -> list[ClusterResponse]:
    stmt = (
        select(Cluster)
        .order_by(Cluster.volume.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [ClusterResponse.model_validate(c) for c in rows]


@router.get("/{cluster_id}", response_model=ClusterDetailResponse)
async def get_cluster(
    cluster_id: str,
    session: AsyncSession = Depends(_scoped_session),
) -> ClusterDetailResponse:
    c_result = await session.execute(select(Cluster).where(Cluster.id == cluster_id))
    cluster = c_result.scalar_one_or_none()
    if not cluster:
        raise NotFoundError("Cluster not found")

    samples_stmt = (
        select(NormalizedRecord.id, NormalizedRecord.normalized_text)
        .join(ClusterMember, ClusterMember.normalized_record_id == NormalizedRecord.id)
        .where(ClusterMember.cluster_id == cluster_id)
        .limit(15)
    )
    samples = (await session.execute(samples_stmt)).all()

    return ClusterDetailResponse(
        id=cluster.id,
        title=cluster.title,
        summary=cluster.summary,
        volume=cluster.volume,
        sentiment_avg=cluster.sentiment_avg,
        first_seen_at=cluster.first_seen_at,
        last_seen_at=cluster.last_seen_at,
        sample_records=[
            ClusterMemberSample(record_id=str(s.id), normalized_text=s.normalized_text)
            for s in samples
        ],
        created_at=cluster.created_at,
    )
