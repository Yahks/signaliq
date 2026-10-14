"""trader_process_history — archive of every saved process version.

Shape A: `trader_process` keeps one current row per org (the
UNIQUE(org_id) constraint stays). This new table appends a copy
of every Save, so the article generator can read the trader's
history, not just the current snapshot.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-04
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SECTIONS = (
    "daily_scan", "entry_logic", "position_sizing", "exit_logic",
    "holding_period", "research_to_rules", "chart_overlay",
    "no_backtest_rule", "multi_strategy_blend",
)


def upgrade() -> None:
    op.create_table(
        "trader_process_history",
        sa.Column(
            "id", sa.Uuid(), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        *[
            sa.Column(s, sa.Text(), nullable=False, server_default=sa.text("''"))
            for s in SECTIONS
        ],
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_trader_process_history_org_created",
        "trader_process_history",
        ["org_id", sa.text("created_at DESC")],
    )
    # RLS — same policy as the live table.
    op.execute("ALTER TABLE trader_process_history ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trader_process_history FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_trader_process_history
        ON trader_process_history
        USING (
            org_id = (current_setting('app.current_org_id'::text, true))::uuid
        )
    """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_trader_process_history "
        "ON trader_process_history"
    )
    op.drop_index(
        "ix_trader_process_history_org_created",
        table_name="trader_process_history",
    )
    op.drop_table("trader_process_history")
