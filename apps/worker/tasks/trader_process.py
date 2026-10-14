"""Periodic task — draft the trader process for each org."""
from __future__ import annotations

import asyncio

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)


@celery_app.task(name="tasks.trader_process_draft")
def trader_process_draft() -> dict:
    return _run(_run())


async def _run() -> dict:
    from sqlalchemy import text as sql_text
    from packages.db.session import get_session_factory
    from packages.db.tenant import set_current_org
    from packages.trading.process_drafter import draft_for_org

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(sql_text(
            "SELECT id::text FROM orgs WHERE deleted_at IS NULL"
        ))).all()

    total = 0
    for r in rows:
        org_id = r[0]
        async with factory() as session:
            await set_current_org(session, org_id)
            total += await draft_for_org(session, org_id)

    log.info("trader_process_draft.done", orgs=len(rows), drafts=total)
    return {"status": "ok", "orgs": len(rows), "drafts": total}
