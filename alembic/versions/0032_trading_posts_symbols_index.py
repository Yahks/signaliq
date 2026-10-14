"""GIN index on trading_posts.symbols for cross-subblock lookups.

Enables fast `WHERE 'NVDA' = ANY(symbols)` queries when building the
per-symbol timeline. Without this, every ticker query is a sequential
scan of trading_posts.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_trading_posts_symbols
        ON trading_posts USING gin (symbols)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_trading_posts_symbols")
