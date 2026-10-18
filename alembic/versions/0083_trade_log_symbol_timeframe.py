"""trade_log: add symbol and timeframe columns.

S.9.45 - the trade log now stores trades per (symbol, timeframe)
so the trades in an article match the instrument and window
that the chart shows.

Existing rows (all NQ, all from one synthetic batch) get NULL
for both columns. They stay in the table but are excluded from
article generation, which now filters by symbol.

Revision ID: 0083
Revises: 0082
Create Date: 2026-10-16
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE trade_log
            ADD COLUMN IF NOT EXISTS symbol text
    """)
    op.execute("""
        ALTER TABLE trade_log
            ADD COLUMN IF NOT EXISTS timeframe text
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_trade_log_symbol_tf
        ON trade_log (org_id, symbol, timeframe, traded_at DESC)
        WHERE deleted_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_trade_log_symbol_tf")
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS timeframe")
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS symbol")
