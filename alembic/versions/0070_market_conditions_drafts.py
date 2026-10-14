"""market_conditions_drafts — model-drafted market observations.

Mirror of trader_process_drafts. The model drafts the trader's
market-observations block (what they track, baselines, regimes,
thresholds, adjustments, mechanisms). The trader reviews and
either accepts it (writes to market_conditions) or deletes it.

Revision ID: 0070
Revises: 0069
Create Date: 2026-10-05
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_conditions_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Uuid(), nullable=False),
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
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"],
                                ondelete="CASCADE"),
    )
    op.create_index(
        "ix_market_conditions_drafts_org_created",
        "market_conditions_drafts",
        ["org_id", sa.text("created_at DESC")],
    )
    op.execute(
        "ALTER TABLE market_conditions_drafts ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE market_conditions_drafts FORCE ROW LEVEL SECURITY"
    )
    op.execute("""
        CREATE POLICY tenant_isolation_market_conditions_drafts
        ON market_conditions_drafts
        USING (
            org_id = (current_setting('app.current_org_id'::text, true))::uuid
        )
    """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_market_conditions_drafts "
        "ON market_conditions_drafts"
    )
    op.drop_index("ix_market_conditions_drafts_org_created",
                  table_name="market_conditions_drafts")
    op.drop_table("market_conditions_drafts")
