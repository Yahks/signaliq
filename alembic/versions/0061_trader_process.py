"""Trader process — the trader's actual operation, per org.

S.8.95 — the concept-explainer register needs the trader's real
process. Each entry is a section + content, timestamped. The
trader writes as often as they want. The subblocks read the
latest entries per section.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trader_process",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section", sa.String(64), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "section IN ('daily_scan','entry_logic','position_sizing',"
            "'exit_logic','holding_period','research_to_rules',"
            "'chart_overlay','no_backtest_rule','multi_strategy_blend')",
            name="trader_process_section_check",
        ),
    )
    op.create_index(
        "ix_trader_process_org_section_created",
        "trader_process",
        ["org_id", "section", sa.text("created_at DESC")],
    )
    op.execute("ALTER TABLE trader_process ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trader_process FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_trader_process ON trader_process
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
    """)
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON trader_process TO signaliq_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_trader_process ON trader_process"
    )
    op.drop_index("ix_trader_process_org_section_created", table_name="trader_process")
    op.drop_table("trader_process")
