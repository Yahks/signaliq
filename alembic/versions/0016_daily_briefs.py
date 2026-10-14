"""Daily briefs: aggregated summaries of the last 24h across all chains.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_briefs",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("brief_date", sa.Date, nullable=False),
        sa.Column("block_slug", sa.String(64), nullable=False, server_default=sa.text("'trading'")),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column("event_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=False)),
                  nullable=False, server_default=sa.text("'{}'::uuid[]")),
        sa.Column("chain_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=False)),
                  nullable=False, server_default=sa.text("'{}'::uuid[]")),
        sa.Column("generated_by", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("ai_models.id"), nullable=True),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=False),
                  na.ForeignKey if False else sa.ForeignKey("prompt_versions.id"),
                  nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_daily_briefs_org_date", "daily_briefs", ["org_id", "brief_date"])

    # RLS
    op.execute("ALTER TABLE daily_briefs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE daily_briefs FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_daily_briefs ON daily_briefs
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON daily_briefs TO signaliq_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_daily_briefs ON daily_briefs")
    op.drop_table("daily_briefs")
