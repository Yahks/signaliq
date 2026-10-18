"""trade_log: add entry_time, exit_time, setup_detail, hesitation_note.

S.9.46 - the reviewer wants specific times, specific prices, and
a specific moment of hesitation in the article. The trade is the
spine. These four columns give the template the raw material to
render that specificity.

All nullable. Existing rows stay NULL. New rows filled by the
trade generator.

Revision ID: 0084
Revises: 0083
Create Date: 2026-10-17
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trade_log "
        "ADD COLUMN IF NOT EXISTS entry_time timestamptz"
    )
    op.execute(
        "ALTER TABLE trade_log "
        "ADD COLUMN IF NOT EXISTS exit_time timestamptz"
    )
    op.execute(
        "ALTER TABLE trade_log "
        "ADD COLUMN IF NOT EXISTS setup_detail text"
    )
    op.execute(
        "ALTER TABLE trade_log "
        "ADD COLUMN IF NOT EXISTS hesitation_note text"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS hesitation_note")
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS setup_detail")
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS exit_time")
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS entry_time")
