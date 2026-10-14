"""Dashboard summary: aggregated counts and top items for the home view."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.get("/summary")
async def summary(
    session: AsyncSession = Depends(_scoped_session),
) -> dict:
    """Single endpoint returning everything the dashboard needs."""
    # Counts
    counts = (await session.execute(sql_text("""
        SELECT
          (SELECT count(*) FROM normalized_records WHERE is_duplicate = false) AS records,
          (SELECT count(*) FROM ai_enrichments) AS enriched,
          (SELECT count(*) FROM entities) AS entities,
          (SELECT count(*) FROM topics) AS topics,
          (SELECT count(*) FROM clusters) AS clusters,
          (SELECT count(*) FROM pain_points) AS pain_points,
          (SELECT count(*) FROM sources WHERE deleted_at IS NULL) AS sources
    """))).first()

    # Sentiment distribution
    sentiment = (await session.execute(sql_text("""
        SELECT sentiment_label, count(*) AS cnt
        FROM ai_enrichments
        WHERE sentiment_label IS NOT NULL
        GROUP BY sentiment_label
        ORDER BY cnt DESC
    """))).all()

    # Top entities by mentions
    entities = (await session.execute(sql_text("""
        SELECT e.canonical_name, e.type, count(em.id) AS mentions
        FROM entities e
        LEFT JOIN entity_mentions em ON em.entity_id = e.id
        GROUP BY e.id, e.canonical_name, e.type
        ORDER BY mentions DESC
        LIMIT 10
    """))).all()

    # Top topics by record count
    topics = (await session.execute(sql_text("""
        SELECT t.name, count(tm.id) AS records
        FROM topics t
        LEFT JOIN topic_memberships tm ON tm.topic_id = t.id
        GROUP BY t.id, t.name
        ORDER BY records DESC
        LIMIT 10
    """))).all()

    # Top clusters
    clusters = (await session.execute(sql_text("""
        SELECT id::text, title, volume, sentiment_avg
        FROM clusters
        ORDER BY volume DESC
        LIMIT 5
    """))).all()

    # Top pain points
    pain_points = (await session.execute(sql_text("""
        SELECT id::text, title, severity
        FROM pain_points
        ORDER BY severity DESC NULLS LAST
        LIMIT 5
    """))).all()

    # Recent jobs
    jobs = (await session.execute(sql_text("""
        SELECT id::text, status, processed_records, created_at
        FROM ingestion_jobs
        ORDER BY created_at DESC
        LIMIT 5
    """))).all()

    return {
        "counts": {
            "records": counts[0],
            "enriched": counts[1],
            "entities": counts[2],
            "topics": counts[3],
            "clusters": counts[4],
            "pain_points": counts[5],
            "sources": counts[6],
        },
        "sentiment": [
            {"label": r[0], "count": r[1]} for r in sentiment
        ],
        "top_entities": [
            {"name": r[0], "type": r[1], "mentions": r[2]} for r in entities
        ],
        "top_topics": [
            {"name": r[0], "records": r[1]} for r in topics
        ],
        "top_clusters": [
            {"id": r[0], "title": r[1], "volume": r[2],
             "sentiment_avg": r[3]} for r in clusters
        ],
        "top_pain_points": [
            {"id": r[0], "title": r[1], "severity": r[2]} for r in pain_points
        ],
        "recent_jobs": [
            {"id": r[0], "status": r[1], "processed": r[2],
             "created_at": r[3].isoformat() if r[3] else None} for r in jobs
        ],
    }
