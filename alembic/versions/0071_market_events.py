"""market_events — the shared structured context movers writes.

Every movers run writes one market_events row. The four
market-structure subblocks (economic_events, market_cap,
asset_classes, stock_lists) read the newest row as their source
of truth. One fetch, one event, four articles.

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-06
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_events",
        sa.Column("id", sa.Uuid(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        # The structured event record built by movers.
        sa.Column("event_record", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        # Convenience columns for querying.
        sa.Column("volatility_regime", sa.String(24), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"],
                                ondelete="CASCADE"),
    )
    op.create_index(
        "ix_market_events_org_event_at",
        "market_events",
        ["org_id", sa.text("event_at DESC")],
    )
    op.execute("ALTER TABLE market_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE market_events FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_market_events
        ON market_events
        USING (
            org_id = (current_setting('app.current_org_id'::text, true))::uuid
        )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_market_events ON market_events")
    op.drop_index("ix_market_events_org_event_at",
                  table_name="market_events")
    op.drop_table("market_events")
