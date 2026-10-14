"""Ingestion tables: sources, ingestion_jobs, raw_records, normalized_records.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- sources ----
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("collection_method", sa.Text, nullable=True),
        sa.Column("retention_policy", sa.String(64), nullable=True),
        sa.Column("config", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sources_org", "sources", ["org_id"])

    # ---- ingestion_jobs ----
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("file_path", sa.Text, nullable=True),
        sa.Column("file_format", sa.String(16), nullable=True),
        sa.Column("total_records", sa.Integer, nullable=True),
        sa.Column("processed_records", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("failed_records", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("mapping", postgresql.JSONB, nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_ingestion_jobs_org_status", "ingestion_jobs",
                    ["org_id", "status"])

    # ---- raw_records (partitioned by created_at) ----
    op.execute("""
        CREATE TABLE raw_records (
            id                 uuid NOT NULL DEFAULT gen_random_uuid(),
            org_id             uuid NOT NULL,
            source_id          uuid NOT NULL,
            ingestion_job_id   uuid,
            source_record_id   text,
            author_identifier  text,
            original_timestamp timestamptz,
            text               text NOT NULL,
            language           text,
            metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
            content_hash       text NOT NULL,
            created_at         timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    """)
    # Default partition catches any row until monthly partitions are added
    op.execute("""
        CREATE TABLE raw_records_default PARTITION OF raw_records DEFAULT
    """)
    op.execute("CREATE INDEX ix_raw_records_org_time ON raw_records (org_id, created_at DESC)")
    op.execute("CREATE INDEX ix_raw_records_hash ON raw_records (org_id, content_hash)")
    op.execute("CREATE INDEX ix_raw_records_source ON raw_records (source_id)")

    # ---- normalized_records ----
    op.create_table(
        "normalized_records",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_record_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("normalized_text", sa.Text, nullable=False),
        sa.Column("translated_text", sa.Text, nullable=True),
        sa.Column("translation_lang", sa.String(16), nullable=True),
        sa.Column("detected_language", sa.String(16), nullable=False),
        sa.Column("language_confidence", sa.Float, nullable=True),
        sa.Column("is_duplicate", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("duplicate_of_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("is_spam", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_bot_suspected", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("quality_score", sa.Float, nullable=True),
        sa.Column("tokens_count", sa.Integer, nullable=True),
        sa.Column("urls", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("hashtags", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("mentions", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_norm_org", "normalized_records", ["org_id"])
    op.create_index("ix_norm_raw", "normalized_records", ["raw_record_id"])
    op.create_index("ix_norm_lang", "normalized_records", ["org_id", "detected_language"])

    # ---- Grants for signaliq_app on new tables ----
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON sources TO signaliq_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ingestion_jobs TO signaliq_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON raw_records TO signaliq_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON raw_records_default TO signaliq_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON normalized_records TO signaliq_app")

    # ---- Force RLS + policies on tenant tables ----
    for tbl in ("sources", "ingestion_jobs", "raw_records", "normalized_records"):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{tbl} ON {tbl}
                USING (org_id = current_setting('app.current_org_id', true)::uuid)
        """)


def downgrade() -> None:
    for tbl in ("normalized_records", "raw_records", "ingestion_jobs", "sources"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{tbl} ON {tbl}")
    op.drop_table("normalized_records")
    op.execute("DROP TABLE raw_records")
    op.drop_table("ingestion_jobs")
    op.drop_table("sources")
