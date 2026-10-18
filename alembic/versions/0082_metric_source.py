"""market_conditions: add metric_source column.

S.9.44 - the trader's dashboard readings each need a definition
in the article. Yahoo Finance shows "P/E Ratio: 28.45" and
separately shows "Price ÷ trailing EPS" as the definition. The
trader's dashboard should work the same way: the reading is the
number, the metric_source column is the definition.

One column. Nullable. Pre-existing rows stay NULL.

Revision ID: 0082
Revises: 0081
Create Date: 2026-10-16
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE market_conditions
            ADD COLUMN IF NOT EXISTS metric_source text
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE market_conditions DROP COLUMN IF EXISTS metric_source"
    )
