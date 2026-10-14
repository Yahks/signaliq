"""ai_credentials: strip the OR widening from the tenant policy.

S.9.37 — a prior one-off patch changed ai_credentials' policy to

    ((NULLIF(...) IS NULL) OR (org_id = NULLIF(...)::uuid))

The first clause is true when app.current_org_id is unset, so any
session without org context sees every row. That is a cross-tenant
leak, not a fix. This migration replaces it with the plain form
used everywhere else.

Revision ID: 0077
Revises: 0076
Create Date: 2026-10-13
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_ai_credentials "
        "ON ai_credentials"
    )
    op.execute("""
        CREATE POLICY tenant_isolation_ai_credentials ON ai_credentials
        USING (
            org_id = NULLIF(
                current_setting('app.current_org_id', true), ''
            )::uuid
        )
    """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_ai_credentials "
        "ON ai_credentials"
    )
    op.execute("""
        CREATE POLICY tenant_isolation_ai_credentials ON ai_credentials
        USING (
            (NULLIF(current_setting('app.current_org_id', true), '')
                IS NULL)
            OR (
                org_id = NULLIF(
                    current_setting('app.current_org_id', true), ''
                )::uuid
            )
        )
    """)
