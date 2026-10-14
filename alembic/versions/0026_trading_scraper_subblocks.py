"""Add 3 scraper-fed trading subblocks.

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


SUBBLOCKS = [
    {
        "slug": "stock_lists",
        "name": "Stock Lists",
        "description": "Curated stock lists \u2014 new listings, momentum, gainers, unusual volume.",
        "category": "market",
        "cadence": "hourly_market",
        "source_kind": "scraper",
        "sort_order": 140,
        "theme": {
            "color": "#22c55e",
            "icon": "ListOrdered",
            "layout": "table"
        },
        "style_guide": "Voice: market screener. Present each list with its criteria, top N names, and one line of context per name. No buy/sell advice. Note the source and timestamp of every list. If a list is empty or the source is degraded, say so plainly rather than omitting it.",
        "training_config": {
            "data_sources": [
                "scraper"
            ],
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
                        "change": "td:nth-child(4)"
                    }
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
                        "change": "td:nth-child(4)"
                    }
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
                        "change": "td:nth-child(5)"
                    }
                }
            ],
            "categories": [
                "new listings",
                "momentum gainers",
                "unusual volume",
                "top losers"
            ],
            "top_n": 15,
            "cache_ttl_seconds": 900
        }
    },
    {
        "slug": "market_signals",
        "name": "Market Signals",
        "description": "Aggregated analyst ratings, price targets, upgrades/downgrades.",
        "category": "market",
        "cadence": "daily",
        "source_kind": "scraper",
        "sort_order": 150,
        "theme": {
            "color": "#f97316",
            "icon": "Radio",
            "layout": "table"
        },
        "style_guide": "Voice: equity research summarizer. Report consensus rating and target range for each covered name. Cite the source. Distinguish 'consensus' from 'single analyst'. Never present as a recommendation.",
        "training_config": {
            "data_sources": [
                "scraper"
            ],
            "sources": [
                {
                    "id": "stockanalysis.screener",
                    "url": "https://stockanalysis.com/stocks/screener/",
                    "parser": "table",
                    "robots": "allow",
                    "selectors": {
                        "row": "table tbody tr",
                        "symbol": "td:nth-child(1)",
                        "name": "td:nth-child(2)"
                    }
                },
                {
                    "id": "finviz.screener_buy",
                    "url": "https://finviz.com/screener.ashx?v=111&f=an_recom_buybetter",
                    "parser": "table",
                    "robots": "check",
                    "selectors": {
                        "row": "table.screener_table tbody tr",
                        "symbol": "td:nth-child(2)",
                        "name": "td:nth-child(3)"
                    }
                }
            ],
            "signals": [
                "consensus rating",
                "price target range",
                "recent upgrades",
                "recent downgrades"
            ],
            "top_n": 20,
            "cache_ttl_seconds": 3600
        }
    },
    {
        "slug": "screener_picks",
        "name": "Screener Picks",
        "description": "Rule-based screener output \u2014 market cap, valuation, momentum, quality.",
        "category": "market",
        "cadence": "daily",
        "source_kind": "scraper",
        "sort_order": 160,
        "theme": {
            "color": "#64748b",
            "icon": "Filter",
            "layout": "table"
        },
        "style_guide": "Voice: quant screener. State the rule, show the qualifying names, and disclose the data source and refresh time. No extrapolation beyond the rule.",
        "training_config": {
            "data_sources": [
                "scraper"
            ],
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
                        "market_cap": "td:nth-child(3)"
                    }
                },
                {
                    "id": "sec.edgar.fulltext",
                    "url": "https://efts.sec.gov/LATEST/search-index?q=*&dateRange=custom",
                    "parser": "json",
                    "robots": "allow",
                    "user_agent": "SignalIQ/0.1 (contact: ops@signaliq.local)"
                }
            ],
            "rules": [
                {
                    "id": "top_by_market_cap",
                    "name": "Top 20 by market cap",
                    "criteria": {
                        "sort": "market_cap",
                        "order": "desc",
                        "limit": 20
                    }
                },
                {
                    "id": "high_roe",
                    "name": "High ROE (>20%)",
                    "criteria": {
                        "filter": "roe > 0.20",
                        "limit": 15
                    }
                },
                {
                    "id": "new_filings",
                    "name": "New S-1 filings",
                    "criteria": {
                        "form_type": "S-1",
                        "days_back": 7
                    }
                }
            ],
            "top_n": 20,
            "cache_ttl_seconds": 3600
        }
    }
]


def upgrade() -> None:
    # Ensure the system 'trading' block exists. Migration 0009
    # creates the blocks table; no migration has ever inserted a
    # 'trading' row (it was created by hand on the dev DB). This
    # migration is the first to reference it, so we seed it here.
    op.execute("""
        INSERT INTO blocks (org_id, slug, name, description,
                            is_system, is_active)
        SELECT NULL, 'trading', 'Trading',
               'Trading block: psychology, risk, styles, and other '
               'first-class content verticals for traders.',
               true, true
        WHERE NOT EXISTS (
            SELECT 1 FROM blocks
            WHERE slug = 'trading' AND org_id IS NULL
        )
    """)

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
                '{sb["slug"]}', '{sb["name"]}', '{desc}',
                '{sb["category"]}', '{sb["cadence"]}', '{sb["source_kind"]}',
                '{theme}'::jsonb, '{style}', '{training}'::jsonb,
                true, {sb["sort_order"]}
            )
            ON CONFLICT (slug) WHERE org_id IS NULL DO NOTHING
        """)


def downgrade() -> None:
    slugs = [sb["slug"] for sb in SUBBLOCKS]
    for slug in slugs:
        op.execute(f"""
            DELETE FROM trading_subblocks
            WHERE slug = '{slug}' AND org_id IS NULL
        """)
