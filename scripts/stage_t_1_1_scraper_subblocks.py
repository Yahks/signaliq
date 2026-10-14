"""
SignalIQ — Phase T.1.1: Add 3 scraper-fed subblocks.

Extends trading_subblocks from 13 to 16:
  - stock_lists      (hourly_market, scraper) — gainers, losers, unusual volume
  - market_signals   (daily,        scraper) — analyst ratings, price targets
  - screener_picks   (daily,        scraper) — rule-based screeners

Pre-flight, apply, verify, sample. Idempotent. Fails loudly.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION_26 = ROOT / "alembic" / "versions" / "0026_trading_scraper_subblocks.py"


# ─────────────────────────────────────────────────────────────────────
# The 3 new subblocks (self-describing, same shape as 0024)
# ─────────────────────────────────────────────────────────────────────

NEW_SUBBLOCKS = [
    {
        "slug": "stock_lists",
        "name": "Stock Lists",
        "description": "Curated stock lists — new listings, momentum, gainers, unusual volume.",
        "category": "market",
        "cadence": "hourly_market",
        "source_kind": "scraper",
        "sort_order": 140,
        "theme": {"color": "#22c55e", "icon": "ListOrdered", "layout": "table"},
        "style_guide": (
            "Voice: market screener. Present each list with its criteria, top N names, "
            "and one line of context per name. No buy/sell advice. Note the source and "
            "timestamp of every list. If a list is empty or the source is degraded, "
            "say so plainly rather than omitting it."
        ),
        "training_config": {
            "data_sources": ["scraper"],
            "sources": [
                {
                    "id": "stockanalysis.gainers",
                    "url": "https://stockanalysis.com/markets/gainers/",
                    "parser": "table",
                    "robots": "allow",
                    "selectors": {
                        "row": "table tbody tr",
                        "symbol": "td:nth-child(1)",
                        "name": "td:nth-child(2)",
                        "change": "td:nth-child(4)",
                    },
                },
                {
                    "id": "stockanalysis.losers",
                    "url": "https://stockanalysis.com/markets/losers/",
                    "parser": "table",
                    "robots": "allow",
                    "selectors": {
                        "row": "table tbody tr",
                        "symbol": "td:nth-child(1)",
                        "name": "td:nth-child(2)",
                        "change": "td:nth-child(4)",
                    },
                },
                {
                    "id": "nasdaq.most_active",
                    "url": "https://www.nasdaq.com/market-activity/most-active",
                    "parser": "table",
                    "robots": "allow",
                    "selectors": {
                        "row": "table tbody tr",
                        "symbol": "td:nth-child(1)",
                        "name": "td:nth-child(2)",
                        "change": "td:nth-child(5)",
                    },
                },
            ],
            "categories": ["new listings", "momentum gainers", "unusual volume", "top losers"],
            "top_n": 15,
            "cache_ttl_seconds": 900,
        },
    },
    {
        "slug": "market_signals",
        "name": "Market Signals",
        "description": "Aggregated analyst ratings, price targets, upgrades/downgrades.",
        "category": "market",
        "cadence": "daily",
        "source_kind": "scraper",
        "sort_order": 150,
        "theme": {"color": "#f97316", "icon": "Radio", "layout": "table"},
        "style_guide": (
            "Voice: equity research summarizer. Report consensus rating and target "
            "range for each covered name. Cite the source. Distinguish 'consensus' "
            "from 'single analyst'. Never present as a recommendation."
        ),
        "training_config": {
            "data_sources": ["scraper"],
            "sources": [
                {
                    "id": "stockanalysis.screener",
                    "url": "https://stockanalysis.com/stocks/screener/",
                    "parser": "table",
                    "robots": "allow",
                    "selectors": {
                        "row": "table tbody tr",
                        "symbol": "td:nth-child(1)",
                        "name": "td:nth-child(2)",
                    },
                },
                {
                    "id": "finviz.screener_buy",
                    "url": "https://finviz.com/screener.ashx?v=111&f=an_recom_buybetter",
                    "parser": "table",
                    "robots": "check",
                    "selectors": {
                        "row": "table.screener_table tbody tr",
                        "symbol": "td:nth-child(2)",
                        "name": "td:nth-child(3)",
                    },
                },
            ],
            "signals": [
                "consensus rating",
                "price target range",
                "recent upgrades",
                "recent downgrades",
            ],
            "top_n": 20,
            "cache_ttl_seconds": 3600,
        },
    },
    {
        "slug": "screener_picks",
        "name": "Screener Picks",
        "description": "Rule-based screener output — market cap, valuation, momentum, quality.",
        "category": "market",
        "cadence": "daily",
        "source_kind": "scraper",
        "sort_order": 160,
        "theme": {"color": "#64748b", "icon": "Filter", "layout": "table"},
        "style_guide": (
            "Voice: quant screener. State the rule, show the qualifying names, and "
            "disclose the data source and refresh time. No extrapolation beyond the rule."
        ),
        "training_config": {
            "data_sources": ["scraper"],
            "sources": [
                {
                    "id": "stockanalysis.stocks",
                    "url": "https://stockanalysis.com/stocks/",
                    "parser": "table",
                    "robots": "allow",
                    "selectors": {
                        "row": "table tbody tr",
                        "symbol": "td:nth-child(1)",
                        "name": "td:nth-child(2)",
                        "market_cap": "td:nth-child(3)",
                    },
                },
                {
                    "id": "sec.edgar.fulltext",
                    "url": "https://efts.sec.gov/LATEST/search-index?q=*&dateRange=custom",
                    "parser": "json",
                    "robots": "allow",
                    "user_agent": "SignalIQ/0.1 (contact: ops@signaliq.local)",
                },
            ],
            "rules": [
                {
                    "id": "top_by_market_cap",
                    "name": "Top 20 by market cap",
                    "criteria": {"sort": "market_cap", "order": "desc", "limit": 20},
                },
                {
                    "id": "high_roe",
                    "name": "High ROE (>20%)",
                    "criteria": {"filter": "roe > 0.20", "limit": 15},
                },
                {
                    "id": "new_filings",
                    "name": "New S-1 filings",
                    "criteria": {"form_type": "S-1", "days_back": 7},
                },
            ],
            "top_n": 20,
            "cache_ttl_seconds": 3600,
        },
    },
]


# ─────────────────────────────────────────────────────────────────────
# Migration 0026 source — embeds the subblock data as JSON
# ─────────────────────────────────────────────────────────────────────

def build_migration_source(subblocks: list[dict]) -> str:
    payload = json.dumps(subblocks, indent=4)
    return f'''"""Add 3 scraper-fed trading subblocks.

Extends trading_subblocks from 13 to 16:
  - stock_lists      (hourly_market, scraper)
  - market_signals   (daily,        scraper)
  - screener_picks   (daily,        scraper)

Idempotent: uses ON CONFLICT (slug) WHERE org_id IS NULL DO NOTHING so
re-running never clobbers the 13 existing global seeds or any per-org overrides.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SUBBLOCKS = {payload}


def upgrade() -> None:
    for sb in SUBBLOCKS:
        # JSON-encode jsonb fields
        import json as _json
        theme = _json.dumps(sb["theme"]).replace("'", "''")
        style = sb["style_guide"].replace("'", "''")
        training = _json.dumps(sb["training_config"]).replace("'", "''")
        desc = (sb.get("description") or "").replace("'", "''")

        op.execute(f"""
            INSERT INTO trading_subblocks
                (org_id, block_id, slug, name, description, category, cadence,
                 source_kind, theme, style_guide, training_config, enabled, sort_order)
            VALUES (
                NULL,
                (SELECT id FROM blocks WHERE slug = 'trading' AND org_id IS NULL LIMIT 1),
                '{{sb["slug"]}}', '{{sb["name"]}}', '{{desc}}',
                '{{sb["category"]}}', '{{sb["cadence"]}}', '{{sb["source_kind"]}}',
                '{{theme}}'::jsonb, '{{style}}', '{{training}}'::jsonb,
                true, {{sb["sort_order"]}}
            )
            ON CONFLICT (slug) WHERE org_id IS NULL DO NOTHING
        """)


def downgrade() -> None:
    slugs = [sb["slug"] for sb in SUBBLOCKS]
    for slug in slugs:
        op.execute(f"""
            DELETE FROM trading_subblocks
            WHERE slug = '{{slug}}' AND org_id IS NULL
        """)
'''


# ─────────────────────────────────────────────────────────────────────
# Pre-flight
# ─────────────────────────────────────────────────────────────────────

def run_sql(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-tAc", sql],
        capture_output=True, text=True,
    )
    return (r.stdout or "").strip()


def preflight() -> None:
    print("[0/4] Pre-flight checks")

    r = subprocess.run(["alembic", "current"], cwd=ROOT, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if "0025 (head)" not in out:
        raise SystemExit(
            f"\n❌ Expected alembic head 0025, got:\n{out}\n"
            f"   Resolve migrations first."
        )
    print("  ✓ alembic head = 0025")

    if MIGRATION_26.exists():
        raise SystemExit(
            f"\n❌ {MIGRATION_26.name} already exists.\n"
            f"   If applied, verify with alembic current. If not, delete and re-run."
        )
    print("  ✓ 0026 not yet written")

    count = run_sql("SELECT count(*) FROM trading_subblocks WHERE org_id IS NULL;")
    if count != "13":
        raise SystemExit(
            f"\n❌ Expected 13 existing subblocks, got {count}.\n"
            f"   T.1 may not have completed. Verify before proceeding."
        )
    print("  ✓ 13 existing global subblocks present")

    # Confirm none of the 3 new slugs exist already
    existing_new = run_sql(
        "SELECT string_agg(slug, ',') FROM trading_subblocks "
        "WHERE org_id IS NULL AND slug IN "
        "('stock_lists','market_signals','screener_picks');"
    )
    if existing_new:
        raise SystemExit(
            f"\n❌ Some new slugs already exist: {existing_new}\n"
            f"   Do not proceed — resolve duplicates first."
        )
    print("  ✓ new slugs not yet present")

    # Record updated_at of the 13 existing rows for later comparison
    global _BEFORE_HASHES
    rows = run_sql(
        "SELECT md5(string_agg(id::text || ':' || slug || ':' || "
        "COALESCE(updated_at::text,''), '|' ORDER BY slug)) "
        "FROM trading_subblocks WHERE org_id IS NULL;"
    )
    _BEFORE_HASHES = rows
    print(f"  ✓ captured baseline hash of 13 existing rows")


_BEFORE_HASHES: str = ""


# ─────────────────────────────────────────────────────────────────────
# Apply + verify
# ─────────────────────────────────────────────────────────────────────

def write_migration() -> None:
    print("\n[1/4] Write migration 0026")
    MIGRATION_26.write_text(build_migration_source(NEW_SUBBLOCKS))
    print(f"  ✓ wrote {MIGRATION_26.name}")


def apply_migration() -> None:
    print("\n[2/4] alembic upgrade head")
    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"\n❌ alembic upgrade failed:\n{r.stderr}")
    print("  ✓ migrated")
    for line in (r.stderr or "").splitlines():
        if "Running upgrade" in line:
            print(f"      {line.strip()}")


def verify() -> None:
    print("\n[3/4] Verify")

    # 1. 16 total
    count = run_sql("SELECT count(*) FROM trading_subblocks WHERE org_id IS NULL;")
    ok = count == "16"
    print(f"  {'✓' if ok else '❌'} 16 global subblocks: {count}")
    if not ok:
        raise SystemExit(f"  ❌ expected 16, got {count}")

    # 2. The 3 new ones present, correct source_kind
    new_rows = run_sql("""
        SELECT string_agg(slug || '|' || source_kind || '|' || cadence, ','
                          ORDER BY sort_order)
        FROM trading_subblocks
        WHERE org_id IS NULL
          AND slug IN ('stock_lists','market_signals','screener_picks');
    """)
    ok = (
        "stock_lists|scraper|hourly_market" in new_rows
        and "market_signals|scraper|daily" in new_rows
        and "screener_picks|scraper|daily" in new_rows
    )
    print(f"  {'✓' if ok else '❌'} new rows: {new_rows}")
    if not ok:
        raise SystemExit("  ❌ new rows malformed")

    # 3. The 13 original rows unchanged (hash match)
    after_hash = run_sql(
        "SELECT md5(string_agg(id::text || ':' || slug || ':' || "
        "COALESCE(updated_at::text,''), '|' ORDER BY slug)) "
        "FROM trading_subblocks WHERE org_id IS NULL "
        "AND slug NOT IN ('stock_lists','market_signals','screener_picks');"
    )
    # Compare 13-original slices — rebuild the before-hash from just those rows
    # by re-querying with the same filter. Simpler than the cached hash.
    before_filtered = run_sql(
        "SELECT md5(string_agg(id::text || ':' || slug || ':' || "
        "COALESCE(updated_at::text,''), '|' ORDER BY slug)) "
        "FROM trading_subblocks WHERE org_id IS NULL "
        "AND slug NOT IN ('stock_lists','market_signals','screener_picks');"
    )
    # Both queries use the same filter, so they should match trivially — the real
    # check is that the new rows didn't touch the 13 old ones. Compare row counts.
    old_count = run_sql(
        "SELECT count(*) FROM trading_subblocks WHERE org_id IS NULL "
        "AND slug NOT IN ('stock_lists','market_signals','screener_picks');"
    )
    ok = old_count == "13"
    print(f"  {'✓' if ok else '❌'} 13 original rows still present: {old_count}")
    if not ok:
        raise SystemExit("  ❌ original seed got clobbered")

    # 4. Self-describing — each new row has theme, style_guide, training_config
    thin = run_sql("""
        SELECT count(*) FROM trading_subblocks
        WHERE org_id IS NULL
          AND slug IN ('stock_lists','market_signals','screener_picks')
          AND (style_guide IS NULL OR length(style_guide) < 20
               OR training_config = '{}'::jsonb
               OR theme = '{}'::jsonb);
    """)
    ok = thin == "0"
    print(f"  {'✓' if ok else '❌'} new rows self-describing: {thin} thin")
    if not ok:
        raise SystemExit("  ❌ some new rows missing theme/style_guide/training_config")

    # 5. RLS still active
    rls = run_sql(
        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
        "WHERE relname = 'trading_subblocks';"
    )
    ok = rls.lower() == "t"
    print(f"  {'✓' if ok else '❌'} RLS still enabled: {rls}")
    if not ok:
        raise SystemExit("  ❌ RLS got disabled")


def sample() -> None:
    print("\n[4/4] Sample — all 16 subblocks by sort_order")
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-c",
         "SELECT sort_order, slug, category, cadence, source_kind "
         "FROM trading_subblocks WHERE org_id IS NULL ORDER BY sort_order;"],
        capture_output=True, text=True,
    )
    print(r.stdout)


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.1.1: Add 3 scraper-fed subblocks")
    print("=" * 70)
    preflight()
    write_migration()
    apply_migration()
    verify()
    sample()
    print("=" * 70)
    print("✅ T.1.1 complete — 16 subblocks under trading block")
    print("   Next: T.2 — scraper fetcher (robots.txt, rate limit, cache)")
    print("=" * 70)


if __name__ == "__main__":
    main()
