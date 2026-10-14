"""Add live_quote JSONB to chain_watchlist.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chain_watchlist",
        sa.Column("live_quote", postgresql.JSONB,
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("chain_watchlist", "live_quote")
