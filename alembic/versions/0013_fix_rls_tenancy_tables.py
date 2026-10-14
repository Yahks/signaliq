"""Fix RLS on orgs, users, memberships.

Migration 0003 forgot to `ENABLE ROW LEVEL SECURITY` on these tables —
it only ran `FORCE ROW LEVEL SECURITY`, which requires RLS to already
be enabled to have any effect.

Also adds policies to `orgs` and `users` (they had none, meaning every
query was denied once RLS was enforced — but since RLS was disabled,
they were wide open).

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-17
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- memberships ----
    # ENABLE was missing — the policy existed but was ignored.
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    # Ensure the policy uses both USING (read) and WITH CHECK (write).
    op.execute("DROP POLICY IF EXISTS tenant_isolation_memberships ON memberships")
    op.execute("""
        CREATE POLICY tenant_isolation_memberships ON memberships
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)
    """)

    # ---- orgs ----
    # The org table itself: users in the current org can see their own org.
    # There is no cross-org boundary here — each row IS an org.
    # We allow SELECT on the current org only; INSERT/UPDATE/DELETE allowed
    # because signup creates orgs.
    op.execute("ALTER TABLE orgs ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_orgs ON orgs")
    op.execute("""
        CREATE POLICY tenant_isolation_orgs ON orgs
            USING (
                id = current_setting('app.current_org_id', true)::uuid
                OR current_setting('app.current_org_id', true) IS NULL
            )
            WITH CHECK (true)
    """)
    # Rationale for the two clauses:
    #   - id = current_org      → app role reads only its own org
    #   - current_org IS NULL   → allows registration (no org context yet)
    #   - WITH CHECK (true)     → allows inserting new orgs during signup

    # ---- users ----
    # Users are global (one user can belong to multiple orgs). We allow:
    #   - Reading a user if they are a member of the current org
    #   - Inserting during registration (no tenant context)
    #   - Reading self (for /api/auth/me)
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_users ON users")
    op.execute("""
        CREATE POLICY tenant_isolation_users ON users
            USING (
                current_setting('app.current_org_id', true) IS NULL
                OR EXISTS (
                    SELECT 1 FROM memberships m
                    WHERE m.user_id = users.id
                      AND m.org_id = current_setting('app.current_org_id', true)::uuid
                )
            )
            WITH CHECK (true)
    """)

    # ---- raw_records partition ----
    # The parent table has RLS, but the default partition is a separate relation
    # and needs its own RLS to protect rows routed to it.
    op.execute("ALTER TABLE raw_records_default ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE raw_records_default FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_raw_records_default ON raw_records_default")
    op.execute("""
        CREATE POLICY tenant_isolation_raw_records_default ON raw_records_default
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)
    """)

    # ---- Grant table access to app role (post-migration) ----
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON memberships TO signaliq_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON users TO signaliq_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON orgs TO signaliq_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_raw_records_default ON raw_records_default")
    op.execute("ALTER TABLE raw_records_default NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE raw_records_default DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_users ON users")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_orgs ON orgs")
    op.execute("ALTER TABLE orgs DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_memberships ON memberships")
    op.execute("ALTER TABLE memberships DISABLE ROW LEVEL SECURITY")
    # Recreate the original (broken) policy for reversibility
    op.execute("""
        CREATE POLICY tenant_isolation_memberships ON memberships
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
    """)
