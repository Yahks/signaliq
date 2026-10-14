"""Notifications and follows.

Adds:
  - chain_follows: users can follow chains for updates
  - notifications: unread items when something happens in a followed chain

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- chain_follows ----
    op.create_table(
        "chain_follows",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_chains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "chain_id", name="uq_user_chain_follow"),
    )
    op.create_index("ix_chain_follows_user", "chain_follows", ["user_id"])
    op.create_index("ix_chain_follows_chain", "chain_follows", ["chain_id"])

    # ---- notifications ----
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        # kind: chain_update | chain_ready | chain_failed | system
        sa.Column("chain_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_chains.id", ondelete="CASCADE"), nullable=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_notifications_user_unread", "notifications",
                    ["user_id", "read_at"])
    op.create_index("ix_notifications_user_created", "notifications",
                    ["user_id", "created_at"])

    # ---- RLS ----
    for tbl in ("chain_follows", "notifications"):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{tbl} ON {tbl}
                USING (org_id = current_setting('app.current_org_id', true)::uuid)
        """)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO signaliq_app")


def downgrade() -> None:
    for tbl in ("notifications", "chain_follows"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{tbl} ON {tbl}")
        op.drop_table(tbl)
