"""AI governance: ai_models, prompt_versions, ai_calls, embeddings.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- ai_models ----
    op.create_table(
        "ai_models",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("context_window", sa.Integer, nullable=True),
        sa.Column("input_cost_per_1k", sa.Numeric(10, 6), nullable=True),
        sa.Column("output_cost_per_1k", sa.Numeric(10, 6), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("provider", "model_name", name="uq_ai_models_provider_name"),
    )
    op.create_index("ix_ai_models_role", "ai_models", ["role", "is_active"])

    # ---- prompt_versions ----
    op.create_table(
        "prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("template", sa.Text, nullable=False),
        sa.Column("variables", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_prompt_name_version"),
    )
    op.create_index(
        "ix_prompt_one_active", "prompt_versions", ["name"],
        unique=True, postgresql_where=sa.text("is_active = true"),
    )

    # ---- ai_calls ----
    op.create_table(
        "ai_calls",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("ai_models.id"), nullable=True),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("prompt_versions.id"), nullable=True),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=True),
        sa.Column("cached", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("response_payload", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_ai_calls_org_time", "ai_calls", ["org_id", "created_at"])
    op.create_index("ix_ai_calls_hash", "ai_calls", ["request_hash"])

    # ---- embeddings ----
    op.create_table(
        "embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_type", sa.String(32), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("ai_models.id"), nullable=False),
        sa.Column("dim", sa.Integer, nullable=False),
        sa.Column("vector", sa.Text, nullable=False),  # stored as text, cast to vector on query
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("owner_type", "owner_id", "model_id", name="uq_embedding_owner_model"),
    )
    op.create_index("ix_emb_owner", "embeddings", ["org_id", "owner_type", "owner_id"])

    # Cast vector column to real `vector(768)` type
    op.execute("ALTER TABLE embeddings ALTER COLUMN vector TYPE vector(768) USING vector::vector(768)")
    op.execute("CREATE INDEX ix_emb_vector_hnsw ON embeddings USING hnsw (vector vector_cosine_ops)")

    # ---- RLS ----
    op.execute("ALTER TABLE embeddings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE embeddings FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_embeddings ON embeddings
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
    """)

    # ---- Grants ----
    for tbl in ("ai_models", "prompt_versions", "ai_calls", "embeddings"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO signaliq_app")

    # ---- Seed the Ollama models ----
    op.execute("""
        INSERT INTO ai_models (provider, model_name, role, context_window, input_cost_per_1k, output_cost_per_1k)
        VALUES
            ('ollama', 'qwen2.5:0.5b-instruct', 'fast',      32768, 0, 0),
            ('ollama', 'qwen2.5:0.5b-instruct', 'reasoning', 32768, 0, 0),
            ('ollama', 'nomic-embed-text',       'embedding',  8192, 0, 0)
        ON CONFLICT (provider, model_name) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_embeddings ON embeddings")
    op.drop_table("embeddings")
    op.drop_table("ai_calls")
    op.execute("DROP INDEX IF EXISTS ix_prompt_one_active")
    op.drop_table("prompt_versions")
    op.drop_table("ai_models")
