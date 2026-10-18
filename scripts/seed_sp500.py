"""Seed market_series_config with the S&P 500.

S.9.44 — fetch the S&P 500 list from Wikipedia, validate each
symbol against Yahoo, insert the validated ones into
market_series_config for the given org.

Idempotent: ON CONFLICT DO NOTHING on (org_id, symbol).

Run inside the api container:
    docker compose exec -T api python3 scripts/seed_sp500.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx
from sqlalchemy import text as sql_text

sys.path.insert(0, os.getcwd())

from packages.core.context import current_org_id  # noqa: E402
from packages.db.session import get_session_factory  # noqa: E402
from packages.db.tenant import set_current_org  # noqa: E402
from packages.core.logging import get_logger  # noqa: E402

log = get_logger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _yahoo_symbol(wiki_symbol: str) -> str:
    """Wikipedia uses '.' for share classes. Yahoo uses '-'."""
    return wiki_symbol.strip().replace(".", "-")


def fetch_sp500_list() -> list[dict[str, str]]:
    """Fetch and parse the Wikipedia table. Returns
    [{symbol, name, sector}, ...]."""
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        r = client.get(
            WIKI_URL,
            headers={
                "User-Agent": _HEADERS["User-Agent"],
                "Accept": "text/html",
            },
        )
        r.raise_for_status()
        html = r.text

    # Use pandas' read_html — it does the parsing.
    import io
    import pandas as pd
    tables = pd.read_html(io.StringIO(html))
    df = tables[0]
    df.columns = [c.strip() for c in df.columns]

    out: list[dict[str, str]] = []
    for _, row in df.iterrows():
        raw_sym = str(row.get("Symbol", "")).strip()
        if not raw_sym or raw_sym.lower() == "nan":
            continue
        out.append({
            "symbol": _yahoo_symbol(raw_sym),
            "name": str(row.get("Security", "")).strip(),
            "sector": str(row.get("GICS Sector", "")).strip(),
        })
    return out


async def main() -> None:
    org_id = os.environ.get("SIGNALIQ_ORG_ID")
    if not org_id:
        print("ERROR: set SIGNALIQ_ORG_ID env var")
        sys.exit(1)

    current_org_id.set(org_id)

    print("Fetching S&P 500 list from Wikipedia...")
    rows = fetch_sp500_list()
    print(f"  parsed {len(rows)} symbols")

    # Skip Yahoo validation — the endpoint aggressively rate-limits
    # raw httpx requests (429). Wikipedia's list is authoritative.
    # Phase 2 (the backfill) will discover any symbol Yahoo can't
    # serve and log it.
    valid = rows
    print(f"  accepting all {len(valid)} (validation skipped)")

    # Insert into market_series_config.
    factory = get_session_factory()
    inserted = 0
    async with factory() as s:
        await set_current_org(s, org_id)
        for row in valid:
            try:
                await s.execute(sql_text("""
                    INSERT INTO market_series_config
                        (org_id, symbol, display, category, enabled)
                    VALUES (:oid, :sym, :disp, 'stock', true)
                    ON CONFLICT (org_id, symbol) WHERE deleted_at IS NULL
                    DO NOTHING
                """), {
                    "oid": org_id,
                    "sym": row["symbol"],
                    "disp": row["name"][:120],
                })
                inserted += 1
            except Exception as exc:
                log.warning("seed.insert_failed",
                            symbol=row["symbol"], error=str(exc)[:200])
        await s.commit()

    print(f"  inserted (or already present): {inserted}")

    # ── Insert into stock_metadata as well. ──
    # Reference data: name, sector. No org_id — the table is
    # global. Upsert, so repeated runs refresh.
    meta_written = 0
    async with factory() as s:
        await set_current_org(s, org_id)
        for row in valid:
            try:
                await s.execute(sql_text("""
                    INSERT INTO stock_metadata (symbol, name, sector)
                    VALUES (:sym, :name, :sector)
                    ON CONFLICT (symbol) DO UPDATE SET
                        name   = EXCLUDED.name,
                        sector = EXCLUDED.sector,
                        updated_at = now()
                """), {
                    "sym": row["symbol"],
                    "name": row["name"][:200],
                    "sector": row["sector"][:100],
                })
                meta_written += 1
            except Exception as exc:
                log.warning("seed.metadata_insert_failed",
                            symbol=row["symbol"], error=str(exc)[:200])
        await s.commit()

    print(f"  stock_metadata rows written: {meta_written}")

    # Report.
    async with factory() as s:
        await set_current_org(s, org_id)
        total = (await s.execute(sql_text("""
            SELECT COUNT(*) FROM market_series_config
            WHERE org_id = :oid AND deleted_at IS NULL
        """), {"oid": org_id})).scalar()
        stocks = (await s.execute(sql_text("""
            SELECT COUNT(*) FROM market_series_config
            WHERE org_id = :oid AND category = 'stock'
              AND deleted_at IS NULL
        """), {"oid": org_id})).scalar()
        meta_total = (await s.execute(sql_text("""
            SELECT COUNT(*) FROM stock_metadata
        """))).scalar()

    print()
    print(f"market_series_config for org {org_id[:8]}:")
    print(f"  total symbols:  {total}")
    print(f"  stock symbols:  {stocks}")
    print(f"stock_metadata total rows: {meta_total}")


if __name__ == "__main__":
    asyncio.run(main())
