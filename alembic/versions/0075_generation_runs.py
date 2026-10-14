"""Generation runs — one row per article attempt.

Records every attempt to generate a subblock article, whether it
succeeded, failed, or produced a draft the validator rejected. The
UI reads this so the user sees what the worker actually did.

Revision ID: 0075
Revises: 9cd0b5b31995
Create Date: 2026-10-12
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS generation_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
            subblock_slug TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            attempt_count INT NOT NULL DEFAULT 0,
            credential_rotations INT NOT NULL DEFAULT 0,
            post_id UUID,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_generation_runs_org_started
        ON generation_runs (org_id, started_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_generation_runs_org_started")
    op.execute("DROP TABLE IF EXISTS generation_runs")
