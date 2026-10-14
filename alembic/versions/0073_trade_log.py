"""trade_log — the trader's actual trades.

Every article needs one real trade to open on. The process
document describes rules; the trade_history_summary describes
aggregate outcomes; this table has the specific trades.

Populated three ways:
  - manual entry via /trading/trade-log
  - YAML import from a broker export
  - hourly synthetic trades derived from the process document

Revision ID: 0073
Revises: 0072
Create Date: 2026-10-07
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_log",
        sa.Column("id", sa.Uuid(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("traded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("instrument", sa.String(24), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("setup", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("entry", sa.Numeric(), nullable=True),
        sa.Column("contracts", sa.Integer(), nullable=True),
        sa.Column("stop", sa.Numeric(), nullable=True),
        sa.Column("atr_at_entry", sa.Numeric(), nullable=True),
        sa.Column("target", sa.Numeric(), nullable=True),
        sa.Column("exit", sa.Numeric(), nullable=True),
        sa.Column("exit_reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("result", sa.Numeric(), nullable=True),
        sa.Column("percent_of_equity", sa.Numeric(), nullable=True),
        sa.Column("hold_time", sa.String(32), nullable=True),
        sa.Column("lesson", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("source", sa.String(16), nullable=False,
                  server_default=sa.text("'manual'")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_trade_log_org_traded_at",
        "trade_log",
        ["org_id", sa.text("traded_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.execute("ALTER TABLE trade_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trade_log FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_trade_log
        ON trade_log
        USING (
            org_id = (current_setting('app.current_org_id'::text, true))::uuid
        )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_trade_log ON trade_log")
    op.drop_index("ix_trade_log_org_traded_at", table_name="trade_log")
    op.drop_table("trade_log")
