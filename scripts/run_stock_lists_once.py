"""Manually run the stock_lists generator once against the dev DB.

Prints the resulting post (title, word count, first 600 chars of body).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    from packages.core.config import get_settings
    from packages.trading.subblocks.base import generate_subblock

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Pick the target org: slug from argv[1], else first org with
    # at least one active AI credential. The old default of "first
    # non-deleted org" picked system orgs that have no credentials,
    # so generation died at the router's Gate 5.
    target_slug = sys.argv[1] if len(sys.argv) > 1 else None
    async with factory() as session:
        if target_slug:
            org = (await session.execute(text(
                "SELECT id::text, slug FROM orgs "
                "WHERE deleted_at IS NULL AND slug = :s LIMIT 1"
            ), {"s": target_slug})).first()
            if not org:
                print(f"❌ no org with slug: {target_slug}")
                sys.exit(1)
        else:
            org = (await session.execute(text(
                "SELECT id::text, slug FROM orgs o "
                "WHERE o.deleted_at IS NULL "
                "AND EXISTS (SELECT 1 FROM ai_credentials c "
                "            WHERE c.org_id = o.id AND c.is_active) "
                "ORDER BY o.created_at LIMIT 1"
            ))).first()
            if not org:
                print("❌ no org with active credentials found")
                sys.exit(1)
    org_id, org_slug = org
    print(f"Org: {org_slug} ({org_id[:8]}…)")

    async with factory() as session:
        await session.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        # S.8.48 — set the ContextVars the AI router reads.
        # Without this the router raises NoContextError before
        # the LLM call. The Celery worker does this via
        # packages.core.context.set_context_from_org; the
        # one-shot runner was written before the router
        # enforced the context gate.
        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)
        print("Running stock_lists generator…\n")
        result = await generate_subblock(session, org_id=org_id, slug="stock_lists")
        print(f"Result: {result}\n")

    # Fetch the just-created post
    async with factory() as session:
        await session.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()
        row = (await session.execute(text("""
            SELECT title, body, word_count, symbols, source_urls
            FROM trading_posts
            WHERE org_id = :o
            ORDER BY created_at DESC LIMIT 1
        """), {"o": org_id})).first()
        if row:
            title, body, wc, symbols, urls = row
            print("=" * 70)
            print(f"TITLE: {title}")
            print(f"WORDS: {wc}")
            print(f"SYMBOLS: {len(symbols or [])}  e.g. {(symbols or [])[:5]}")
            print(f"SOURCES: {urls}")
            print("=" * 70)
            print("BODY (first 1500 chars):")
            print((body or "")[:1500])
            print("…")
        else:
            print("❌ no post found")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
