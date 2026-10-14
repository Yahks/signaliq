"""Enforce: openai/gpt-oss-20b cannot serve the reasoning role.

The router selects generation credentials by role. When 120b
credentials exhaust, the router falls through to any credential
whose roles include 'reasoning'. gpt-oss-20b produces shorter,
flatter articles — the exact failure the reviewer named — so it
must not serve reasoning. It can still serve 'fast'.

Per-org: every org's credential rows are validated. The CHECK
constraint fires on INSERT and UPDATE, so a new org adding a
20b reasoning credential fails at the DB level.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-01
"""
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove reasoning from existing 20b rows across all orgs.
    op.execute("""
        UPDATE ai_credentials
        SET roles = array_remove(roles, 'reasoning'),
            updated_at = now()
        WHERE model_name = 'openai/gpt-oss-20b'
          AND 'reasoning' = ANY(roles)
    """)
    # Enforce the invariant per-row for future inserts and updates.
    op.execute("""
        ALTER TABLE ai_credentials
        ADD CONSTRAINT ai_credentials_gpt_oss_20b_no_reasoning
        CHECK (
            model_name <> 'openai/gpt-oss-20b'
            OR NOT ('reasoning' = ANY(roles))
        )
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE ai_credentials
        DROP CONSTRAINT IF EXISTS ai_credentials_gpt_oss_20b_no_reasoning
    """)
