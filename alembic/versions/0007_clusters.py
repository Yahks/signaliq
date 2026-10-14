"""Clusters and cluster_members tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- clusters ----
    op.create_table(
        "clusters",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("volume", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("sentiment_avg", sa.Float, nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_clusters_org", "clusters", ["org_id"])

    # ---- cluster_members ----
    op.create_table(
        "cluster_members",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("normalized_record_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("normalized_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("similarity", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("cluster_id", "normalized_record_id", name="uq_cluster_member"),
    )
    op.create_index("ix_cluster_members_org", "cluster_members", ["org_id", "cluster_id"])

    # ---- RLS ----
    for tbl in ("clusters", "cluster_members"):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{tbl} ON {tbl}
                USING (org_id = current_setting('app.current_org_id', true)::uuid)
        """)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO signaliq_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_cluster_members ON cluster_members")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_clusters ON clusters")
    op.drop_table("cluster_members")
    op.drop_table("clusters")
