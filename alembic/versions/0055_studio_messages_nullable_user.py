"""S.8.9.7 — studio_messages.user_id becomes nullable.

Auto-generated articles (from the movers subblock) are not
attached to a user prompt. They appear in the chat feed as
system-generated messages. NULL user_id signals that.

The chat endpoint continues to write user_id for real user
prompts; only the scanner writes NULL.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text as sql_text


revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        ALTER TABLE studio_messages
            ALTER COLUMN user_id DROP NOT NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        ALTER TABLE studio_messages
            ALTER COLUMN user_id SET NOT NULL
    """))
