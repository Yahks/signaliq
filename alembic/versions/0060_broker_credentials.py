"""Broker credentials: the trader's Alpaca (or other broker) keys.

S.8.90 — Paper Account tab. The org stores its broker API
key + secret, encrypted at rest with Fernet (same cipher as
ai_credentials). The credentials power:
  - the Paper Account tab (account summary, positions, orders)
  - the trade-monitor pipeline (fills upserted into trades)
  - the register's Decision beat (via today_trades)

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broker_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("api_key_enc", sa.Text, nullable=False),
        sa.Column("api_secret_enc", sa.Text, nullable=False),
        sa.Column("paper", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        sa.Column("monitor_enabled", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_error", sa.Text, nullable=True),
        sa.Column("key_fingerprint", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("org_id", "broker", name="uq_broker_credentials_org_broker"),
        sa.CheckConstraint(
            "broker IN ('alpaca','ibkr','tradier')",
            name="broker_credentials_broker_check",
        ),
    )
    op.create_index(
        "ix_broker_credentials_org",
        "broker_credentials", ["org_id"],
        postgresql_using="btree",
    )

    op.execute("ALTER TABLE broker_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE broker_credentials FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_broker_credentials ON broker_credentials
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
    """)
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON broker_credentials TO signaliq_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_broker_credentials ON broker_credentials"
    )
    op.drop_index("ix_broker_credentials_org", table_name="broker_credentials")
    op.drop_table("broker_credentials")
