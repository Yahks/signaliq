"""Per-block auto toggle.

Each org can turn auto-generation on or off per block. When off,
Celery generator tasks for that block skip silently. Existing data
remains visible; only new generation is paused.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_block_settings",
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("block_slug", sa.String(64), nullable=False),
        sa.Column("auto_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("org_id", "block_slug", name="pk_org_block_settings"),
    )

    op.execute("ALTER TABLE org_block_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_block_settings FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_org_block_settings ON org_block_settings
            USING (
                NULLIF(current_setting('app.current_org_id', true), '') IS NULL
                OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
    """)

    # Backfill: every existing org gets toggles for 'trading' and 'news'
    op.execute("""
        INSERT INTO org_block_settings (org_id, block_slug, auto_enabled)
        SELECT o.id, b.slug, true
        FROM orgs o
        CROSS JOIN (VALUES ('trading'), ('news')) AS b(slug)
        ON CONFLICT (org_id, block_slug) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_org_block_settings ON org_block_settings")
    op.drop_table("org_block_settings")
