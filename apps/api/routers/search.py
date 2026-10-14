"""Semantic search endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id
from apps.api.schemas.search import (
    SearchHit,
    SemanticSearchRequest,
    SemanticSearchResponse,
)
from packages.ai.router import embed
from packages.core.error import ValidationError
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/search", tags=["search"])


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.post("/semantic", response_model=SemanticSearchResponse)
async def semantic_search(
    body: SemanticSearchRequest,
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped_session),
) -> SemanticSearchResponse:
    """Embed the query, find nearest records by cosine similarity."""
    # 1. Embed the query
    try:
        vectors = await embed(texts=[body.query], org_id=org_id, purpose="search.query")
    except Exception as exc:
        raise ValidationError(f"Embedding failed: {exc}") from exc

    if not vectors:
        raise ValidationError("No embedding produced")

    query_vec = "[" + ",".join(str(float(x)) for x in vectors[0]) + "]"

    # 2. Cosine similarity search using pgvector HNSW index
    #    <=> is cosine distance (0 = identical, 2 = opposite); similarity = 1 - distance
    # Use DISTINCT ON to dedup by normalized_text, keeping the highest-similarity row.
    # See: we had 239 records but only 148 distinct texts (91 duplicates).
    sql = sql_text("""
        WITH ranked AS (
            SELECT
                e.owner_id::text AS record_id,
                nr.normalized_text,
                nr.detected_language,
                nr.created_at,
                1 - (e.vector <=> CAST(:q AS vector)) AS similarity,
                row_number() OVER (
                    PARTITION BY nr.normalized_text
                    ORDER BY e.vector <=> CAST(:q AS vector)
                ) AS rn
            FROM embeddings e
            JOIN normalized_records nr ON nr.id = e.owner_id
            WHERE e.org_id = :org_id
              AND e.owner_type = 'normalized_record'
        )
        SELECT record_id, normalized_text, detected_language, created_at, similarity
        FROM ranked
        WHERE rn = 1
        ORDER BY similarity DESC
        LIMIT :lim
    """)
    rows = (await session.execute(sql, {
        "q": query_vec,
        "org_id": org_id,
        "lim": body.limit,
    })).all()

    hits = [
        SearchHit(
            record_id=r.record_id,
            normalized_text=r.normalized_text,
            detected_language=r.detected_language,
            similarity=float(r.similarity),
            created_at=r.created_at,
        )
        for r in rows
        if r.similarity >= body.min_similarity
    ]

    return SemanticSearchResponse(
        query=body.query,
        hits=hits,
        total=len(hits),
    )


@router.get("/similar/{record_id}", response_model=SemanticSearchResponse)
async def similar_records(
    record_id: str,
    limit: int = 10,
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped_session),
) -> SemanticSearchResponse:
    """Find records semantically similar to a given record."""
    # 1. Get the record's existing embedding
    row = (await session.execute(sql_text("""
        SELECT e.vector::text AS vec, nr.normalized_text
        FROM embeddings e
        JOIN normalized_records nr ON nr.id = e.owner_id
        WHERE e.org_id = :org_id
          AND e.owner_type = 'normalized_record'
          AND e.owner_id = :record_id
        LIMIT 1
    """), {"org_id": org_id, "record_id": record_id})).first()

    if not row:
        raise ValidationError("Record has no embedding yet")

    query_vec, source_text = row[0], row[1]

    # 2. Find nearest excluding self
    rows = (await session.execute(sql_text("""
        WITH ranked AS (
            SELECT
                e.owner_id::text AS record_id,
                nr.normalized_text,
                nr.detected_language,
                nr.created_at,
                1 - (e.vector <=> CAST(:q AS vector)) AS similarity,
                row_number() OVER (
                    PARTITION BY nr.normalized_text
                    ORDER BY e.vector <=> CAST(:q AS vector)
                ) AS rn
            FROM embeddings e
            JOIN normalized_records nr ON nr.id = e.owner_id
            WHERE e.org_id = :org_id
              AND e.owner_type = 'normalized_record'
              AND e.owner_id != :record_id
        )
        SELECT record_id, normalized_text, detected_language, created_at, similarity
        FROM ranked
        WHERE rn = 1
        ORDER BY similarity DESC
        LIMIT :lim
    """), {"q": query_vec, "org_id": org_id, "record_id": record_id, "lim": limit})).all()

    hits = [
        SearchHit(
            record_id=r.record_id,
            normalized_text=r.normalized_text,
            detected_language=r.detected_language,
            similarity=float(r.similarity),
            created_at=r.created_at,
        )
        for r in rows
    ]

    return SemanticSearchResponse(
        query=f"Similar to: {source_text[:80]}...",
        hits=hits,
        total=len(hits),
    )
