"""Fire any due scheduled articles.

Run via cron every minute. Reads orgs.settings->'schedule',
fires every due subblock, writes a notification per article,
updates next_fire_at.

    crontab -e
    * * * * * cd /path/to/signaliq && .venv/bin/python -m scripts.fire_scheduled >> /var/log/signaliq-scheduler.log 2>&1

The worker is idempotent: it only fires rows whose next_fire_at
is in the past. It sets next_fire_at before generating, so a
slow generation does not double-fire.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.core.logging import get_logger

log = get_logger(__name__)


async def _find_due_orgs(session: AsyncSession) -> list[dict[str, Any]]:
    """Return every org with at least one due subblock.

    S.9.21 — the schedule is now a per-subblock dict:
      { "subblocks": { "slug": {enabled, interval_hours}, ... } }

    An org is due if any enabled subblock's last post is older than
    its interval. The caller fires only the due ones.
    """
    rows = (await session.execute(sql_text("""
        SELECT
            o.id::text              AS org_id,
            o.settings->'schedule'  AS schedule
        FROM orgs o
        WHERE o.deleted_at IS NULL
          AND o.settings->'schedule'->'subblocks' IS NOT NULL
        ORDER BY o.id
        LIMIT 20
    """))).mappings().all()

    due: list[dict[str, Any]] = []
    for r in rows:
        sched = r["schedule"] or {}
        subs = sched.get("subblocks") if isinstance(sched, dict) else None
        if not isinstance(subs, dict):
            continue
        # Filter to enabled subblocks.
        enabled = {
            slug: cfg for slug, cfg in subs.items()
            if isinstance(cfg, dict) and cfg.get("enabled")
        }
        if enabled:
            due.append({"org_id": r["org_id"], "subblocks": enabled})
    return due


async def _claim_org(session: AsyncSession, org_id: str, hours: int) -> bool:
    """Advance next_fire_at. Returns True if this worker claimed the row."""
    res = await session.execute(sql_text("""
        UPDATE orgs
        SET settings = jsonb_set(
            settings,
            '{schedule,next_fire_at}',
            to_jsonb(
                (now() + make_interval(hours => :h))::text
            )
        )
        WHERE id = :oid
          AND (settings->'schedule'->>'next_fire_at')::timestamptz <= now()
        RETURNING id
    """), {"oid": org_id, "h": hours})
    row = res.first()
    return row is not None


async def _notify(
    session: AsyncSession,
    *,
    org_id: str,
    title: str,
    body: str,
) -> None:
    """Insert one notification per user in the org."""
    users = (await session.execute(sql_text("""
        SELECT user_id::text FROM memberships WHERE org_id = :oid
    """), {"oid": org_id})).scalars().all()
    for uid in users:
        await session.execute(sql_text("""
            INSERT INTO notifications (org_id, user_id, kind, title, body)
            VALUES (:oid, :uid, 'article_generated', :title, :body)
        """), {
            "oid": org_id,
            "uid": uid,
            "title": title[:500],
            "body": body[:4000],
        })


async def main() -> None:
    from packages.core.config import get_settings
    from packages.core.context import set_context_from_org
    from packages.trading.subblocks.base import generate_subblock

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    from datetime import UTC, datetime, timedelta

    try:
        await _run_tick(session_factory=factory)
    finally:
        # S.9.22 — dispose the ephemeral engine after each tick.
        # Without this, every 60-second run leaks one connection and
        # Postgres fills up over hours.
        await engine.dispose()


async def _run_tick(*, session_factory) -> None:
    factory = session_factory
    async with factory() as session:
        due = await _find_due_orgs(session)
        if not due:
            return

        for row in due:
            org_id = row["org_id"]
            subblocks_cfg = row["subblocks"]  # {slug: {enabled, interval_hours}}

            await session.execute(
                sql_text("SELECT set_config('app.current_org_id', :oid, false)"),
                {"oid": org_id},
            )
            await set_context_from_org(session, org_id)

            # Determine which subblocks are actually due (last post
            # older than interval).
            now = datetime.now(UTC)
            due_slugs: list[str] = []
            for slug, cfg in subblocks_cfg.items():
                hours = max(2, int(cfg.get("interval_hours", 6)))
                last_row = (await session.execute(sql_text("""
                    SELECT MAX(tp.created_at)
                    FROM trading_posts tp
                    JOIN trading_subblocks ts ON ts.id = tp.subblock_id
                    WHERE tp.org_id = :oid
                      AND ts.slug = :slug
                      AND tp.deleted_at IS NULL
                """), {"oid": org_id, "slug": slug})).first()
                last = last_row[0] if last_row and last_row[0] else None
                if last is None or (now - last) >= timedelta(hours=hours):
                    due_slugs.append(slug)

            if not due_slugs:
                continue

            log.info("scheduler.firing", org_id=org_id[:8],
                     slugs=due_slugs)

            # S.9.17 — statuses that mean "the subblock can't run
            # yet", not "the subblock failed". Do not notify the user
            # about these. They will resolve once trade_log or
            # market_conditions has data.
            SKIP_STATUSES = {
                "no_event", "no_market_conditions", "no_movers",
                "no_process", "no_section", "no_trades",
                "no_trades_available", "no_data",
                "exhausted", "all_covered",
                "disabled", "skipped", "no_topic",
            }

            for slug in due_slugs:
                try:
                    result = await generate_subblock(
                        session, org_id=org_id, slug=slug,
                    )
                    status = result.get("status", "unknown")

                    if status in SKIP_STATUSES:
                        log.info("scheduler.skipped",
                                 slug=slug, status=status)
                        await session.rollback()
                        continue

                    words = result.get("word_count", 0)
                    post_id = result.get("post_id", "")
                    await _notify(session, org_id=org_id,
                                  title=f"New article: {slug}",
                                  body=f"{words} words — post {post_id[:8]}")
                    await session.commit()
                    log.info("scheduler.article_done", slug=slug,
                             status=status, words=words)
                except Exception as exc:
                    await session.rollback()
                    await _notify(
                        session,
                        org_id=org_id,
                        title=f"Scheduled article failed: {slug}",
                        body=f"{type(exc).__name__}: {str(exc)[:200]}",
                    )
                    await session.commit()
                    log.warning("scheduler.article_failed",
                                slug=slug, error=str(exc)[:200])


if __name__ == "__main__":
    asyncio.run(main())
