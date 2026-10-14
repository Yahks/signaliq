"""Embedding task: vectorize normalized records in batches.

Uses Ollama's nomic-embed-text (768-dim, local, free).
Batches of 20 → ~12 batched calls for 239 records.
"""
from __future__ import annotations

import asyncio

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)

BATCH_SIZE = 20


async def _embed_org_async(org_id: str, limit: int | None = None) -> dict:
    from sqlalchemy import text as _t
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.ai.router import embed
    from packages.core.config import get_settings

    settings = get_settings()
    factory = get_session_factory()

    processed = 0
    failed = 0

    async with factory() as session:
        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)

        # Get the embedding model ID
        emb_model = (
            await session.execute(_t("""
                SELECT id::text FROM ai_models
                WHERE role = 'embedding' AND is_active = true
                ORDER BY created_at LIMIT 1
            """))
        ).first()
        if not emb_model:
            return {"error": "No active embedding model"}
        model_id = emb_model[0]

        # Fetch records without embeddings
        sql = """
            SELECT nr.id::text, nr.normalized_text
            FROM normalized_records nr
            LEFT JOIN embeddings e
              ON e.owner_type = 'normalized_record' AND e.owner_id = nr.id
            WHERE nr.org_id = :org_id AND e.id IS NULL
            ORDER BY nr.created_at
        """
        params: dict = {"org_id": org_id}
        if limit:
            sql += " LIMIT :lim"
            params["lim"] = limit

        rows = (await session.execute(_t(sql), params)).all()
        log.info("embed.batch_start", org_id=org_id, count=len(rows), batch_size=BATCH_SIZE)

        # Process in batches
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            ids = [r[0] for r in batch]
            texts = [r[1] for r in batch]

            try:
                vectors = await embed(texts=texts, org_id=org_id, purpose="embed.record")
            except Exception as exc:
                failed += len(batch)
                log.warning("embed.batch_failed", count=len(batch), error=str(exc)[:200])
                continue

            if len(vectors) != len(ids):
                failed += len(batch)
                log.warning("embed.mismatch", expected=len(ids), got=len(vectors))
                continue

            for rec_id, vec in zip(ids, vectors):
                try:
                    vec_str = "[" + ",".join(str(float(x)) for x in vec) + "]"
                    await session.execute(_t("""
                        INSERT INTO embeddings
                            (org_id, owner_type, owner_id, model_id, dim, vector)
                        VALUES
                            (:org_id, 'normalized_record', :owner_id, :model_id, :dim, CAST(:vec AS vector))
                        ON CONFLICT (owner_type, owner_id, model_id) DO NOTHING
                    """), {
                        "org_id": org_id,
                        "owner_id": rec_id,
                        "model_id": model_id,
                        "dim": len(vec),
                        "vec": vec_str,
                    })
                    processed += 1
                except Exception as exc:
                    failed += 1
                    log.warning("embed.insert_failed", record_id=rec_id, error=str(exc)[:200])

            await session.commit()
            log.info("embed.batch_done", done=processed, total=len(rows))


    # S.6.5.1 — chain: after embed completes, recluster the org's records
    # so clusters are rebuilt with the new vectors. Best-effort.
    if processed > 0:
        try:
            from apps.worker.tasks.cluster import recluster_org
            recluster_org.delay(str(org_id))
            log.info("embed.chained_cluster", org_id=org_id[:8], processed=processed)
        except Exception as exc:
            log.warning("embed.chain_cluster_failed", error=str(exc)[:200])

    return {"processed": processed, "failed": failed, "total": len(rows)}


@celery_app.task(name="tasks.embed_org", bind=True)
def embed_org(self, org_id: str, limit: int | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Vectorize all un-embedded records for an org."""
    return _run(_embed_org_async(org_id, limit))
