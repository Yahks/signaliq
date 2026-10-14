"""trader_process — add trade_history_summary column.

The reviewer's critique: articles lack trading logs, equity
curves, or longer-term validation data. The register's evidence
rule says "every NUMBER must come from the process document",
but the process document describes rules — not results.

This column is where the trader writes their aggregate
performance: number of trades, win rate, average win, average
loss, max drawdown, Sharpe, longest streaks. Prose. The
register quotes it for any performance claim.

Revision ID: 0072
Revises: 0071
Create Date: 2026-10-06
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for tbl in (
        "trader_process",
        "trader_process_drafts",
        "trader_process_history",
    ):
        op.add_column(
            tbl,
            sa.Column(
                "trade_history_summary",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            ),
        )


def downgrade() -> None:
    for tbl in (
        "trader_process",
        "trader_process_drafts",
        "trader_process_history",
    ):
        op.drop_column(tbl, "trade_history_summary")
