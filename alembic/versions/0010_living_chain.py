"""Living chain: chain_updates, chain_impacts, chain_scenarios, chain_watchlist.

Makes news_chains a living research page rather than a one-off article:
  - chain_updates:        events attached to an existing chain over time
  - chain_impacts:        "what could this affect?" — sectors, entities, assets
  - chain_scenarios:      2-4 conditional futures with directional pressure
  - chain_watchlist:      indicators/tickers to monitor

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- chain_updates (event attached to a chain) ----
    op.create_table(
        "chain_updates",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_chains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("similarity", sa.Float, nullable=False),
        sa.Column("reasoning", sa.Text, nullable=True),
        sa.Column("attached_by", sa.String(16), nullable=False,
                  server_default=sa.text("'auto'")),  # auto | human
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("chain_id", "event_id", name="uq_chain_update"),
    )
    op.create_index("ix_chain_updates_chain", "chain_updates", ["chain_id", "created_at"])

    # ---- chain_impacts (what this chain could affect) ----
    op.create_table(
        "chain_impacts",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_chains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        # category: sector | commodity | asset_class | currency | credit | region | company
        sa.Column("direction", sa.String(16), nullable=True),
        # direction: up | down | mixed | uncertain
        sa.Column("strength", sa.String(16), nullable=True),
        # strength: strong | moderate | weak | unknown
        sa.Column("reasoning", sa.Text, nullable=True),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_chain_impacts_chain", "chain_impacts", ["chain_id", "position"])

    # ---- chain_scenarios (conditional futures) ----
    op.create_table(
        "chain_scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_chains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("condition", sa.Text, nullable=False),
        sa.Column("reasoning", sa.Text, nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_chain_scenarios_chain", "chain_scenarios", ["chain_id", "position"])

    # ---- chain_watchlist (what to monitor) ----
    op.create_table(
        "chain_watchlist",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("news_chains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),  # optional ticker/index
        sa.Column("reasoning", sa.Text, nullable=True),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_chain_watchlist_chain", "chain_watchlist", ["chain_id", "position"])

    # ---- news_chains: add "living" fields ----
    op.add_column("news_chains",
                  sa.Column("embedding", sa.Text, nullable=True))
    op.add_column("news_chains",
                  sa.Column("last_updated_at", sa.DateTime(timezone=True),
                            server_default=sa.text("now()"), nullable=True))
    op.add_column("news_chains",
                  sa.Column("update_count", sa.Integer, nullable=False, server_default=sa.text("0")))

    # Cast embedding column to vector(768)
    op.execute("ALTER TABLE news_chains ALTER COLUMN embedding TYPE vector(768) USING embedding::vector(768)")
    op.execute("CREATE INDEX ix_news_chains_embedding ON news_chains USING hnsw (embedding vector_cosine_ops)")

    # ---- RLS ----
    for tbl in ("chain_updates", "chain_impacts", "chain_scenarios", "chain_watchlist"):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{tbl} ON {tbl}
                USING (org_id = current_setting('app.current_org_id', true)::uuid)
        """)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO signaliq_app")


def downgrade() -> None:
    for tbl in ("chain_watchlist", "chain_scenarios", "chain_impacts", "chain_updates"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{tbl} ON {tbl}")
        op.drop_table(tbl)

    op.execute("DROP INDEX IF EXISTS ix_news_chains_embedding")
    op.drop_column("news_chains", "update_count")
    op.drop_column("news_chains", "last_updated_at")
    op.drop_column("news_chains", "embedding")
