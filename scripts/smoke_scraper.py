"""Smoke test: hit each configured scraper source once and print extracted rows.

Loads sources from trading_subblocks.training_config.sources[] for the
three scraper subblocks, fetches each once with force_refresh=True,
and prints what the CSS selectors actually extract.

Usage:
    python scripts/smoke_scraper.py             # all sources
    python scripts/smoke_scraper.py <source_id> # one source
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    from packages.core.config import get_settings
    from packages.trading.sources.scraper import fetch

    filter_id = sys.argv[1] if len(sys.argv) > 1 else None

    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    sources: list[dict] = []
    async with factory() as session:
        rows = (await session.execute(text("""
            SELECT slug, training_config->'sources'
            FROM trading_subblocks
            WHERE org_id IS NULL
              AND slug IN ('stock_lists','market_signals','screener_picks')
              AND source_kind = 'scraper'
            ORDER BY sort_order
        """))).all()
        for slug, sources_json in rows:
            for src in (sources_json or []):
                src = dict(src)
                src["_subblock"] = slug
                sources.append(src)

    await engine.dispose()

    if filter_id:
        sources = [s for s in sources if s.get("id") == filter_id]
        if not sources:
            print(f"❌ no source with id={filter_id}")
            sys.exit(1)

    print(f"Running smoke test on {len(sources)} sources\n")

    for src in sources:
        sid = src.get("id")
        url = src.get("url")
        print("=" * 70)
        print(f"SOURCE: {sid}")
        print(f"SUBBLOCK: {src.get('_subblock')}")
        print(f"URL: {url}")
        print(f"PARSER: {src.get('parser')}  ROBOTS: {src.get('robots')}")

        result = await fetch(src, force_refresh=True)
        print(f"OK: {result.ok}  STATUS: {result.status_code}  BYTES: {result.bytes_read}")
        if result.error:
            print(f"ERROR: {result.error}")
        print(f"ROWS: {len(result.rows)}")
        print("SAMPLE (first 3 rows):")
        for row in result.rows[:3]:
            print(f"  {json.dumps(row, default=str)}")
        if len(result.rows) > 3:
            print(f"  ... and {len(result.rows) - 3} more")
        print()

    print("=" * 70)
    print("Done. If a source returned OK=True with 0 rows, or ERROR=parse_failed,")
    print("the CSS selectors in trading_subblocks.training_config need updating.")
    print()
    print("To fix a source's selectors, run:")
    print("  UPDATE trading_subblocks")
    print("  SET training_config = jsonb_set(training_config, '{sources}', '<new_json>')")
    print("  WHERE slug = '<subblock>' AND org_id IS NULL;")


if __name__ == "__main__":
    asyncio.run(main())
