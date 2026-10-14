"""Add quantified data column to chain_scenarios.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chain_scenarios",
        sa.Column("candidate_symbols", postgresql.ARRAY(sa.Text),
                  nullable=False, server_default=sa.text("'{}'::text[]")),
    )
    op.add_column(
        "chain_scenarios",
        sa.Column("quantified", postgresql.JSONB,
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("chain_scenarios", "quantified")
    op.drop_column("chain_scenarios", "candidate_symbols")
