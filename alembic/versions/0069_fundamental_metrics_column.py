"""trader_process — add fundamental_metrics column.

The `fundamental_metrics` subblock is a trader-written section
describing which fundamental checks they run before entering,
why they run them, and what they do with them. It is prose —
not a signal, not a value.

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-05
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for tbl in (
        "trader_process",
        "trader_process_drafts",
        "trader_process_history",
    ):
        op.add_column(
            tbl,
            sa.Column(
                "fundamental_metrics",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            ),
        )


def downgrade() -> None:
    for tbl in (
        "trader_process",
        "trader_process_drafts",
        "trader_process_history",
    ):
        op.drop_column(tbl, "fundamental_metrics")
