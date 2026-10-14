"""trader_process: soft delete

S.8.97 follow-up — the Saved process page needs Delete (move to
trash). This adds a nullable deleted_at column, and indexes the
org_id only for rows where deleted_at IS NULL, since those are
the only rows the API, the register, and the excerpt reader
should ever see.

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trader_process",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_trader_process_org_alive",
        "trader_process",
        ["org_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_trader_process_org_alive", table_name="trader_process")
    op.drop_column("trader_process", "deleted_at")
