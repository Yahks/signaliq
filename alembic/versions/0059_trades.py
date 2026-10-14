"""Trades: the trader's own fills, one row per entry.

S.8.88 — Change Set 2. The register's Decision beat says
"state the trade you did not take" because the payload has
no trade data. This table is the payload field that makes
the Decision beat a real trade report.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trades",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("exit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pnl", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("target_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("source", sa.String(16), nullable=False,
                  server_default=sa.text("'manual'")),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("side IN ('buy','sell')", name="trades_side_check"),
        sa.CheckConstraint(
            "source IN ('manual','alpaca','ibkr','tradier','paper')",
            name="trades_source_check",
        ),
    )
    op.create_index(
        "ix_trades_org_entry_at",
        "trades", ["org_id", "entry_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "ix_trades_org_symbol_entry_at",
        "trades", ["org_id", "symbol", "entry_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "ux_trades_external",
        "trades", ["org_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    op.execute("ALTER TABLE trades ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trades FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_trades ON trades
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON trades TO signaliq_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_trades ON trades")
    op.drop_index("ux_trades_external", table_name="trades")
    op.drop_index("ix_trades_org_symbol_entry_at", table_name="trades")
    op.drop_index("ix_trades_org_entry_at", table_name="trades")
    op.drop_table("trades")
