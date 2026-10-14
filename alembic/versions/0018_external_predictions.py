"""External prediction sources, form snapshots, H2H snapshots.

Schema:
  - external_prediction_sources: registry of where predictions come from
  - external_predictions: one row per (source, match) prediction
  - source_reliability: rolling accuracy per source (updated daily)
  - form_snapshots: cached last-5 form per (team, as_of_date)
  - h2h_snapshots: cached last-5 H2H per (home_team, away_team, as_of_date)

Design:
  - External sources are GLOBAL (not tenant-scoped). Predictions are about
    public football matches — same principle as `matches` and `predictions`.
  - Reliability is tracked per source so we can weight sources by their
    actual historical accuracy. Unreliable sources fade out.
  - Form and H2H snapshots are cached to avoid recomputing for every prediction.
    Refreshed daily by a scheduled task.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- external_prediction_sources ----
    op.create_table(
        "external_prediction_sources",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        # kind: bookmaker | prediction_site | internal_model | rss | manual
        sa.Column("base_url", sa.String(512), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    # ---- external_predictions ----
    op.create_table(
        "external_predictions",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("external_prediction_sources.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("match_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prob_home_win", sa.Float, nullable=False),
        sa.Column("prob_draw", sa.Float, nullable=False),
        sa.Column("prob_away_win", sa.Float, nullable=False),
        sa.Column("prob_over_2_5", sa.Float, nullable=True),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source_id", "match_id", name="uq_external_prediction"),
    )
    op.create_index("ix_external_pred_match", "external_predictions", ["match_id"])

    # ---- source_reliability ----
    # Rolling accuracy per source, updated by a scheduled task.
    op.create_table(
        "source_reliability",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("external_prediction_sources.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("window_days", sa.Integer, nullable=False),
        sa.Column("predictions_scored", sa.Integer, nullable=False),
        sa.Column("result_accuracy", sa.Float, nullable=False),
        sa.Column("avg_brier_score", sa.Float, nullable=False),
        sa.Column("reliability_weight", sa.Float, nullable=False),
        # reliability_weight: 0-1, used for ensemble blending
        sa.Column("computed_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source_id", "window_days", name="uq_reliability_source_window"),
    )

    # ---- form_snapshots ----
    op.create_table(
        "form_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("team", sa.String(128), nullable=False),
        sa.Column("competition", sa.String(64), nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("matches_considered", sa.Integer, nullable=False),
        sa.Column("goals_for_avg", sa.Float, nullable=False),
        sa.Column("goals_against_avg", sa.Float, nullable=False),
        sa.Column("points_per_game", sa.Float, nullable=False),
        sa.Column("attack_residual", sa.Float, nullable=False),
        sa.Column("defense_residual", sa.Float, nullable=False),
        sa.Column("raw_recent_matches", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("computed_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("team", "competition", "as_of_date", name="uq_form_snapshot"),
    )
    op.create_index("ix_form_team_comp_date", "form_snapshots",
                    ["team", "competition", "as_of_date"])

    # ---- h2h_snapshots ----
    op.create_table(
        "h2h_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("home_team", sa.String(128), nullable=False),
        sa.Column("away_team", sa.String(128), nullable=False),
        sa.Column("competition", sa.String(64), nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("matches_considered", sa.Integer, nullable=False),
        sa.Column("home_wins", sa.Integer, nullable=False),
        sa.Column("draws", sa.Integer, nullable=False),
        sa.Column("away_wins", sa.Integer, nullable=False),
        sa.Column("home_goals_avg", sa.Float, nullable=False),
        sa.Column("away_goals_avg", sa.Float, nullable=False),
        sa.Column("h2h_multiplier", sa.Float, nullable=False),
        # h2h_multiplier: multiplicative adjustment to the base model's home/away λ
        sa.Column("raw_recent_h2h", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("computed_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("home_team", "away_team", "competition", "as_of_date",
                            name="uq_h2h_snapshot"),
    )
    op.create_index("ix_h2h_pair_comp_date", "h2h_snapshots",
                    ["home_team", "away_team", "competition", "as_of_date"])

    # ---- Seed the initial source registry ----
    op.execute("""
        INSERT INTO external_prediction_sources (slug, name, kind, base_url, enabled)
        VALUES
            ('dixon_coles_v1', 'Dixon-Coles v1 (internal)', 'internal_model', NULL, true),
            ('dixon_coles_v2', 'Dixon-Coles v2 (form + H2H, internal)', 'internal_model', NULL, true),
            ('elo_baseline', 'Elo baseline (internal)', 'internal_model', NULL, true),
            ('the_odds_api', 'The Odds API (bookmaker consensus)', 'bookmaker', 'https://the-odds-api.com', true)
        ON CONFLICT (slug) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("h2h_snapshots")
    op.drop_table("form_snapshots")
    op.drop_table("source_reliability")
    op.drop_table("external_predictions")
    op.drop_table("external_prediction_sources")
