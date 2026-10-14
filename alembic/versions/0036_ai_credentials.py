"""AI credentials — BYOK (Bring Your Own Key).

Each org stores its own LLM provider API keys. The router reads from
this table instead of the global .env, so every user pays for their
own LLM usage.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-20
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("api_key_enc", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("last_429_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exhausted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("org_id", "label", name="uq_ai_credentials_org_label"),
    )
    op.create_index(
        "ix_ai_credentials_org_role",
        "ai_credentials",
        ["org_id", "role", "is_active", "priority"],
    )

    op.execute("ALTER TABLE ai_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_credentials FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_ai_credentials ON ai_credentials
            USING (
                NULLIF(current_setting('app.current_org_id', true), '') IS NULL
                OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_ai_credentials ON ai_credentials")
    op.drop_index("ix_ai_credentials_org_role", table_name="ai_credentials")
    op.drop_table("ai_credentials")
