"""Daily brief generation task."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)


def _list_orgs() -> list[dict]:
    """Return active orgs."""
    from sqlalchemy import create_engine, text as sql_text
    from packages.core.config import get_settings

    settings = get_settings()
    from packages.db.session import get_sync_engine
    engine = get_sync_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql_text("""
                SELECT id::text, slug
                FROM orgs
                WHERE deleted_at IS NULL
            """)).all()
            return [{"id": r[0], "slug": r[1]} for r in rows]
    finally:
        engine.dispose()


async def _generate_for_org(
    org_id: str,
    *,
    window_hours: int = 24,
    force: bool = False,
) -> dict:
    """Generate + persist a daily brief for one org.

    If a brief already exists for today and `force=False`, skip.
    """
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.ai.brief.generator import generate_daily_brief
    from packages.core.config import get_settings

    settings = get_settings()
    factory = get_session_factory()

    today = datetime.now(UTC).date()

    async with factory() as session:
        await session.execute(sql_text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        from packages.core.context import set_context_from_org
        owner_id = await set_context_from_org(session, org_id)

        # Skip if we already have today's brief
        if not force:
            existing = (await session.execute(sql_text("""
                SELECT id::text FROM daily_briefs
                WHERE org_id = :oid AND brief_date = :d
                LIMIT 1
            """), {"oid": org_id, "d": today})).first()
            if existing:
                log.info("brief.skipped_existing", org_id=org_id[:8], brief_id=existing[0][:8])
                return {"status": "skipped", "reason": "already_generated_today"}

        # Generate
        result = await generate_daily_brief(session, org_id=org_id, window_hours=window_hours)
        if result is None:
            log.info("brief.no_data", org_id=org_id[:8])
            return {"status": "no_data"}

        # Persist
        row = (await session.execute(sql_text("""
            INSERT INTO daily_briefs
                (org_id, brief_date, block_slug, title, body, word_count,
                 event_ids, chain_ids, status)
            VALUES
                (:oid, :d, 'trading', :title, :body, :wc,
                 :event_ids, :chain_ids, 'draft')
            RETURNING id::text
        """), {
            "oid": org_id,
            "d": today,
            "title": result.title[:500],
            "body": result.body,
            "wc": result.word_count,
            "event_ids": result.event_ids,
            "chain_ids": result.chain_ids,
        })).first()

        await session.commit()

        # S.5.4a — charge for the brief just written.
        if owner_id and result.word_count:
            from packages.core.tokens import charge_for_content
            await charge_for_content(
                session, owner_id, kind="brief",
                word_count=int(result.word_count),
                ref_id=row[0], org_id=org_id,
            )

    return {
        "status": "ok",
        "brief_id": row[0],
        "word_count": result.word_count,
        "event_count": len(result.event_ids),
        "chain_count": len(result.chain_ids),
    }


@celery_app.task(name="tasks.generate_daily_brief", bind=True)
def generate_daily_brief_task(  # type: ignore[no-untyped-def]
    self,
    org_id: str,
    window_hours: int = 24,
    force: bool = False,
) -> dict:
    """Generate a daily brief for one org."""
    return _run(_generate_for_org(org_id, window_hours=window_hours, force=force))


@celery_app.task(name="tasks.generate_daily_briefs_all", bind=True)
def generate_daily_briefs_all(self, window_hours: int = 24) -> dict:  # type: ignore[no-untyped-def]
    """Generate daily briefs for every active org. Called by Beat."""
    orgs = _list_orgs()
    log.info("brief.batch_start", orgs=len(orgs))
    queued: list[str] = []
    for org in orgs:
        r = generate_daily_brief_task.delay(org["id"], window_hours, False)
        queued.append(r.id)
    return {"orgs": len(orgs), "tasks_queued": len(queued)}
