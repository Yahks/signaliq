"""market_history, stock_metadata, market_series_config.

S.9.43 - three tables for the historical market data pipeline.

  - market_history: per-org daily closes fetched from Yahoo.
  - stock_metadata: reference data (sector, industry, market cap)
    for symbols appearing in market_events.top_movers.
  - market_series_config: the trader's list of series to fetch.
    Seeded with defaults for every existing org so the pipeline
    works before the UI exists.

Revision ID: 0078
Revises: 0077
Create Date: 2026-10-14
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── market_history ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS market_history (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id      uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
            symbol      text NOT NULL,
            category    text NOT NULL,
            trade_date  date NOT NULL,
            close       numeric NOT NULL,
            source      text NOT NULL DEFAULT 'yahoo',
            created_at  timestamptz NOT NULL DEFAULT now(),
            deleted_at  timestamptz
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_market_history_org_sym_date
        ON market_history (org_id, symbol, trade_date)
        WHERE deleted_at IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_market_history_lookup
        ON market_history (org_id, symbol, trade_date DESC)
        WHERE deleted_at IS NULL
    """)
    op.execute("ALTER TABLE market_history ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE market_history FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_market_history ON market_history")
    op.execute("""
        CREATE POLICY tenant_isolation_market_history ON market_history
        USING (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        )
    """)

    # ── stock_metadata (reference data, not org-scoped) ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS stock_metadata (
            symbol          text PRIMARY KEY,
            name            text,
            sector          text,
            industry        text,
            market_cap      numeric,
            market_cap_tier text,
            source          text NOT NULL DEFAULT 'yahoo',
            updated_at      timestamptz NOT NULL DEFAULT now()
        )
    """)

    # ── market_series_config ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS market_series_config (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id      uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
            symbol      text NOT NULL,
            display     text NOT NULL,
            category    text NOT NULL,
            enabled     boolean NOT NULL DEFAULT true,
            created_at  timestamptz NOT NULL DEFAULT now(),
            deleted_at  timestamptz
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_market_series_config_org_sym
        ON market_series_config (org_id, symbol)
        WHERE deleted_at IS NULL
    """)
    op.execute("ALTER TABLE market_series_config ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE market_series_config FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_market_series_config ON market_series_config")
    op.execute("""
        CREATE POLICY tenant_isolation_market_series_config ON market_series_config
        USING (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        )
    """)

    # ── Seed defaults for every existing org ──
    op.execute("""
        INSERT INTO market_series_config (org_id, symbol, display, category)
        SELECT o.id, s.symbol, s.display, s.category
        FROM orgs o
        CROSS JOIN (VALUES
            ('^VIX', 'VIX',          'vix'),
            ('SPY',  'S&P 500 ETF',  'index'),
            ('MDY',  'Mid-cap ETF',  'index'),
            ('IWM',  'Small-cap ETF','index'),
            ('TLT',  'Long bonds',   'asset_class'),
            ('GLD',  'Gold',         'asset_class'),
            ('HYG',  'High-yield',   'asset_class'),
            ('UUP',  'Dollar index', 'asset_class')
        ) AS s(symbol, display, category)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS market_series_config CASCADE")
    op.execute("DROP TABLE IF EXISTS stock_metadata CASCADE")
    op.execute("DROP TABLE IF EXISTS market_history CASCADE")
