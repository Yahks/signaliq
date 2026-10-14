"""trader_process — one document per org, one draft per model run.

S.8.97 — the entry-per-row model broke when drafts arrived during
the trader's edits. The process is one document with nine
sections. The trader saves the whole thing. The model drafts
the whole thing. They live in two tables.

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-03
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SECTIONS = (
    "daily_scan", "entry_logic", "position_sizing", "exit_logic",
    "holding_period", "research_to_rules", "chart_overlay",
    "no_backtest_rule", "multi_strategy_blend",
)


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_trader_process ON trader_process")
    op.drop_table("trader_process")

    op.create_table(
        "trader_process",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        *[sa.Column(s, sa.Text, nullable=False, server_default="") for s in SECTIONS],
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("org_id", name="uq_trader_process_org"),
    )
    op.execute("ALTER TABLE trader_process ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trader_process FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_trader_process ON trader_process
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON trader_process TO signaliq_app")

    op.create_table(
        "trader_process_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        *[sa.Column(s, sa.Text, nullable=False, server_default="") for s in SECTIONS],
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_trader_process_drafts_org_created",
        "trader_process_drafts", ["org_id", sa.text("created_at DESC")],
    )
    op.execute("ALTER TABLE trader_process_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trader_process_drafts FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_trader_process_drafts ON trader_process_drafts
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON trader_process_drafts TO signaliq_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_trader_process_drafts ON trader_process_drafts")
    op.drop_index("ix_trader_process_drafts_org_created", table_name="trader_process_drafts")
    op.drop_table("trader_process_drafts")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_trader_process ON trader_process")
    op.drop_table("trader_process")
