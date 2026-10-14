"""Match corners for corners-based analysis.

Separate table from `matches` because:
  - Corners data comes from a DIFFERENT source (Football-Data.co.uk CSV)
  - Not all matches have corners data
  - The relationship is 1:1 but the source provenance is different

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "match_corners",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("external_id", sa.String(128), nullable=True),  # football-data.co.uk has no id, we build one
        sa.Column("competition", sa.String(64), nullable=False),
        sa.Column("season", sa.String(16), nullable=False),
        sa.Column("home_team", sa.String(128), nullable=False),
        sa.Column("away_team", sa.String(128), nullable=False),
        sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_goals", sa.Integer, nullable=False),
        sa.Column("away_goals", sa.Integer, nullable=False),
        sa.Column("home_corners", sa.Integer, nullable=True),
        sa.Column("away_corners", sa.Integer, nullable=True),
        sa.Column("home_yellow", sa.Integer, nullable=True),
        sa.Column("away_yellow", sa.Integer, nullable=True),
        sa.Column("home_red", sa.Integer, nullable=True),
        sa.Column("away_red", sa.Integer, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("competition", "kickoff_at", "home_team", "away_team",
                            name="uq_corner_match"),
    )
    op.create_index("ix_corner_team_time", "match_corners",
                    ["competition", "kickoff_at"])
    op.create_index("ix_corner_home_team", "match_corners", ["home_team"])
    op.create_index("ix_corner_away_team", "match_corners", ["away_team"])


def downgrade() -> None:
    op.drop_table("match_corners")
