"""Blocks: domain-scoped pipelines with style guides and subtopics.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-15
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- blocks ----
    op.create_table(
        "blocks",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("style_guide", sa.Text, nullable=True),
        sa.Column("example_output", sa.Text, nullable=True),
        sa.Column("config", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("subtopics", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_system", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("org_id", "slug", name="uq_block_org_slug"),
    )
    op.create_index("ix_blocks_org", "blocks", ["org_id", "is_active"])
    # For system blocks (org_id NULL), a second unique index for global slug uniqueness
    op.create_index(
        "ix_blocks_system_slug_unique",
        "blocks",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("org_id IS NULL"),
    )

    # ---- add block_id + subtopic_slug to news_events ----
    op.add_column("news_events",
                  sa.Column("block_id", postgresql.UUID(as_uuid=False),
                            sa.ForeignKey("blocks.id", ondelete="SET NULL"),
                            nullable=True))
    op.add_column("news_events",
                  sa.Column("subtopic_slug", sa.String(64), nullable=True))
    op.create_index("ix_news_events_block", "news_events",
                    ["block_id", "occurred_at"])

    # ---- add block_id + subtopic_slug to news_chains ----
    op.add_column("news_chains",
                  sa.Column("block_id", postgresql.UUID(as_uuid=False),
                            sa.ForeignKey("blocks.id", ondelete="SET NULL"),
                            nullable=True))
    op.add_column("news_chains",
                  sa.Column("subtopic_slug", sa.String(64), nullable=True))
    op.create_index("ix_news_chains_block", "news_chains",
                    ["block_id", "created_at"])

    # ---- Grants for signaliq_app ----
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON blocks TO signaliq_app")
    # blocks is NOT tenant-scoped (org_id nullable) so no RLS on it

    # ---- RLS note ----
    # blocks table: system blocks have org_id=NULL and are visible to everyone.
    # Custom blocks belong to one org. We enforce this at the query layer,
    # not with RLS, because system rows must bypass tenant filtering.


def downgrade() -> None:
    op.drop_index("ix_news_chains_block", "news_chains")
    op.drop_column("news_chains", "subtopic_slug")
    op.drop_column("news_chains", "block_id")

    op.drop_index("ix_news_events_block", "news_events")
    op.drop_column("news_events", "subtopic_slug")
    op.drop_column("news_events", "block_id")

    op.drop_index("ix_blocks_system_slug_unique", "blocks")
    op.drop_index("ix_blocks_org", "blocks")
    op.drop_table("blocks")
