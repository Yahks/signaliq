"""Extend the gpt-oss-20b role constraint to exclude verification.

S.8.80 — the router falls through to gpt-oss-20b for verify when
all 120b credentials are exhausted. 20b flags every claim in its
batch: observed 12/19, 13/23, 6/17 on the same batch sizes that
120b handled with 1-2 flags. An honest "verify unavailable" is
better than twelve false positives.

Revision ID: 0058
Revises: 0057
"""
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing 20b rows: drop verification.
    op.execute("""
        UPDATE ai_credentials
        SET roles = array_remove(roles, 'verification'),
            updated_at = now()
        WHERE model_name = 'openai/gpt-oss-20b'
          AND 'verification' = ANY(roles)
    """)
    # Replace the CHECK with one that also excludes verification.
    op.execute("""
        ALTER TABLE ai_credentials
        DROP CONSTRAINT IF EXISTS ai_credentials_gpt_oss_20b_no_reasoning
    """)
    op.execute("""
        ALTER TABLE ai_credentials
        ADD CONSTRAINT ai_credentials_gpt_oss_20b_no_reasoning
        CHECK (
            model_name <> 'openai/gpt-oss-20b'
            OR (
                NOT ('reasoning' = ANY(roles))
                AND NOT ('verification' = ANY(roles))
            )
        )
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE ai_credentials
        DROP CONSTRAINT IF EXISTS ai_credentials_gpt_oss_20b_no_reasoning
    """)
    op.execute("""
        ALTER TABLE ai_credentials
        ADD CONSTRAINT ai_credentials_gpt_oss_20b_no_reasoning
        CHECK (
            model_name <> 'openai/gpt-oss-20b'
            OR NOT ('reasoning' = ANY(roles))
        )
    """)
