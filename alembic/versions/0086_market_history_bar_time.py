"""market_history: add bar_time for intraday bars.

S.9.48 — Path B, part 2. The fetcher was storing one row per
date per (symbol, timeframe). For 1h bars that means six hourly
bars collapse into one close, and the intraday Open/High/Low are
lost. To store the intraday OHLC that yfinance already returns,
the schema needs a real timestamp per bar.

This migration:
  1. adds bar_time (timestamptz, nullable)
  2. backfills existing rows: bar_time = trade_date at midnight UTC
  3. drops the old unique index on (org_id, symbol, timeframe, trade_date)
  4. creates a new unique index on (org_id, symbol, timeframe, bar_time)
  5. keeps the existing lookup indexes (they still work on trade_date)

The trade_date column stays, derived from bar_time on write.
Queries filtering on trade_date keep working.

Revision ID: 0086
Revises: 0085
Create Date: 2026-10-18
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. New column.
    op.execute(
        "ALTER TABLE market_history "
        "ADD COLUMN IF NOT EXISTS bar_time timestamptz"
    )
    # 2. Backfill existing rows to their date at midnight UTC.
    op.execute(
        "UPDATE market_history "
        "SET bar_time = trade_date::timestamptz "
        "WHERE bar_time IS NULL"
    )
    # 3. Drop the old unique index.
    op.execute(
        "DROP INDEX IF EXISTS ux_market_history_org_sym_tf_date"
    )
    # 4. New unique index on bar_time.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "ux_market_history_org_sym_tf_bartime "
        "ON market_history (org_id, symbol, timeframe, bar_time) "
        "WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_market_history_org_sym_tf_bartime")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "ux_market_history_org_sym_tf_date "
        "ON market_history (org_id, symbol, timeframe, trade_date) "
        "WHERE deleted_at IS NULL"
    )
    op.execute("ALTER TABLE market_history DROP COLUMN IF EXISTS bar_time")
