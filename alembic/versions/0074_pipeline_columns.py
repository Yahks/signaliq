"""Pipeline columns: trader_process.account_name + anything the
synthetic-trade pipeline needs that isn't already in a migration.

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-12
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The account name shown on /trading/performance and in every
    # article's performance sentence.
    op.execute("""
        ALTER TABLE trader_process
        ADD COLUMN IF NOT EXISTS account_name TEXT
    """)

    # Batch tracking on trade_log — added in the synthetic-trade
    # work. Present in dev by hand, needs to be a migration.
    op.execute("""
        ALTER TABLE trade_log
        ADD COLUMN IF NOT EXISTS batch_id UUID
    """)
    op.execute("""
        ALTER TABLE trade_log
        ADD COLUMN IF NOT EXISTS used_in_article_id UUID
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_trade_log_batch
        ON trade_log (org_id, batch_id)
        WHERE deleted_at IS NULL
    """)

    # Backfill existing rows — the column was added but never
    # populated. The article pipeline falls back to "my demo
    # account" at read time, but storing it makes the performance
    # page show the correct name immediately.
    op.execute("""
        UPDATE trader_process
        SET account_name = 'my demo account'
        WHERE account_name IS NULL AND deleted_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_trade_log_batch")
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS used_in_article_id")
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS batch_id")
    op.execute("ALTER TABLE trader_process DROP COLUMN IF EXISTS account_name")
