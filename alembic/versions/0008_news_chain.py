"""News chain: events, chains, links, posts, evidence.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- news_events ----
    op.create_table(
        "news_events",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("headline", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("actors", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("event_type", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'raw'")),
        sa.Column("confidence", sa.Float, nullable=False, server_default=sa.text("0.5")),
        sa.Column("embedding", sa.Text, nullable=True),  # placeholder; cast to vector below
        sa.Column("source_record_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=False)),
                  nullable=False, server_default=sa.text("'{}'::uuid[]")),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_news_events_org_time", "news_events", ["org_id", "occurred_at"])
    op.execute("ALTER TABLE news_events ALTER COLUMN embedding TYPE vector(768) USING embedding::vector(768)")
    op.execute("CREATE INDEX ix_news_events_embedding ON news_events USING hnsw (embedding vector_cosine_ops)")

    # ---- news_chains ----
    op.create_table(
        "news_chains",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("thesis", sa.Text, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("primary_entities", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("primary_topics", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_news_chains_org_status", "news_chains", ["org_id", "status"])

    # ---- chain_links ----
    op.create_table(
        "chain_links",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_chains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_event_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_event_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_type", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("chain_id", "from_event_id", "to_event_id", "link_type",
                            name="uq_chain_link"),
    )
    op.create_index("ix_chain_links_chain", "chain_links", ["chain_id", "sequence"])

    # ---- generated_posts ----
    op.create_table(
        "generated_posts",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_chains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("format", sa.String(32), nullable=False, server_default=sa.text("'long_form'")),
        sa.Column("tone", sa.String(32), nullable=False, server_default=sa.text("'neutral'")),
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("ai_models.id"), nullable=True),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("prompt_versions.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("generated_by", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_posts_org_status", "generated_posts", ["org_id", "status"])

    # ---- post_evidence ----
    op.create_table(
        "post_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("generated_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_text", sa.Text, nullable=False),
        sa.Column("claim_type", sa.String(32), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("char_start", sa.Integer, nullable=True),
        sa.Column("char_end", sa.Integer, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_post_evidence_post", "post_evidence", ["post_id"])

    # ---- post_versions ----
    op.create_table(
        "post_versions",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("generated_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("edited_by", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("edit_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("post_id", "version", name="uq_post_version"),
    )

    # ---- post_feedback ----
    op.create_table(
        "post_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("generated_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("rating", sa.Integer, nullable=True),
        sa.Column("verdict", sa.String(16), nullable=True),
        sa.Column("comments", sa.Text, nullable=True),
        sa.Column("diff", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    # ---- RLS + Grants ----
    for tbl in (
        "news_events", "news_chains", "chain_links", "generated_posts",
        "post_evidence", "post_versions", "post_feedback",
    ):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{tbl} ON {tbl}
                USING (org_id = current_setting('app.current_org_id', true)::uuid)
        """)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO signaliq_app")


def downgrade() -> None:
    for tbl in (
        "post_feedback", "post_versions", "post_evidence", "generated_posts",
        "chain_links", "news_chains", "news_events",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{tbl} ON {tbl}")
        op.drop_table(tbl)
