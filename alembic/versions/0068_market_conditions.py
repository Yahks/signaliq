"""market_conditions — trader-filled current state for market articles.

The market-analysis registers (market_analysis, market_condition,
market_signals) need current state vs. baseline. This table stores
the trader's written sections describing what they track, their
baselines, their regime definitions, and their process
adjustments. Computed current values are stored alongside for
trigger context only — they are never cited in an article body.

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-05
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_conditions",
        sa.Column("id", sa.Uuid(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        # Trader-filled sections (prose)
        sa.Column("what_i_track", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
        sa.Column("baselines", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
        sa.Column("regimes", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
        sa.Column("thresholds", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
        sa.Column("adjustments", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
        sa.Column("mechanisms", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
        # Computed (optional, trigger only)
        sa.Column("realized_vol_current", sa.Numeric(), nullable=True),
        sa.Column("spread_current", sa.Numeric(), nullable=True),
        sa.Column("range_current", sa.Numeric(), nullable=True),
        sa.Column("correlation_current", sa.Numeric(), nullable=True),
        sa.Column("breadth_current", sa.Numeric(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"],
                                ondelete="CASCADE"),
    )
    op.create_index(
        "ix_market_conditions_org_created",
        "market_conditions",
        ["org_id", sa.text("created_at DESC")],
    )
    op.execute("ALTER TABLE market_conditions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE market_conditions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_market_conditions
        ON market_conditions
        USING (
            org_id = (current_setting('app.current_org_id'::text, true))::uuid
        )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_market_conditions ON market_conditions")
    op.drop_index("ix_market_conditions_org_created",
                  table_name="market_conditions")
    op.drop_table("market_conditions")
