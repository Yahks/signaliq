"""S.6.4.1a — migration 0042: ai_credentials.role → roles[].

Single-purpose commit. Only the schema changes. No code, no API, no
frontend. After this commit, the code still reads `role` (which no
longer exists) — that's expected; the next commit is S.6.4.1b which
migrates the service layer.

The org's data is small (4 rows across 2 orgs) so the migration is
straightforward:
  1. Add `roles text[]`
  2. Backfill: `roles = ARRAY[role]`
  3. NOT NULL on `roles`
  4. Drop `role`
  5. GIN index on `roles` for the `:role = ANY(roles)` lookup

Verified by inspecting the resulting schema and confirming the data
survived.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "alembic" / "versions" / "0042_credentials_roles_array.py"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


MIGRATION_SRC = '''"""ai_credentials: role → roles[].

Before: one row per (provider, role) pair. Adding a second Groq key
overwrote the first because the label was deterministic
(`{provider}-{role}`) and upsert used `(org_id, label)` as the
uniqueness key.

After: one row per key. Roles it serves are an array. Adding multiple
keys of the same provider now augments the rotation pool.

Data migration:
  - every existing row's single role becomes a 1-element array
  - no rows are merged (verified: no two rows share an encrypted key)
  - the `role` column is dropped after backfill

Revision ID: 0042
Revises: 0041
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_credentials",
        sa.Column("roles", sa.ARRAY(sa.Text()), nullable=True),
    )
    op.execute("UPDATE ai_credentials SET roles = ARRAY[role]")
    op.execute("ALTER TABLE ai_credentials ALTER COLUMN roles SET NOT NULL")
    op.drop_column("ai_credentials", "role")
    op.execute(
        "CREATE INDEX ix_ai_credentials_roles "
        "ON ai_credentials USING gin (roles)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ai_credentials_roles")
    op.add_column(
        "ai_credentials",
        sa.Column("role", sa.String(16), nullable=True),
    )
    op.execute("UPDATE ai_credentials SET role = roles[1]")
    op.execute("ALTER TABLE ai_credentials ALTER COLUMN role SET NOT NULL")
    op.drop_column("ai_credentials", "roles")
'''


def main() -> None:
    print("=" * 70)
    print("S.6.4.1a — migration 0042: role → roles[]")
    print("=" * 70)

    # 1. Write the migration
    print("\n[1/4] write the migration file")
    if MIGRATION.exists():
        print(f"  ↷ {MIGRATION.name} already exists")
    else:
        MIGRATION.write_text(MIGRATION_SRC)
        print(f"  ✓ wrote {MIGRATION.name}")

    # 2. Show the pre-migration data (evidence for the record)
    print("\n[2/4] pre-migration data")
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        """
        SELECT org_id, provider, role, label,
               LEFT(api_key_enc, 16) AS enc_short
        FROM ai_credentials
        ORDER BY org_id, provider, label
        """
    ], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"psql read failed:\n{r.stderr}")
    print(r.stdout)

    # 3. Run the migration
    print("[3/4] alembic upgrade head")
    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        fail(f"alembic failed:\n{r.stderr[-800:]}")
    # Show the "Running upgrade" line if present
    for line in (r.stderr or "").splitlines():
        if "Running upgrade" in line:
            print(f"  {line.strip()}")
    print("  ✓ migration applied")

    # 4. Verify the schema and the data
    print("\n[4/4] verify the schema and the migrated data")

    # 4a. columns
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-t", "-c",
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'ai_credentials' "
        "AND column_name IN ('role', 'roles') "
        "ORDER BY column_name"
    ], capture_output=True, text=True)
    cols = [c.strip() for c in r.stdout.splitlines() if c.strip()]
    print(f"  columns named role/roles: {cols}")
    if "role" in cols:
        fail("`role` column still exists — migration did not drop it")
    if "roles" not in cols:
        fail("`roles` column missing")
    print("  ✓ `roles` exists, `role` dropped")

    # 4b. the index
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-t", "-c",
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename = 'ai_credentials' "
        "AND indexname = 'ix_ai_credentials_roles'"
    ], capture_output=True, text=True)
    if "ix_ai_credentials_roles" not in r.stdout:
        fail("GIN index on roles missing")
    print("  ✓ GIN index present")

    # 4c. the data — every row should have roles = [old_role]
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        """
        SELECT org_id, provider, roles, label,
               LEFT(api_key_enc, 16) AS enc_short
        FROM ai_credentials
        ORDER BY org_id, provider, label
        """
    ], capture_output=True, text=True)
    print(r.stdout)

    # 4d. NOT NULL constraint on roles
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-t", "-c",
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'ai_credentials' AND column_name = 'roles'"
    ], capture_output=True, text=True)
    if "NO" not in r.stdout:
        fail(f"`roles` is still nullable: {r.stdout.strip()}")
    print("  ✓ roles is NOT NULL")

    print("=" * 70)
    print("✅ S.6.4.1a complete")
    print()
    print("The code layer still reads `role` — that will fail until S.6.4.1b.")
    print("Do NOT run a chain build between this commit and the next.")
    print("=" * 70)
    sys.exit(0)


if __name__ == "__main__":
    main()
