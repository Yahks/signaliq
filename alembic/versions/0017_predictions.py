"""Football predictions and result tracking.

Schema:
  - predictions: every model output, stored with provenance
  - prediction_results: outcome after the match is played
  - model_performance: rolling accuracy aggregates (materialized)

The key design principle: a prediction is IMMUTABLE once published. If the
model changes, a new prediction row is created (with a new model_version).
This makes the accuracy tracking honest — you can never edit history.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- matches (fixtures + results in one table) ----
    op.create_table(
        "matches",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("external_id", sa.String(128), nullable=False),  # football-data.org id
        sa.Column("competition", sa.String(64), nullable=False),    # "EPL", "La Liga"
        sa.Column("season", sa.String(16), nullable=False),         # "2026-27"
        sa.Column("home_team", sa.String(128), nullable=False),
        sa.Column("away_team", sa.String(128), nullable=False),
        sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'scheduled'")),
        # status: scheduled | live | finished | postponed | cancelled
        sa.Column("home_goals", sa.Integer, nullable=True),
        sa.Column("away_goals", sa.Integer, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("external_id", name="uq_match_external_id"),
    )
    op.create_index("ix_matches_kickoff", "matches", ["kickoff_at"])
    op.create_index("ix_matches_competition_season", "matches", ["competition", "season"])
    op.create_index("ix_matches_status", "matches", ["status"])

    # ---- predictions (immutable once created) ----
    op.create_table(
        "predictions",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("match_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_version", sa.String(32), nullable=False),  # e.g. "dixon_coles_v1"
        sa.Column("model_config_hash", sa.String(64), nullable=True),
        # --- Probabilities (Win/Draw/Loss from home team perspective) ---
        sa.Column("prob_home_win", sa.Float, nullable=False),
        sa.Column("prob_draw", sa.Float, nullable=False),
        sa.Column("prob_away_win", sa.Float, nullable=False),
        # --- Expected goals ---
        sa.Column("expected_home_goals", sa.Float, nullable=False),
        sa.Column("expected_away_goals", sa.Float, nullable=False),
        # --- Over/Under 2.5 ---
        sa.Column("prob_over_2_5", sa.Float, nullable=False),
        sa.Column("prob_under_2_5", sa.Float, nullable=False),
        # --- Confidence metadata ---
        sa.Column("confidence", sa.String(16), nullable=False),  # low | medium | high
        sa.Column("data_quality", sa.Float, nullable=True),       # 0-1, based on sample size
        # --- Fair odds (derived from probabilities) ---
        sa.Column("fair_odds_home", sa.Float, nullable=True),
        sa.Column("fair_odds_draw", sa.Float, nullable=True),
        sa.Column("fair_odds_away", sa.Float, nullable=True),
        # --- Provenance ---
        sa.Column("inputs_snapshot", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_published", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("match_id", "model_version", name="uq_prediction_match_model"),
    )
    op.create_index("ix_predictions_match", "predictions", ["match_id"])
    op.create_index("ix_predictions_published", "predictions",
                    ["is_published", "published_at"])

    # ---- prediction_results (one per prediction, created when match finishes) ----
    op.create_table(
        "prediction_results",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("prediction_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("predictions.id", ondelete="CASCADE"), nullable=False, unique=True),
        # --- Actual outcome ---
        sa.Column("actual_result", sa.String(8), nullable=False),  # home_win | draw | away_win
        sa.Column("actual_home_goals", sa.Integer, nullable=False),
        sa.Column("actual_away_goals", sa.Integer, nullable=False),
        sa.Column("actual_total_goals", sa.Integer, nullable=False),
        sa.Column("actual_over_2_5", sa.Boolean, nullable=False),
        # --- Scoring ---
        sa.Column("result_correct", sa.Boolean, nullable=False),     # Win/Draw/Loss predicted correctly
        sa.Column("over_under_correct", sa.Boolean, nullable=False),
        sa.Column("brier_score", sa.Float, nullable=False),          # multiclass Brier, lower is better
        sa.Column("log_loss", sa.Float, nullable=False),
        # --- Context ---
        sa.Column("scored_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_pred_results_prediction", "prediction_results", ["prediction_id"])

    # ---- model_performance (rolling aggregates, written by scheduled task) ----
    op.create_table(
        "model_performance",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("model_version", sa.String(32), nullable=False),
        sa.Column("competition", sa.String(64), nullable=True),       # NULL = all competitions
        sa.Column("window_days", sa.Integer, nullable=False),         # 7, 30, 90, 365, 0=all-time
        sa.Column("predictions_made", sa.Integer, nullable=False),
        sa.Column("predictions_scored", sa.Integer, nullable=False),
        sa.Column("result_accuracy", sa.Float, nullable=False),       # 0-1
        sa.Column("over_under_accuracy", sa.Float, nullable=False),
        sa.Column("avg_brier_score", sa.Float, nullable=False),
        sa.Column("avg_log_loss", sa.Float, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_perf_model_window", "model_performance",
                    ["model_version", "competition", "window_days"])

    # ---- RLS note ----
    # These tables are NOT tenant-scoped. Predictions are global model output
    # shared across all orgs. Multi-tenancy does not apply here.
    # (This is documented in docs/product-position.md — predictions are facts
    # about a public football match, not private user data.)


def downgrade() -> None:
    op.drop_table("model_performance")
    op.drop_table("prediction_results")
    op.drop_table("predictions")
    op.drop_table("matches")
