"""Fast-role fallback chain for Groq 429s.

Reactivates 4 working fast-role fallbacks in priority order and removes
3 retired models that returned 404 during testing.

Priority (created_at ascending):
  1. groq/openai/gpt-oss-20b          (already active, unchanged)
  2. gemini/gemini-flash-latest       (reactivated, created_at = now()+1s)
  3. openrouter/nvidia/nemotron-3-... (reactivated, created_at = now()+2s)
  4. openrouter/nex-agi/nex-n2.5-mini (reactivated, created_at = now()+3s)
  5. ollama/qwen2.5:0.5b-instruct     (reactivated, created_at = now()+4s)

Deleted (tested, returned 404 or connection error):
  - groq/llama-3.3-70b-versatile
  - openrouter/google/gemini-2.0-flash-exp:free
  - openrouter/google/gemma-4-31b-it:free

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (provider, model_name) to reactivate, in priority order
REACTIVATE = [
    ("gemini", "gemini-flash-latest"),
    ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
    ("openrouter", "nex-agi/nex-n2.5-mini:free"),
    ("ollama", "qwen2.5:0.5b-instruct"),
]

# (provider, model_name) to delete (tested, no longer valid)
DELETE = [
    ("groq", "llama-3.3-70b-versatile"),
    ("openrouter", "google/gemini-2.0-flash-exp:free"),
    ("openrouter", "google/gemma-4-31b-it:free"),
]


def upgrade() -> None:
    # Reactivate + bump created_at so they sort after the primary
    for i, (provider, model_name) in enumerate(REACTIVATE):
        offset_seconds = i + 1
        p = provider.replace("'", "''")
        m = model_name.replace("'", "''")
        op.execute(f"""
            UPDATE ai_models
            SET is_active = true,
                created_at = now() + INTERVAL '{offset_seconds} seconds'
            WHERE provider = '{p}'
              AND model_name = '{m}'
              AND role = 'fast'
        """)

    # Soft-delete retired models. Hard delete would violate the FK
    # from ai_calls — audit trail must be preserved.
    for provider, model_name in DELETE:
        p = provider.replace("'", "''")
        m = model_name.replace("'", "''")
        op.execute(f"""
            UPDATE ai_models
            SET metadata = jsonb_set(
                    COALESCE(metadata, '{{}}'::jsonb),
                    '{{decommissioned}}',
                    to_jsonb('model returned 404; kept for historical ai_calls'::text)
                ),
                is_active = false
            WHERE provider = '{p}'
              AND model_name = '{m}'
              AND role = 'fast'
        """)


def downgrade() -> None:
    # Deactivate the 4 we activated (without restoring created_at)
    for provider, model_name in REACTIVATE:
        p = provider.replace("'", "''")
        m = model_name.replace("'", "''")
        op.execute(f"""
            UPDATE ai_models
            SET is_active = false
            WHERE provider = '{p}' AND model_name = '{m}' AND role = 'fast'
        """)
    # Note: deleted rows are not restored — this is a one-way cleanup
