"""RLS policies: NULLIF guard on current_setting.

S.9.36 — when app.current_org_id is unset (fresh connection from
the pool, or after a session.rollback() reset), current_setting
returns '' not NULL. Casting '' to uuid raises
'invalid input syntax for type uuid: ""'. The RLS policy itself
threw on every query against an RLS table, cascading into
unrelated SQL errors.

The fix: replace the cast

    (current_setting('app.current_org_id', true))::uuid

with the safe form

    (NULLIF(current_setting('app.current_org_id', true), ''))::uuid

inside every existing tenant_isolation_* policy — WITHOUT
changing the rest of the USING expression. The previous version
of this migration recreated policies from a hardcoded pattern,
which broke on tables whose tenant policy isn't `org_id = ...`
(e.g. orgs, users, memberships).

This migration reads each policy from pg_policies, does a
string replacement on the USING expression, and recreates the
policy with the fixed expression.

Revision ID: 0076
Revises: 0075
Create Date: 2026-10-13
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
DO $$
DECLARE
    r RECORD;
    new_qual text;
BEGIN
    FOR r IN
        SELECT schemaname, tablename, policyname, qual
        FROM pg_policies
        WHERE policyname LIKE 'tenant_isolation_%'
          AND schemaname = 'public'
    LOOP
        -- Swap only the cast. Leave the rest of the USING expr
        -- untouched — a policy for orgs might use id, a policy for
        -- chain_follows might use org_id. We don't assume.
        new_qual := replace(
            r.qual,
            '(current_setting(''app.current_org_id''::text, true))::uuid',
            '(NULLIF(current_setting(''app.current_org_id''::text, true), ''''::text))::uuid'
        );
        -- Also handle the variant without outer parens, in case
        -- a policy was written slightly differently.
        new_qual := replace(
            new_qual,
            'current_setting(''app.current_org_id''::text, true)::uuid',
            'NULLIF(current_setting(''app.current_org_id''::text, true), ''''::text)::uuid'
        );
        IF new_qual = r.qual THEN
            -- Already NULLIF or nothing to replace.
            CONTINUE;
        END IF;
        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON %I.%I',
            r.policyname, r.schemaname, r.tablename
        );
        EXECUTE format(
            'CREATE POLICY %I ON %I.%I USING (%s)',
            r.policyname, r.schemaname, r.tablename, new_qual
        );
    END LOOP;
END $$;
"""


DOWNGRADE_SQL = """
DO $$
DECLARE
    r RECORD;
    new_qual text;
BEGIN
    FOR r IN
        SELECT schemaname, tablename, policyname, qual
        FROM pg_policies
        WHERE policyname LIKE 'tenant_isolation_%'
          AND schemaname = 'public'
    LOOP
        new_qual := replace(
            r.qual,
            'NULLIF(current_setting(''app.current_org_id''::text, true), ''''::text)',
            'current_setting(''app.current_org_id''::text, true)'
        );
        IF new_qual = r.qual THEN
            CONTINUE;
        END IF;
        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON %I.%I',
            r.policyname, r.schemaname, r.tablename
        );
        EXECUTE format(
            'CREATE POLICY %I ON %I.%I USING (%s)',
            r.policyname, r.schemaname, r.tablename, new_qual
        );
    END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
