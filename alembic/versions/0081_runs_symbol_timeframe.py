"""market_analysis_runs: add symbol and timeframe.

S.9.44 - the market_analysis rotation now picks a (stock,
timeframe) pair per article. Recording them in the run table
lets the picker avoid recent pairs, same as it already does for
(window, angle, focus).

Both columns nullable — pre-existing rows (before this change)
have no stock/timeframe and stay NULL.

Revision ID: 0081
Revises: 0080
Create Date: 2026-10-15
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE market_analysis_runs
            ADD COLUMN IF NOT EXISTS symbol text
    """)
    op.execute("""
        ALTER TABLE market_analysis_runs
            ADD COLUMN IF NOT EXISTS timeframe text
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE market_analysis_runs DROP COLUMN IF EXISTS timeframe"
    )
    op.execute(
        "ALTER TABLE market_analysis_runs DROP COLUMN IF EXISTS symbol"
    )
