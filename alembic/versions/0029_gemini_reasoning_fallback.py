"""Register Gemini as a fallback reasoning model.

The AI router already iterates ai_models in created_at order and uses
the first successful provider. Adding gemini-2.5-flash as a second
reasoning model makes any Groq 429 fall through automatically.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO ai_models (provider, model_name, role, is_active, metadata)
        VALUES (
            'gemini',
            'gemini-2.5-flash',
            'reasoning',
            true,
            '{"note": "fallback for groq/openai/gpt-oss-120b; free tier available"}'::jsonb
        )
        ON CONFLICT (provider, model_name) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM ai_models
        WHERE provider = 'gemini' AND model_name = 'gemini-2.5-flash'
    """)
