"""trader_process_history — soft delete column

The Saved process page reads from history. Moving a version to
trash needs a place to record the deletion. Add deleted_at,
matching trader_process.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-04
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trader_process_history",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Index for the "alive" query the list endpoint runs.
    op.create_index(
        "ix_trader_process_history_org_alive",
        "trader_process_history",
        ["org_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trader_process_history_org_alive",
        table_name="trader_process_history",
    )
    op.drop_column("trader_process_history", "deleted_at")
