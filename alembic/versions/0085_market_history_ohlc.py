"""market_history: add open, high, low per bar.

S.9.47 — Path B. The trade-spine template asks for the moment
and the hesitation, with specific intraday highs and lows. The
market_history table stores only close, so the model invents
the intraday action. This migration adds the three OHLC columns
the fetcher already has in hand (yfinance returns O/H/L/C) but
currently discards.

All nullable. Existing rows stay NULL until the refetch. New
rows are filled by upsert_market_history when yfinance returns
the columns.

Revision ID: 0085
Revises: 0084
Create Date: 2026-10-18
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE market_history "
        "ADD COLUMN IF NOT EXISTS open numeric(18, 6)"
    )
    op.execute(
        "ALTER TABLE market_history "
        "ADD COLUMN IF NOT EXISTS high numeric(18, 6)"
    )
    op.execute(
        "ALTER TABLE market_history "
        "ADD COLUMN IF NOT EXISTS low numeric(18, 6)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE market_history DROP COLUMN IF EXISTS low")
    op.execute("ALTER TABLE market_history DROP COLUMN IF EXISTS high")
    op.execute("ALTER TABLE market_history DROP COLUMN IF EXISTS open")
