"""Manually run the market_analysis generator once against the dev DB."""
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

    async with factory() as session:
        org = (await session.execute(text(
            "SELECT id::text, slug FROM orgs WHERE deleted_at IS NULL "
            "ORDER BY created_at LIMIT 1"
        ))).first()
    if not org:
        print("❌ no org found")
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
        print("Running market_analysis generator…\n")
        result = await generate_subblock(session, org_id=org_id, slug="market_analysis")
        print(f"Result: {result}\n")

    async with factory() as session:
        await session.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()
        row = (await session.execute(text("""
            SELECT title, body, word_count, symbols, status
            FROM trading_posts
            WHERE org_id = :o
            ORDER BY created_at DESC LIMIT 1
        """), {"o": org_id})).first()
        if row:
            title, body, wc, symbols, status = row
            print("=" * 70)
            print(f"TITLE: {title}")
            print(f"STATUS: {status}")
            print(f"WORDS: {wc}")
            print(f"SYMBOLS: {len(symbols or [])}  e.g. {(symbols or [])[:5]}")
            print("=" * 70)
            print("BODY (first 2000 chars):")
            print((body or "")[:2000])
            print("…")
        else:
            print("❌ no post found")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
