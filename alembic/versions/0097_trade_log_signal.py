"""trade_log: which fundamental signal produced the trade.

S.9.96 — every article needs a real trade. This column records the
signal state a trade came from (e.g. spread/backwardation_widening),
so the article can cite the trade and the math can filter by state.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0097"
down_revision: str | None = "0096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS signal text")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_trade_log_signal
        ON trade_log (org_id, signal) WHERE deleted_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_trade_log_signal")
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS signal")
