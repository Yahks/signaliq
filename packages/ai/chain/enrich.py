"""Enrich seed events with related events from the org's existing pool.

Vector search on news_events.embedding (vector(768), HNSW cosine index).
Seeds are embedded with the same embedding role used elsewhere; the mean
of the seed embeddings becomes the query vector.

Falls back gracefully: if embeddings fail or no matches are found, returns
the seeds unchanged.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.chain.event_extractor import EventInput
from packages.ai.router import embed
from packages.core.logging import get_logger

log = get_logger(__name__)


async def enrich_with_related_events(
    session: AsyncSession,
    org_id: str,
    seed_events: list[EventInput],
    *,
    max_total: int = 20,
) -> list[EventInput]:
    """Return seed_events plus up to (max_total - len(seeds)) related events.

    Related = nearest neighbours in the org's news_events.embedding space,
    excluding any event whose headline exactly matches a seed.
    """
    if not seed_events:
        return []
    if len(seed_events) >= max_total:
        return seed_events[:max_total]

    # Embed each seed — headline + summary joined
    seed_texts = [f"{e.headline}. {e.summary}" for e in seed_events]
    try:
        vecs = await embed(texts=seed_texts, org_id=org_id, purpose="chain.enrich_seeds")
    except Exception as exc:
        log.warning("enrich.embed_failed", error=str(exc)[:200])
        return seed_events

    if not vecs or len(vecs) != len(seed_texts):
        log.warning("enrich.embed_mismatch", expected=len(seed_texts), got=len(vecs or []))
        return seed_events

    # Mean vector (component-wise)
    dim = len(vecs[0])
    mean = [0.0] * dim
    for v in vecs:
        for i, x in enumerate(v):
            mean[i] += float(x)
    n = len(vecs)
    mean = [x / n for x in mean]

    # Format as pgvector literal
    vec_literal = "[" + ",".join(f"{x:.6f}" for x in mean) + "]"

    # Exclude seeds by exact headline match
    seed_headlines = [e.headline for e in seed_events]

    need = max_total - len(seed_events)
    try:
        rows = (await session.execute(sql_text("""
            SELECT id::text, headline, summary, occurred_at, actors,
                   event_type, status, confidence
            FROM news_events
            WHERE org_id = :oid
              AND embedding IS NOT NULL
              AND headline <> ALL(:seed_headlines)
            ORDER BY embedding <=> CAST(:vec AS vector(768))
            LIMIT :limit
        """), {
            "oid": org_id,
            "seed_headlines": seed_headlines,
            "vec": vec_literal,
            "limit": need,
        })).all()
    except Exception as exc:
        log.warning("enrich.vector_search_failed", error=str(exc)[:200])
        return seed_events

    related: list[EventInput] = []
    for r in rows:
        try:
            related.append(EventInput(
                external_id=None,
                headline=str(r[1])[:500],
                summary=str(r[2])[:4000],
                occurred_at=r[3],
                reported_at=None,
                location=None,
                actors=list(r[4] or []),
                event_type=str(r[5] or "other"),
                status=str(r[6] or "fact"),
                confidence=float(r[7] if r[7] is not None else 0.5),
            ))
        except Exception as exc:
            log.warning("enrich.row_skip", error=str(exc)[:120])

    log.info(
        "enrich.done",
        seeds=len(seed_events),
        related=len(related),
        total=len(seed_events) + len(related),
    )
    return seed_events + related
