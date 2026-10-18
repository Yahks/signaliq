"""market_history: add timeframe column.

S.9.44 - market_history now stores multiple timeframes per symbol.
The unique key becomes (org_id, symbol, timeframe, trade_date).

Existing rows (daily bars) get timeframe='1d'.

Revision ID: 0080
Revises: 0079
Create Date: 2026-10-15
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0080"
down_revision: str | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE market_history
            ADD COLUMN IF NOT EXISTS timeframe text NOT NULL DEFAULT '1d'
    """)

    # Drop the old unique index — it doesn't account for timeframe.
    op.execute("DROP INDEX IF EXISTS ux_market_history_org_sym_date")

    # New unique index includes timeframe.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_market_history_org_sym_tf_date
        ON market_history (org_id, symbol, timeframe, trade_date)
        WHERE deleted_at IS NULL
    """)

    # New lookup index includes timeframe.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_market_history_lookup_tf
        ON market_history (org_id, symbol, timeframe, trade_date DESC)
        WHERE deleted_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_market_history_lookup_tf")
    op.execute("DROP INDEX IF EXISTS ux_market_history_org_sym_tf_date")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_market_history_org_sym_date
        ON market_history (org_id, symbol, trade_date)
        WHERE deleted_at IS NULL
    """)
    op.execute("ALTER TABLE market_history DROP COLUMN IF EXISTS timeframe")
