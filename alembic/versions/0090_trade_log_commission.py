"""trade_log: commission_usd, backfilled at an estimated $2/trade.

S.9.77 — the cost model. result is gross of both slippage and
commission; slippage is already measured in entry_slippage and
exit_slippage (signed P&L contributions: net = result +
entry_slippage + exit_slippage). Commission had no column.

Existing rows are backfilled at $2/trade — an ESTIMATE, not a
measurement, stated as such everywhere it is used. New rows get 0
until the generator records a real commission.

Revision ID: 0090
Revises: 0089
Create Date: 2026-10-23
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0090"
down_revision: str | None = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ESTIMATED_COMMISSION_PER_TRADE = 2.00


def upgrade() -> None:
    op.execute("""
        ALTER TABLE trade_log
        ADD COLUMN IF NOT EXISTS commission_usd numeric
    """)
    op.execute(f"""
        UPDATE trade_log
        SET commission_usd = {ESTIMATED_COMMISSION_PER_TRADE}
        WHERE commission_usd IS NULL
    """)
    op.execute("""
        COMMENT ON COLUMN trade_log.commission_usd IS
        'Round-turn commission in USD. Backfilled at an ESTIMATED
         $2/trade for rows predating the cost model; not measured.'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS commission_usd")
