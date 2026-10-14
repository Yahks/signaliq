"""
SignalIQ — Phase T.4.3: Fix /api/auth/me + defensive RLS.

Root cause: /me runs a query under RLS without setting app.current_org_id.
If a pooled connection has that setting as '' (empty string, from a prior
request), the RLS policy tries to cast ''::uuid and crashes.

Fix A (migration 0027): rewrite users RLS policy with NULLIF(..., '').
Fix B (/me endpoint): use owner session — reading your own user record
doesn't need RLS.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUTH_ROUTER = ROOT / "apps" / "api" / "routers" / "auth.py"
MIGRATION_27 = ROOT / "alembic" / "versions" / "0027_users_rls_nullif.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(f"\n❌ {path.name}: pattern not found for '{label}'")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. Migration 0027 — NULLIF-wrap the users RLS policy
# ─────────────────────────────────────────────────────────────────────

MIGRATION_SRC = '''"""Harden users RLS policy against empty-string app.current_org_id.

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
down_revision: str | None = "0022"
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
'''


def step1_write_migration() -> None:
    print("\n[1/3] Write migration 0027")
    if MIGRATION_27.exists():
        print(f"  ↷ {MIGRATION_27.name} already exists")
        return
    MIGRATION_27.write_text(MIGRATION_SRC)
    print(f"  ✓ wrote {MIGRATION_27.name}")


# ─────────────────────────────────────────────────────────────────────
# 2. Fix /me to use owner session
# ─────────────────────────────────────────────────────────────────────

def step2_fix_me_endpoint() -> None:
    print("\n[2/3] Fix /me endpoint to use owner session")

    # Add import
    _patch_and_verify(
        AUTH_ROUTER,
        "from packages.db.session import get_owner_session_factory, get_session",
        "from packages.db.session import get_owner_session_factory, get_session",
        "import already present",
    )

    # Replace the /me body
    _patch_and_verify(
        AUTH_ROUTER,
        '''@router.get("/me", response_model=MeResponse)
async def me(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token, expected_type="access")
    user_id = payload["sub"]

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")

    org_id = payload.get("org")
    role = payload.get("role")
    org_slug: str | None = None
    if org_id:
        org_result = await session.execute(select(Org).where(Org.id == org_id))
        org = org_result.scalar_one_or_none()
        org_slug = org.slug if org else None

    return MeResponse(
        user=UserResponse.model_validate(user),
        org_id=org_id,
        org_slug=org_slug,
        role=role,
    )''',
        '''@router.get("/me", response_model=MeResponse)
async def me(
    authorization: str | None = Header(default=None),
) -> MeResponse:
    """Return the current user + their org context.

    Uses the OWNER session factory because reading your own user record
    via a valid JWT is not a tenant-scoped operation — it happens before
    (or independently of) any org context. RLS on the `users` table
    requires app.current_org_id to be set, which /me has no reason to do.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token, expected_type="access")
    user_id = payload["sub"]

    factory = get_owner_session_factory()
    async with factory() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise NotFoundError("User not found")

        org_id = payload.get("org")
        role = payload.get("role")
        org_slug: str | None = None
        if org_id:
            org_result = await session.execute(select(Org).where(Org.id == org_id))
            org = org_result.scalar_one_or_none()
            org_slug = org.slug if org else None

    return MeResponse(
        user=UserResponse.model_validate(user),
        org_id=org_id,
        org_slug=org_slug,
        role=role,
    )''',
        "/me uses owner session + bypasses RLS",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Apply migration + restart API + verify
# ─────────────────────────────────────────────────────────────────────

def step3_apply_and_verify() -> bool:
    print("\n[3/3] Apply migration + restart API + verify /me")

    r = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"❌ alembic upgrade failed:\n{r.stderr}")
    print("  ✓ alembic upgrade head")

    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    import time
    time.sleep(2)

    log = Path("/tmp/signaliq-api.log")
    with log.open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(5)

    # Health
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout}")
        return False
    print("  ✓ /health 200")

    # Login + /me
    r = subprocess.run([
        "bash", "-c",
        "TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login "
        "-H 'Content-Type: application/json' "
        "-d '{\"email\":\"elearningyarhaya@gmail.com\",\"password\":\"12345678\"}' "
        "| python -c \"import sys,json; print(json.load(sys.stdin).get('access_token',''))\") && "
        "echo \"token length: ${#TOKEN}\" && "
        "curl -s -X GET http://localhost:8000/api/auth/me "
        "-H \"Authorization: Bearer $TOKEN\""
    ], capture_output=True, text=True, timeout=30)
    print(r.stdout)

    return '"email"' in r.stdout or '"org_id"' in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.4.3: Fix /api/auth/me")
    print("=" * 70)

    step1_write_migration()
    step2_fix_me_endpoint()
    ok = step3_apply_and_verify()

    print("=" * 70)
    if ok:
        print("✅ T.4.3 complete — /me works, RLS policy hardened")
        print()
        print("Reload the browser at http://localhost:3000/login")
        sys.exit(0)
    print("❌ T.4.3 failed — see output above")
    sys.exit(1)


if __name__ == "__main__":
    main()
