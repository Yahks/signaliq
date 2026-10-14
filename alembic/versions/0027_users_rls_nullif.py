"""Harden users RLS policy against empty-string app.current_org_id.

Before:
    (current_setting('app.current_org_id', true) IS NULL)
    OR EXISTS (... m.org_id = (current_setting('app.current_org_id', true))::uuid)

If a pooled connection has the setting as empty string (''), the ::uuid
cast fails with "invalid input syntax for type uuid".

After:
    NULLIF(current_setting(...), '')  -- empty string becomes NULL
    policies compare against the NULLIF'd value

Revision ID: 0027
Revises: 0022
Create Date: 2026-09-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop and recreate the users policy with NULLIF guards
    op.execute("DROP POLICY IF EXISTS tenant_isolation_users ON users")
    op.execute("""
        CREATE POLICY tenant_isolation_users ON users
            USING (
                NULLIF(current_setting('app.current_org_id', true), '') IS NULL
                OR EXISTS (
                    SELECT 1 FROM memberships m
                    WHERE m.user_id = users.id
                      AND m.org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                )
            )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_users ON users")
    op.execute("""
        CREATE POLICY tenant_isolation_users ON users
            USING (
                (current_setting('app.current_org_id', true) IS NULL)
                OR EXISTS (
                    SELECT 1 FROM memberships m
                    WHERE m.user_id = users.id
                      AND m.org_id = (current_setting('app.current_org_id', true))::uuid
                )
            )
    """)
