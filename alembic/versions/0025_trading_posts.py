"""Trading posts: generated content per subblock per org.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_posts",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subblock_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("trading_subblocks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("format", sa.String(32), nullable=False, server_default=sa.text("'long_form'")),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_payload", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_urls", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("symbols", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("model_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("ai_models.id"), nullable=True),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("prompt_versions.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_trading_posts_org_subblock_time",
        "trading_posts", ["org_id", "subblock_id", "published_at"],
        postgresql_using="btree",
    )
    op.create_index("ix_trading_posts_source_kind", "trading_posts", ["source_kind"])

    op.execute("ALTER TABLE trading_posts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trading_posts FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_trading_posts ON trading_posts
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON trading_posts TO signaliq_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_trading_posts ON trading_posts")
    op.drop_index("ix_trading_posts_source_kind", table_name="trading_posts")
    op.drop_index("ix_trading_posts_org_subblock_time", table_name="trading_posts")
    op.drop_table("trading_posts")
