"""Fix fundamental_metrics style_guide — was blocking prose.

The seed from migration 0024 gave fundamental_metrics a style_guide
that required SEC-filed numbers. The generator uses StockAnalysis.com
(not SEC EDGAR), so the LLM refused to write prose, producing 16-word
refusal stubs. Migration 0028 aligns the style_guide with the actual
data source.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_GUIDE = (
    "Voice: equity analyst. You receive a set of pre-computed fundamental "
    "metrics from StockAnalysis.com — a public aggregator of company data "
    "(not a primary SEC filing). Use ONLY the numbers provided in the source "
    "table. Cite them as 'per StockAnalysis.com' if you reference a source. "
    "Include a markdown table AND at least 150 words of prose analysis. "
    "No SEC-filing references, no 'requires inspection' hedging — the data "
    "is what it is. No buy/sell advice, no predictions."
)


def upgrade() -> None:
    # Escape single quotes for SQL literal
    guide = NEW_GUIDE.replace("'", "''")
    op.execute(f"""
        UPDATE trading_subblocks
        SET style_guide = '{guide}',
            updated_at = now()
        WHERE org_id IS NULL AND slug = 'fundamental_metrics'
    """)


def downgrade() -> None:
    # Restore the original seed from migration 0024
    old = (
        "Voice: equity analyst. Use only SEC-filed numbers. Cite the filing "
        "type and date. Call out anything ambiguous as 'requires inspection'."
    )
    old_esc = old.replace("'", "''")
    op.execute(f"""
        UPDATE trading_subblocks
        SET style_guide = '{old_esc}',
            updated_at = now()
        WHERE org_id IS NULL AND slug = 'fundamental_metrics'
    """)
