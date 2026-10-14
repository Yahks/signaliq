"""Pain point aggregation task."""
from __future__ import annotations

import asyncio

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)


async def _aggregate_org_async(org_id: str) -> dict:
    from sqlalchemy import text as _t
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.core.config import get_settings
    from packages.intelligence.pain_points import aggregate_signals

    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)

        rows = (await session.execute(_t("""
            SELECT
                e.normalized_record_id::text,
                e.pain_point_signals,
                e.sentiment_score,
                nr.created_at
            FROM ai_enrichments e
            JOIN normalized_records nr ON nr.id = e.normalized_record_id
            WHERE e.org_id = :org_id
              AND nr.is_duplicate = false
              AND array_length(e.pain_point_signals, 1) > 0
        """), {"org_id": org_id})).all()

        log.info("pain_points.input", count=len(rows))

        tuples = [(r[0], list(r[1] or []), r[2], r[3]) for r in rows]
        aggregates = aggregate_signals(tuples)
        log.info("pain_points.aggregated", count=len(aggregates))

        # Wipe and rebuild
        await session.execute(_t("DELETE FROM pain_points WHERE org_id = :org_id"), {"org_id": org_id})

        for agg in aggregates:
            await session.execute(_t("""
                INSERT INTO pain_points (org_id, title, severity, first_seen_at, last_seen_at, description)
                VALUES (:org_id, :title, :severity, :first, :last, :desc)
            """), {
                "org_id": org_id,
                "title": agg.title,
                "severity": agg.severity,
                "first": agg.first_seen_at,
                "last": agg.last_seen_at,
                "desc": f"Frequency: {agg.frequency}, avg_sentiment: {agg.avg_sentiment}",
            })

        await session.commit()

    return {"pain_points": len(aggregates), "input_records": len(rows)}


@celery_app.task(name="tasks.aggregate_pain_points_org", bind=True)
def aggregate_pain_points_org(self, org_id: str) -> dict:  # type: ignore[no-untyped-def]
    return _run(_aggregate_org_async(org_id))
