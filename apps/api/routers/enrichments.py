"""Enrichment, entities, topics read endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id
from apps.api.schemas.enrichment import (
    EnrichmentResponse,
    EntityDetailResponse,
    EntityResponse,
    TopicResponse,
)
from packages.core.error import NotFoundError
from packages.db.models.enrichment import AIEnrichment
from packages.db.models.entity import Entity, EntityMention
from packages.db.models.topic import Topic, TopicMembership
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api", tags=["enrichment"])


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


# ---------- Enrichments ----------

@router.get("/enrichments/{record_id}", response_model=EnrichmentResponse)
async def get_enrichment(
    record_id: str,
    session: AsyncSession = Depends(_scoped_session),
) -> EnrichmentResponse:
    """Get the AI enrichment for a normalized record."""
    result = await session.execute(
        select(AIEnrichment).where(AIEnrichment.normalized_record_id == record_id)
    )
    enr = result.scalar_one_or_none()
    if not enr:
        raise NotFoundError("Enrichment not found for this record")
    return EnrichmentResponse.model_validate(enr)


# ---------- Entities ----------

@router.get("/entities", response_model=list[EntityResponse])
async def list_entities(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(_scoped_session),
) -> list[EntityResponse]:
    # Count mentions per entity
    stmt = (
        select(
            Entity.id,
            Entity.canonical_name,
            Entity.type,
            Entity.created_at,
            func.count(EntityMention.id).label("mention_count"),
        )
        .outerjoin(EntityMention, EntityMention.entity_id == Entity.id)
        .group_by(Entity.id)
        .order_by(func.count(EntityMention.id).desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()
    return [
        EntityResponse(
            id=r.id, canonical_name=r.canonical_name, type=r.type,
            mention_count=r.mention_count, created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/entities/{entity_id}", response_model=EntityDetailResponse)
async def get_entity(
    entity_id: str,
    session: AsyncSession = Depends(_scoped_session),
) -> EntityDetailResponse:
    e_result = await session.execute(select(Entity).where(Entity.id == entity_id))
    ent = e_result.scalar_one_or_none()
    if not ent:
        raise NotFoundError("Entity not found")

    # Aggregates
    agg = await session.execute(
        sql_text("""
            SELECT
                count(*) as mention_count,
                avg(em.sentiment_score) as avg_sentiment
            FROM entity_mentions em
            WHERE em.entity_id = :eid AND em.org_id = current_setting('app.current_org_id', true)::uuid
        """),
        {"eid": entity_id},
    )
    agg_row = agg.first()
    mention_count = int(agg_row[0] or 0)
    avg_sent = float(agg_row[1]) if agg_row[1] is not None else None

    # Sample mentions with text
    samples_stmt = (
        select(
            EntityMention.surface_form,
            EntityMention.confidence,
            EntityMention.sentiment_score,
            EntityMention.normalized_record_id,
        )
        .where(EntityMention.entity_id == entity_id)
        .order_by(EntityMention.created_at.desc())
        .limit(10)
    )
    samples = (await session.execute(samples_stmt)).all()

    sample_dicts = [
        {
            "surface_form": s.surface_form,
            "confidence": s.confidence,
            "sentiment_score": s.sentiment_score,
            "record_id": s.normalized_record_id,
        }
        for s in samples
    ]

    return EntityDetailResponse(
        id=ent.id,
        canonical_name=ent.canonical_name,
        type=ent.type,
        mention_count=mention_count,
        avg_sentiment=avg_sent,
        sample_mentions=sample_dicts,
        created_at=ent.created_at,
    )


# ---------- Topics ----------

@router.get("/topics", response_model=list[TopicResponse])
async def list_topics(
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(_scoped_session),
) -> list[TopicResponse]:
    stmt = (
        select(
            Topic.id,
            Topic.name,
            Topic.created_at,
            func.count(TopicMembership.id).label("record_count"),
        )
        .outerjoin(TopicMembership, TopicMembership.topic_id == Topic.id)
        .where(Topic.is_hidden.is_(False))
        .group_by(Topic.id)
        .order_by(func.count(TopicMembership.id).desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        TopicResponse(
            id=r.id, name=r.name, record_count=r.record_count, created_at=r.created_at,
        )
        for r in rows
    ]
