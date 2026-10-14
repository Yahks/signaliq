"""Fix Gemini fallback model name.

Migration 0029 registered gemini-2.5-flash, which Google has since
retired for new users. This migration swaps the row to gemini-3.6-flash
as instructed by the API error message.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        UPDATE ai_models
        SET model_name = 'gemini-3.6-flash'
        WHERE provider = 'gemini' AND model_name = 'gemini-2.5-flash'
    """)
    op.execute("""
        UPDATE ai_models
        SET metadata = '{"note": "fallback for groq/openai/gpt-oss-120b; free tier available"}'::jsonb
        WHERE provider = 'gemini' AND model_name = 'gemini-3.6-flash'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE ai_models
        SET model_name = 'gemini-2.5-flash'
        WHERE provider = 'gemini' AND model_name = 'gemini-3.6-flash'
    """)
