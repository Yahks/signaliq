"""trader_process — add source column (user | draft).

S.8.96 — auto-draft. The periodic task drafts sections the
trader has not written. Every entry carries a source so the
register can prefer the trader's own writing.

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-03
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trader_process",
        sa.Column(
            "source",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'user'"),
        ),
    )
    op.create_check_constraint(
        "trader_process_source_check",
        "trader_process",
        "source IN ('user','draft')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "trader_process_source_check", "trader_process",
    )
    op.drop_column("trader_process", "source")
