"""market_analysis_rejections: track failed articles by reason.

S.9.50 — at 500/week, silent draft failures are invisible.
Every article that fails Layer 1 (block validation) or Layer 2
(article verification) is recorded here with a named reason.
Query the table to find the dominant failure class.

Revision ID: 0087
Revises: 0086
Create Date: 2026-10-18
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0087"
down_revision: str | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS market_analysis_rejections (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            symbol text,
            timeframe text,
            stage text NOT NULL,
            reason text NOT NULL,
            detail text,
            block_snapshot jsonb,
            article_body text
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_mar_org_created
        ON market_analysis_rejections (org_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_mar_reason
        ON market_analysis_rejections (org_id, reason, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_mar_reason")
    op.execute("DROP INDEX IF EXISTS ix_mar_org_created")
    op.execute("DROP TABLE IF EXISTS market_analysis_rejections")
