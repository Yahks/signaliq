"""trader_process — four more sections so every dimension is unique.

The registry has 14 dimensions. Without distinct source sections,
D5 duplicates D4, D10 duplicates D2, D11 duplicates D1, and D13
duplicates D8. Add rolling, stocks_vs_options, universes, and
emotion_removal so each dimension reads its own section.

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-04
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_COLS = ("rolling", "stocks_vs_options", "universes", "emotion_removal")


def upgrade() -> None:
    for tbl in ("trader_process", "trader_process_drafts",
                "trader_process_history"):
        for col in NEW_COLS:
            op.add_column(
                tbl,
                sa.Column(col, sa.Text(), nullable=False,
                          server_default=sa.text("''")),
            )


def downgrade() -> None:
    for tbl in ("trader_process", "trader_process_drafts",
                "trader_process_history"):
        for col in NEW_COLS:
            op.drop_column(tbl, col)
