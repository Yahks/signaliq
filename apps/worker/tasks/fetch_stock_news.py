"""Fetch live stock news → extract events → optionally build chain.

Two modes:
  - fetch_only: fetch + extract, save events to news_events (block=trading)
  - fetch_and_build: same, then trigger the chain pipeline automatically
"""
from __future__ import annotations

import asyncio

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)


async def _fetch_stock_news_async(
    org_id: str,
    *,
    tickers: list[str] | None = None,
    auto_build: bool = False,
    subtopic_slug: str = "us_stocks",
    max_events: int = 30,
) -> dict:
    """Fetch live stock news, extract events, persist them."""
    from sqlalchemy import text as _t
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory
    from dateutil import parser as dateparser

    from packages.blocks.loader import load_block_by_slug
    from packages.core.config import get_settings
    from packages.ingestion.sources.stock_news import fetch_all
    from packages.ingestion.sources.stock_to_events import rows_to_events

    settings = get_settings()
    factory = get_session_factory()

    # 1. Load trading block
    block = load_block_by_slug("trading", org_id=org_id)

    # 2. Fetch live rows (network)
    log.info("stock_news.fetch_start", tickers=tickers or "default")
    # Under --pool=solo we run one task at a time in one process.
    # asyncio.to_thread spawns a separate thread whose event-loop
    # context conflicts with the process-wide loop that owns the
    # asyncpg engine pool, producing "attached to a different loop".
    # Just call it directly.
    rows = fetch_all(
        tickers=tickers,
        include_general=True,
        include_sec=True,
        fetch_content=False,
    )
    log.info("stock_news.fetch_done", rows=len(rows))

    # 3. Sort by catalyst flag + recency, take top N for extraction
    catalyst_rows = [r for r in rows if r.catalyst_flag]
    other_rows = [r for r in rows if not r.catalyst_flag]
    # Sort each group by published_at desc (None last)
    def sort_key(r):
        return r.published_at or ""
    catalyst_rows.sort(key=sort_key, reverse=True)
    other_rows.sort(key=sort_key, reverse=True)

    selected = (catalyst_rows + other_rows)[:max_events]
    log.info("stock_news.selected_for_extraction", count=len(selected), catalysts=len(catalyst_rows))

    if not selected:
            return {"status": "empty", "fetched": 0, "extracted": 0}

    # 4. Extract structured events (LLM)
    events = await rows_to_events(selected, org_id=org_id)
    log.info("stock_news.extracted", count=len(events))

    # 5. Persist to news_events with block_id
    def _dt(v):
        if not v:
            return None
        if hasattr(v, "year"):
            return v
        try:
            return dateparser.parse(str(v))
        except Exception:
            return None

    inserted_ids: list[str] = []
    reattached_ids: list[str] = []  # events that joined an existing chain
    skipped_dupes = 0

    # Reattach helper
    from packages.ai.chain.reattach import reattach_event

    async with factory() as session:
        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)
        await session.commit()

        for ev in events:
            ext_id = ev.get("external_id") or ""
            # Dedup: if we already have this external_id for this org, skip
            if ext_id:
                exists = (await session.execute(
                    _t("""
                        SELECT 1 FROM news_events
                        WHERE org_id = :org_id AND external_id = :ext
                        LIMIT 1
                    """),
                    {"org_id": org_id, "ext": ext_id},
                )).first()
                if exists:
                    skipped_dupes += 1
                    continue

            # Only store events with real content
            if not ev.get("headline") or not ev.get("summary"):
                continue

            srcs = ev.get("sources") or [{}]
            primary = srcs[0] if srcs else {}

            row = (await session.execute(_t("""
                INSERT INTO news_events
                    (org_id, block_id, subtopic_slug, headline, summary, occurred_at,
                     reported_at, location, actors, event_type, status, confidence, external_id)
                VALUES
                    (:org_id, :block_id, :subtopic_slug, :headline, :summary, :occurred_at,
                     :reported_at, :location, :actors, :event_type, :status, :confidence, :external_id)
                RETURNING id::text
            """), {
                "org_id": org_id,
                "block_id": block.id,
                "subtopic_slug": subtopic_slug,
                "headline": ev["headline"][:500],
                "summary": ev["summary"],
                "occurred_at": _dt(ev.get("occurred_at")),
                "reported_at": _dt(ev.get("reported_at")),
                "location": ev.get("location"),
                "actors": ev.get("actors") or [],
                "event_type": ev.get("event_type"),
                "status": ev.get("status", "fact"),
                "confidence": float(ev.get("confidence", 0.7)),
                "external_id": ext_id,
            })).first()
            event_id = row[0]

            # Try to attach to an existing chain. If it works, don't count
            # this event as "new". Otherwise, add it to the new-chain pool.
            try:
                decision = await reattach_event(
                    session,
                    event_id=event_id,
                    org_id=org_id,
                    block_id=block.id,
                )
                if decision and decision.attach:
                    reattached_ids.append(event_id)
                    log.info(
                        "fetch_stock_news.reattached",
                        event_id=event_id[:8],
                        chain_id=decision.chain_id[:8],
                    )
                    continue  # skip adding to the new-chain pool
            except Exception as exc:
                log.warning(
                    "fetch_stock_news.reattach_error",
                    event_id=event_id[:8],
                    error=str(exc)[:200],
                )

            inserted_ids.append(event_id)

        await session.commit()

    log.info(
        "stock_news.persisted",
        inserted=len(inserted_ids),
        reattached=len(reattached_ids),
        skipped_dupes=skipped_dupes,
    )

    result = {
        "status": "ok",
        "fetched": len(rows),
        "extracted": len(events),
        "inserted": len(inserted_ids),
        "reattached": len(reattached_ids),
        "skipped_dupes": skipped_dupes,
    }

    # 6. Optionally trigger a chain build with the newly inserted events
    #    Events that reattached to existing chains are excluded (they're already
    #    part of a chain — no need to create a new one).
    if auto_build and len(inserted_ids) >= 2:
        from apps.worker.tasks.news_chains import build_chain
        # Load the events back to pass to build_chain
        async with factory() as session:
            await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
            await session.commit()
            rows2 = (await session.execute(_t("""
                SELECT id::text, headline, summary, occurred_at, reported_at,
                       location, actors, event_type, status, confidence
                FROM news_events
                WHERE org_id = :org_id AND id = ANY(:ids)
            """), {"org_id": org_id, "ids": inserted_ids})).all()

        events_for_chain = []
        for r in rows2:
            events_for_chain.append({
                "_placeholder_id": f"ph_{r[0][:8]}",
                "external_id": r[0],
                "headline": r[1],
                "summary": r[2],
                "occurred_at": r[3].isoformat() if r[3] else None,
                "reported_at": r[4].isoformat() if r[4] else None,
                "location": r[5],
                "actors": list(r[6] or []),
                "event_type": r[7],
                "status": r[8],
                "confidence": float(r[9]) if r[9] is not None else 0.7,
            })

        build_chain.delay(
            org_id,
            events_for_chain,
            "trading",
            subtopic_slug,
            None,
        )
        result["chain_build_queued"] = True
        result["chain_events"] = len(events_for_chain)

    return result


@celery_app.task(name="tasks.fetch_stock_news", bind=True)
def fetch_stock_news(  # type: ignore[no-untyped-def]
    self,
    org_id: str,
    tickers: list[str] | None = None,
    auto_build: bool = False,
    subtopic_slug: str = "us_stocks",
    max_events: int = 30,
) -> dict:
    return _run(_fetch_stock_news_async(
        org_id,
        tickers=tickers,
        auto_build=auto_build,
        subtopic_slug=subtopic_slug,
        max_events=max_events,
    ))
