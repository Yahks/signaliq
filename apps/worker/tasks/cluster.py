"""Clustering task: group records into semantic clusters."""
from __future__ import annotations

import asyncio

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)


async def _recluster_org_async(org_id: str) -> dict:
    """Recluster all records for an org.

    Strategy: delete existing clusters for the org, rebuild from scratch.
    For 148 records this is <5s. For 10M records we'd need incremental clustering.
    """
    from sqlalchemy import text as _t
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.core.config import get_settings
    from packages.intelligence.clustering import cluster_vectors

    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)

        # Fetch embeddings + texts for non-duplicate records only
        rows = (await session.execute(_t("""
            SELECT
                nr.id::text,
                nr.normalized_text,
                nr.created_at,
                e.vector::text
            FROM normalized_records nr
            JOIN embeddings e
              ON e.owner_type = 'normalized_record' AND e.owner_id = nr.id
            WHERE nr.org_id = :org_id
              AND nr.is_duplicate = false
            ORDER BY nr.created_at
        """), {"org_id": org_id})).all()

        log.info("cluster.batch_start", org_id=org_id, count=len(rows))

        if len(rows) < 3:
            log.warning("cluster.too_few_records", count=len(rows))
            return {"clusters": 0, "noise": len(rows)}

        # Parse vectors
        record_ids = [r[0] for r in rows]
        texts = [r[1] for r in rows]
        created_ats = [r[2] for r in rows]
        vectors = [_parse_vec(r[3]) for r in rows]

        # Cluster
        labels = cluster_vectors(vectors, min_cluster_size=3, min_samples=2)

        # Group
        groups: dict[int, list[int]] = {}
        noise = 0
        for i, label in enumerate(labels):
            if label == -1:
                noise += 1
                continue
            groups.setdefault(label, []).append(i)

        log.info("cluster.groups", groups=len(groups), noise=noise)

        # Wipe existing clusters for this org
        await session.execute(_t("DELETE FROM clusters WHERE org_id = :org_id"), {"org_id": org_id})

        # Create new clusters
        for label, indices in groups.items():
            # Compute avg sentiment from enrichments
            ids_in_cluster = [record_ids[i] for i in indices]
            avg_sent_row = (await session.execute(_t("""
                SELECT avg(sentiment_score)
                FROM ai_enrichments
                WHERE org_id = :org_id
                  AND normalized_record_id = ANY(:ids)
                  AND sentiment_score IS NOT NULL
            """), {"org_id": org_id, "ids": ids_in_cluster})).first()
            avg_sent = float(avg_sent_row[0]) if avg_sent_row and avg_sent_row[0] is not None else None

            first = min(created_ats[i] for i in indices)
            last = max(created_ats[i] for i in indices)

            # Placeholder title — will be filled by LLM labeler
            title = f"Cluster {label + 1} ({len(indices)} records)"

            cluster_row = (await session.execute(_t("""
                INSERT INTO clusters (org_id, title, summary, volume, sentiment_avg, first_seen_at, last_seen_at)
                VALUES (:org_id, :title, :summary, :volume, :sentiment_avg, :first, :last)
                RETURNING id::text
            """), {
                "org_id": org_id,
                "title": title,
                "summary": None,
                "volume": len(indices),
                "sentiment_avg": avg_sent,
                "first": first,
                "last": last,
            })).first()
            cluster_id = cluster_row[0]

            for i in indices:
                await session.execute(_t("""
                    INSERT INTO cluster_members (org_id, cluster_id, normalized_record_id)
                    VALUES (:org_id, :cluster_id, :record_id)
                """), {
                    "org_id": org_id,
                    "cluster_id": cluster_id,
                    "record_id": record_ids[i],
                })

            # LLM label this cluster (best-effort, async)
            await session.commit()

            # Label after commit (so it gets its own ai_calls row)
            await _label_cluster(cluster_id, [texts[i] for i in indices], org_id)

    return {"clusters": len(groups), "noise": noise, "total": len(rows)}


def _parse_vec(s: str) -> list[float]:
    """Parse pgvector text representation '[1.0,2.0,...]' to list[float]."""
    s = s.strip("[]")
    return [float(x) for x in s.split(",")]


async def _label_cluster(cluster_id: str, sample_texts: list[str], org_id: str) -> None:
    """Ask the LLM for a title + summary of this cluster."""
    from sqlalchemy import text as _t
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.ai.router import complete
    from packages.core.config import get_settings

    # Take up to 5 sample texts
    samples = sample_texts[:5]
    joined = "\n\n---\n\n".join(t[:500] for t in samples)

    from packages.ai.prompts.loader import render_with_version

    prompt, _pv = render_with_version(
        "cluster.topic_label",
        count=len(samples),
        examples=joined,
    )

    try:
        raw = await complete(
            role="fast",
            system=None,
            user=prompt,
            org_id=org_id,
            prompt_version_id=_pv,
            purpose="cluster.label",
            json_mode=True,
            max_tokens=200,
            use_cache=False,
        )
        import json
        parsed = json.loads(raw)
        title = str(parsed.get("title", ""))[:255]
        summary = str(parsed.get("summary", ""))

        settings = get_settings()
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
            await session.execute(_t("""
                UPDATE clusters SET title = :title, summary = :summary, updated_at = now()
                WHERE id = :cid AND org_id = :org_id
            """), {"title": title, "summary": summary, "cid": cluster_id, "org_id": org_id})
            await session.commit()
        log.info("cluster.labeled", cluster_id=cluster_id[:8], title=title)
    except Exception as exc:
        log.warning("cluster.label_failed", cluster_id=cluster_id[:8], error=str(exc)[:200])


@celery_app.task(name="tasks.recluster_org", bind=True)
def recluster_org(self, org_id: str) -> dict:  # type: ignore[no-untyped-def]
    return _run(_recluster_org_async(org_id))
