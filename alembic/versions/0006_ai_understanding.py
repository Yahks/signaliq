"""AI understanding: ai_enrichments, entities, entity_mentions, topics,
topic_memberships, pain_points, feature_requests.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- ai_enrichments ----
    op.create_table(
        "ai_enrichments",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("normalized_record_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("normalized_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("ai_models.id"), nullable=True),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("prompt_versions.id"), nullable=True),

        sa.Column("sentiment_label", sa.String(16), nullable=True),
        sa.Column("sentiment_score", sa.Float, nullable=True),
        sa.Column("sentiment_confidence", sa.Float, nullable=True),
        sa.Column("emotions", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("intent", sa.String(32), nullable=True),
        sa.Column("intent_confidence", sa.Float, nullable=True),

        sa.Column("raw_topics", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("raw_entities", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("pain_point_signals", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("feature_request_signals", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),

        sa.Column("is_sarcasm", sa.Boolean, nullable=True),
        sa.Column("sarcasm_confidence", sa.Float, nullable=True),

        sa.Column("raw_response", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("normalized_record_id", name="uq_enrichment_record"),
    )
    op.create_index("ix_enrich_org", "ai_enrichments", ["org_id"])
    op.create_index("ix_enrich_sentiment", "ai_enrichments", ["org_id", "sentiment_label"])

    # ---- entities ----
    op.create_table(
        "entities",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("org_id", "canonical_name", "type", name="uq_entity_org_name_type"),
    )

    # ---- entity_aliases ----
    op.create_table(
        "entity_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default=sa.text("'llm'")),
        sa.UniqueConstraint("org_id", "alias", name="uq_alias_org_alias"),
    )

    # ---- entity_mentions ----
    op.create_table(
        "entity_mentions",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("normalized_record_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("normalized_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("surface_form", sa.String(255), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("sentiment_score", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_ent_mentions_org", "entity_mentions", ["org_id", "entity_id"])
    op.create_index("ix_ent_mentions_record", "entity_mentions", ["normalized_record_id"])

    # ---- topics ----
    op.create_table(
        "topics",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("parent_topic_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("topics.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_hidden", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_curated", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("org_id", "name", name="uq_topic_org_name"),
    )

    # ---- topic_memberships ----
    op.create_table(
        "topic_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("normalized_record_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("normalized_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("similarity", sa.Float, nullable=True),
        sa.Column("assigned_by", sa.String(16), nullable=False, server_default=sa.text("'llm'")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("topic_id", "normalized_record_id", name="uq_topic_member"),
    )

    # ---- pain_points ----
    op.create_table(
        "pain_points",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("topic_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("topics.id", ondelete="SET NULL"), nullable=True),
        sa.Column("severity", sa.Float, nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    # ---- feature_requests ----
    op.create_table(
        "feature_requests",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("demand_score", sa.Float, nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    # ---- RLS + Grants ----
    for tbl in (
        "ai_enrichments", "entities", "entity_aliases", "entity_mentions",
        "topics", "topic_memberships", "pain_points", "feature_requests",
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
        "feature_requests", "pain_points", "topic_memberships", "topics",
        "entity_mentions", "entity_aliases", "entities", "ai_enrichments",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{tbl} ON {tbl}")
        op.drop_table(tbl)
