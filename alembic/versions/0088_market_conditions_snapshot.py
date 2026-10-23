"""market_conditions: daily snapshot columns and rule_version.

S.9.72 — the trader's market_conditions readings are entered, not
computed, so there is no history: six rows over one week. Without a
series, regime persistence cannot be measured and conditional
expectancy has no in-regime sample.

- snapshot_of: when a row is a daily auto-snapshot of another row,
  this points at the source row. NULL for trader-entered rows.
- rule_version: which rule set was live when the snapshot was taken,
  so an article can say "the 678 trades span v2 and v3".

Revision ID: 0088
Revises: 0087
Create Date: 2026-10-23
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0088"
down_revision: str | None = "0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE market_conditions
        ADD COLUMN IF NOT EXISTS snapshot_of uuid
    """)
    op.execute("""
        ALTER TABLE market_conditions
        ADD COLUMN IF NOT EXISTS rule_version text
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_mc_org_created
        ON market_conditions (org_id, created_at DESC)
        WHERE deleted_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_mc_org_created")
    op.execute("ALTER TABLE market_conditions DROP COLUMN IF EXISTS rule_version")
    op.execute("ALTER TABLE market_conditions DROP COLUMN IF EXISTS snapshot_of")
