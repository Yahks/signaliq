"""Enrichment task: process all normalized records for an org (or a job)."""
from __future__ import annotations

import asyncio

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from apps.worker.celery_app import celery_app
from packages.core.config import get_settings
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)


def _sync_session() -> Session:
    settings = get_settings()
    from packages.db.session import get_sync_engine
    engine = get_sync_engine()
    return sessionmaker(bind=engine, expire_on_commit=False)()


async def _enrich_org_async(org_id: str, limit: int | None = None) -> dict:
    """Enrich all un-enriched normalized records for an org.

    Note: uses `SET` (session-scoped) not `SET LOCAL` (transaction-scoped)
    because the loop commits after each record, which would otherwise wipe
    the tenant context.
    """
    from sqlalchemy import text as _t
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.ai.enrichment.pipeline import enrich_text
    from packages.db.models.enrichment import AIEnrichment
    from packages.db.models.entity import Entity, EntityMention
    from packages.db.models.topic import Topic, TopicMembership

    settings = get_settings()
    factory = get_session_factory()

    processed = 0
    failed = 0

    async with factory() as session:
        # Session-scoped tenant context (persists across commits)
        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        # Resolve the org owner so LLM calls debit the right user
        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)

        # Fetch records that have no enrichment yet
        sql = _t("""
            SELECT nr.id::text, nr.normalized_text
            FROM normalized_records nr
            LEFT JOIN ai_enrichments e ON e.normalized_record_id = nr.id
            WHERE nr.org_id = :org_id AND e.id IS NULL
            ORDER BY nr.created_at
        """)
        params: dict = {"org_id": org_id}
        if limit:
            sql = _t(str(sql) + " LIMIT :lim")
            params["lim"] = limit

        rows = (await session.execute(sql, params)).all()
        log.info("enrich.batch_start", org_id=org_id, count=len(rows))

        for row in rows:
            record_id = row[0]
            text = row[1]
            try:
                result = await enrich_text(text, org_id=org_id, use_cache=True)

                # Validate parsed values before insert
                if result.parse_error:
                    log.warning("enrich.parse_error", record_id=record_id, err=result.parse_error[:200])
                    failed += 1
                    continue

                enrichment = AIEnrichment(
                    org_id=org_id,
                    normalized_record_id=record_id,
                    sentiment_label=result.sentiment_label,
                    sentiment_score=result.sentiment_score,
                    sentiment_confidence=result.sentiment_confidence,
                    emotions=result.emotions or {},
                    intent=result.intent,
                    intent_confidence=result.intent_confidence,
                    raw_topics=result.raw_topics or [],
                    raw_entities=result.raw_entities or [],
                    pain_point_signals=result.pain_point_signals or [],
                    feature_request_signals=result.feature_request_signals or [],
                    raw_response=result.raw_response or {},
                )
                session.add(enrichment)
                await session.flush()

                # Entities
                for ent in result.raw_entities:
                    name = str(ent.get("text", "")).strip()
                    etype = str(ent.get("type", "other")).strip()[:32] or "other"
                    conf = float(ent.get("confidence", 0.5))
                    if not name:
                        continue
                    existing = (
                        await session.execute(
                            _t("""
                                SELECT id::text FROM entities
                                WHERE org_id = :org_id AND canonical_name = :name AND type = :type
                                LIMIT 1
                            """),
                            {"org_id": org_id, "name": name[:255], "type": etype},
                        )
                    ).first()
                    if existing:
                        eid = existing[0]
                    else:
                        new_e = Entity(org_id=org_id, canonical_name=name[:255], type=etype, metadata_={})
                        session.add(new_e)
                        await session.flush()
                        eid = new_e.id

                    session.add(EntityMention(
                        org_id=org_id,
                        entity_id=eid,
                        normalized_record_id=record_id,
                        surface_form=name[:255],
                        confidence=conf,
                        sentiment_score=result.sentiment_score,
                    ))

                # Topics
                for topic_name in result.raw_topics:
                    topic_name = str(topic_name)[:255]
                    if not topic_name:
                        continue
                    existing_t = (
                        await session.execute(
                            _t("""
                                SELECT id::text FROM topics
                                WHERE org_id = :org_id AND name = :name
                                LIMIT 1
                            """),
                            {"org_id": org_id, "name": topic_name},
                        )
                    ).first()
                    if existing_t:
                        tid = existing_t[0]
                    else:
                        new_t = Topic(org_id=org_id, name=topic_name)
                        session.add(new_t)
                        await session.flush()
                        tid = new_t.id

                    session.add(TopicMembership(
                        org_id=org_id,
                        topic_id=tid,
                        normalized_record_id=record_id,
                        assigned_by="llm",
                    ))

                await session.commit()
                processed += 1
                if processed % 5 == 0:
                    log.info("enrich.progress", processed=processed, total=len(rows))

            except Exception as exc:
                failed += 1
                await session.rollback()
                # Re-establish tenant context after rollback
                await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
                await session.commit()
                log.warning("enrich.record_failed", record_id=record_id, error=str(exc)[:300])


    # S.6.5.1 — chain: after enrich completes, embed the records. Then
    # embed will chain to cluster. Best-effort: a chaining failure never
    # breaks the enrichment task itself.
    if processed > 0:
        try:
            from apps.worker.tasks.embed import embed_org
            embed_org.delay(str(org_id))
            log.info("enrich.chained_embed", org_id=org_id[:8], processed=processed)
        except Exception as exc:
            log.warning("enrich.chain_embed_failed", error=str(exc)[:200])

    return {"processed": processed, "failed": failed, "total": len(rows)}


@celery_app.task(name="tasks.enrich_org", bind=True)
def enrich_org(self, org_id: str, limit: int | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Enrich all un-enriched records for an org."""
    return _run(_enrich_org_async(org_id, limit))
