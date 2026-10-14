"""Create app role and enforce RLS.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-13
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- Create the limited application role ----
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signaliq_app') THEN
                CREATE ROLE signaliq_app LOGIN PASSWORD 'signaliq_app'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
            END IF;
        END
        $$;
    """)

    # ---- Grant schema access ----
    op.execute("GRANT USAGE ON SCHEMA public TO signaliq_app")

    # ---- Grant table privileges (existing tables) ----
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO signaliq_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO signaliq_app")

    # ---- Default privileges for FUTURE tables created by signaliq ----
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE signaliq IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO signaliq_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE signaliq IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO signaliq_app
    """)

    # ---- Force RLS so even the table owner is subject to policies ----
    op.execute("ALTER TABLE memberships FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE orgs        FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users       FORCE ROW LEVEL SECURITY")

    # ---- Create the RLS policy on memberships (idempotent) ----
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'public'
                  AND tablename = 'memberships'
                  AND policyname = 'tenant_isolation_memberships'
            ) THEN
                CREATE POLICY tenant_isolation_memberships ON memberships
                    USING (org_id = current_setting('app.current_org_id', true)::uuid);
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_memberships ON memberships")
    op.execute("ALTER TABLE memberships NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE orgs        NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users       NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP ROLE IF EXISTS signaliq_app")
