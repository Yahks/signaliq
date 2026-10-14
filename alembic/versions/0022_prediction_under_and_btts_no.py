"""Add prob_under_1_5, prob_under_3_5, prob_btts_no to predictions.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("predictions", sa.Column("prob_under_1_5", sa.Float, nullable=True))
    op.add_column("predictions", sa.Column("prob_under_3_5", sa.Float, nullable=True))
    op.add_column("predictions", sa.Column("prob_btts_no", sa.Float, nullable=True))


def downgrade() -> None:
    for col in ["prob_btts_no", "prob_under_3_5", "prob_under_1_5"]:
        op.drop_column("predictions", col)
