"""S.8.4 — studio_messages.track column.

Per-message track selection: 'retail' or 'quant'. NULL means the
user did not choose, and the pipeline generates with no track
directive (the current behavior).

Retail track: mainstream stock market news, plain English, defines
terms, targets ordinary retail investors. The traffic driver.

Quant track: case studies with mechanisms, step-by-step reasoning,
assumes professional vocabulary. The credibility layer.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text as sql_text


revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        ALTER TABLE studio_messages
        ADD COLUMN track varchar(16) NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        ALTER TABLE studio_messages DROP COLUMN IF EXISTS track
    """))
