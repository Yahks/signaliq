"""Add extended market columns to predictions.

Adds: prob_over_1_5, prob_over_3_5, BTTS, goals distributions, corners.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("predictions", sa.Column("prob_over_1_5", sa.Float, nullable=True))
    op.add_column("predictions", sa.Column("prob_over_3_5", sa.Float, nullable=True))
    op.add_column("predictions", sa.Column("prob_btts_yes", sa.Float, nullable=True))
    op.add_column("predictions",
                  sa.Column("home_goals_dist", postgresql.JSONB,
                            nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("predictions",
                  sa.Column("away_goals_dist", postgresql.JSONB,
                            nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("predictions",
                  sa.Column("top_scorelines", postgresql.JSONB,
                            nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("predictions", sa.Column("expected_home_corners", sa.Float, nullable=True))
    op.add_column("predictions", sa.Column("expected_away_corners", sa.Float, nullable=True))
    op.add_column("predictions", sa.Column("expected_total_corners", sa.Float, nullable=True))

    # Grants — same as before
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON predictions TO signaliq_app")


def downgrade() -> None:
    for col in [
        "expected_total_corners", "expected_away_corners", "expected_home_corners",
        "top_scorelines", "away_goals_dist", "home_goals_dist",
        "prob_btts_yes", "prob_over_3_5", "prob_over_1_5",
    ]:
        op.drop_column("predictions", col)
