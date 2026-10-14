"""Per-org AI cost tracking and budget caps.

Adds:
  - orgs.ai_config JSONB: per-org budget settings
  - org_ai_usage: daily counters per org (tokens, cost, calls)
  - org_ai_usage_current: materialized view of today's usage (fast lookup)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- Add ai_config to orgs ----
    op.add_column("orgs",
                  sa.Column("ai_config", postgresql.JSONB,
                            nullable=False, server_default=sa.text("'{}'::jsonb")))

    # Default ai_config shape:
    # {
    #   "daily_cost_cap_usd": 5.0,
    #   "monthly_soft_cap_usd": 50.0,
    #   "daily_token_cap": 500000,
    #   "enabled": true,
    #   "alert_at_percent": 80
    # }

    # ---- org_ai_usage (daily counters) ----
    op.create_table(
        "org_ai_usage",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("usage_date", sa.Date, nullable=False),
        sa.Column("input_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("calls", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("last_updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("org_id", "usage_date", name="uq_org_usage_date"),
    )
    op.create_index("ix_org_ai_usage_org_date", "org_ai_usage", ["org_id", "usage_date"])

    # ---- RLS + grants ----
    op.execute("ALTER TABLE org_ai_usage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_ai_usage FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_org_ai_usage ON org_ai_usage
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON org_ai_usage TO signaliq_app")
    # Owner role for background writes without RLS
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON org_ai_usage TO signaliq")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_org_ai_usage ON org_ai_usage")
    op.drop_table("org_ai_usage")
    op.drop_column("orgs", "ai_config")
