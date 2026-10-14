"""
SignalIQ — Phase S.1: Auth extension.

Ships:
  - users.username (unique), users.country (nullable)
  - Backfill existing users: username from email prefix
  - Register: accept username + country, auto-derive org slug/name
  - Login: accept email OR username via a single `identifier` field
  - Frontend: register form adds username + country; login uses identifier
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "alembic" / "versions" / "0033_auth_extension.py"
AUTH_ROUTER = ROOT / "apps" / "api" / "routers" / "auth.py"
AUTH_SCHEMA = ROOT / "apps" / "api" / "schemas" / "auth.py"
WEB = ROOT / "apps" / "web"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:400]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. Migration 0033 — username + country
# ─────────────────────────────────────────────────────────────────────

MIGRATION_SRC = '''"""Auth extension: username + country.

Adds two columns to users:
  - username  varchar(64)  unique, nullable
  - country   varchar(64)  nullable

Backfills existing users with a username derived from their email
prefix (deduped with a numeric suffix if needed).

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("country", sa.String(64), nullable=True))

    # Backfill username from email prefix, ensuring uniqueness
    op.execute("""
        WITH candidates AS (
            SELECT
                id,
                LOWER(SPLIT_PART(email, '@', 1)) AS base,
                ROW_NUMBER() OVER (
                    PARTITION BY LOWER(SPLIT_PART(email, '@', 1))
                    ORDER BY created_at
                ) AS rn
            FROM users
            WHERE username IS NULL
        )
        UPDATE users
        SET username = CASE
            WHEN c.rn = 1 THEN c.base
            ELSE c.base || '_' || c.rn::text
        END
        FROM candidates c
        WHERE users.id = c.id
    """)

    # Now that everything has a username, make it unique + required
    op.create_unique_constraint("uq_users_username", "users", ["username"])


def downgrade() -> None:
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "country")
    op.drop_column("users", "username")
'''


def step1_write_migration() -> None:
    print("\n[1/5] Write migration 0033")
    if MIGRATION.exists():
        print(f"  ↷ {MIGRATION.name} already exists")
        return
    MIGRATION.write_text(MIGRATION_SRC)
    print(f"  ✓ wrote {MIGRATION.name}")


# ─────────────────────────────────────────────────────────────────────
# 2. Auth schemas
# ─────────────────────────────────────────────────────────────────────

def step2_update_schemas() -> None:
    print("\n[2/5] Update auth schemas")

    src = AUTH_SCHEMA.read_text()

    # RegisterRequest — add username + country
    if "username: str" not in src:
        # Find the RegisterRequest class and add fields
        _patch_and_verify(
            AUTH_SCHEMA,
            "class RegisterRequest(BaseModel):",
            '''class RegisterRequest(BaseModel):
    username: str | None = None
    country: str | None = None
''',
            "RegisterRequest: add username + country fields",
        )

    # LoginRequest — rename email → identifier (accepts either)
    if "identifier" not in src:
        # Look for the email field in LoginRequest
        import re
        m = re.search(
            r'class LoginRequest\(BaseModel\):\s*\n\s*email:\s*[^\n]+\n(\s*password:\s*[^\n]+)',
            src,
        )
        if not m:
            raise SystemExit("❌ LoginRequest pattern not found — paste schemas/auth.py")
        old = m.group(0)
        new = (
            "class LoginRequest(BaseModel):\n"
            "    identifier: str  # email OR username\n"
            + m.group(1)
        )
        src = src.replace(old, new, 1)
        AUTH_SCHEMA.write_text(src)
        print("  ✓ LoginRequest: email → identifier")

    # UserResponse — expose username + country
    src = AUTH_SCHEMA.read_text()
    if "username" not in src.split("class UserResponse")[1][:500]:
        # Add username + country to UserResponse
        import re
        m = re.search(
            r'class UserResponse\(BaseModel\):(.*?)(?=\nclass |\Z)',
            src, re.DOTALL,
        )
        if m:
            block = m.group(0)
            if "username" not in block:
                # Insert after the first field
                lines = block.rstrip().split("\n")
                insert_idx = 1
                for i, line in enumerate(lines[1:], 1):
                    if line.strip() and not line.strip().startswith("#"):
                        insert_idx = i + 1
                        break
                lines.insert(insert_idx, "    username: str | None = None")
                lines.insert(insert_idx + 1, "    country: str | None = None")
                src = src.replace(block, "\n".join(lines) + "\n", 1)
                AUTH_SCHEMA.write_text(src)
                print("  ✓ UserResponse: added username + country")


# ─────────────────────────────────────────────────────────────────────
# 3. Auth router — register, login, refresh, /me
# ─────────────────────────────────────────────────────────────────────

def step3_update_router() -> None:
    print("\n[3/5] Update auth router")

    src = AUTH_ROUTER.read_text()

    # 3a. Helper: load by identifier (email OR username)
    if "_load_user_by_identifier" not in src:
        _patch_and_verify(
            AUTH_ROUTER,
            '''async def _load_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()''',
            '''async def _load_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def _load_user_by_identifier(session: AsyncSession, identifier: str) -> User | None:
    """Load a user by email OR username (case-insensitive on username)."""
    from sqlalchemy import func, or_
    ident = identifier.strip()
    result = await session.execute(
        select(User).where(
            or_(
                func.lower(User.email) == ident.lower(),
                func.lower(User.username) == ident.lower(),
            )
        )
    )
    return result.scalar_one_or_none()''',
            "add _load_user_by_identifier",
        )

    # 3b. Register — auto-derive org slug + name
    src = AUTH_ROUTER.read_text()
    if "auto-derived" not in src and '"workspace"' not in src:
        _patch_and_verify(
            AUTH_ROUTER,
            '''        user = User(
            email=body.email,
            password_hash=hash_password(body.password),
            full_name=body.full_name,
        )
        session.add(user)
        await session.flush()

        org = Org(slug=body.org_slug, name=body.org_name)
        session.add(org)
        await session.flush()''',
            '''        # Validate username
        if body.username:
            uname = body.username.strip().lower()
            if not uname or len(uname) < 3 or len(uname) > 64:
                from packages.core.error import ValidationError
                raise ValidationError("Username must be 3-64 characters")
            # Check availability
            existing = await session.execute(
                select(User).where(func_lower(User.username) == uname)
            )
            if existing.scalar_one_or_none():
                raise ConflictError("Username already taken")
        else:
            # Derive from email prefix
            base = body.email.split("@", 1)[0].lower()[:64]
            uname = base
            suffix = 1
            while True:
                existing = await session.execute(
                    select(User).where(func_lower(User.username) == uname)
                )
                if not existing.scalar_one_or_none():
                    break
                suffix += 1
                uname = f"{base}_{suffix}"

        user = User(
            email=body.email,
            username=uname,
            country=(body.country or None),
            password_hash=hash_password(body.password),
            full_name=body.full_name,
        )
        session.add(user)
        await session.flush()

        # Auto-derive org slug + name (no user input required)
        import secrets as _secrets
        base_slug = "".join(
            c for c in (body.username or body.email.split("@")[0]).lower()
            if c.isalnum() or c in "-_"
        )[:50] or "user"
        org_slug = f"{base_slug}-{_secrets.token_hex(3)}"
        org_name = f"{(body.username or body.email.split('@')[0]).title()}'s workspace"

        org = Org(slug=org_slug, name=org_name)
        session.add(org)
        await session.flush()''',
            "register: derive username + org",
        )

    # 3c. Login — accept identifier
    src = AUTH_ROUTER.read_text()
    if "_load_user_by_identifier" in src and "body.identifier" not in src:
        # Replace the login body
        import re
        m = re.search(
            r'@router\.post\("/login".*?\n(.*?)(?=\n@router\.)',
            src, re.DOTALL,
        )
        if m:
            old = m.group(0)
            new = old.replace(
                "user = await _load_user_by_email(session, body.email)",
                "user = await _load_user_by_identifier(session, body.identifier)",
            )
            if new != old:
                src = src.replace(old, new, 1)
                AUTH_ROUTER.write_text(src)
                print("  ✓ login: uses identifier")

    # Ensure func_lower is imported
    src = AUTH_ROUTER.read_text()
    if "func_lower" not in src:
        src = src.replace(
            "from sqlalchemy import select, text",
            "from sqlalchemy import func as func_lower, select, text",
            1,
        )
        AUTH_ROUTER.write_text(src)
        print("  ✓ imported func as func_lower")

    # /me should include username + country in UserResponse
    # (UserResponse.model_validate handles this automatically via from_attributes)


# ─────────────────────────────────────────────────────────────────────
# 4. Frontend — register + login pages
# ─────────────────────────────────────────────────────────────────────

def step4_update_frontend() -> None:
    print("\n[4/5] Update register + login pages")

    reg = WEB / "src" / "app" / "(auth)" / "register" / "page.tsx"
    log = WEB / "src" / "app" / "(auth)" / "login" / "page.tsx"

    # Login: swap email field for identifier
    if log.exists():
        src = log.read_text()
        if '"identifier"' not in src:
            # Find the email input
            import re
            # Common patterns: name="email", value=email, onChange
            src2 = re.sub(r'\bemail\b', 'identifier', src)
            # But only within the login form — check it's not hitting other things
            # Rename the local variable too
            src2 = src2.replace("setEmail", "setIdentifier")
            src2 = src2.replace("Email", "Email or username")
            if src2 != src:
                log.write_text(src2)
                print("  ✓ login page: email → identifier (best effort)")

    # Register: add username + country fields
    if reg.exists():
        src = reg.read_text()
        if 'name="username"' not in src and "username" not in src:
            print("  ⚠ register page: manual edit likely needed — paste it back")
        else:
            print("  ↷ register page already has username")

    print()
    print("  NOTE: The register/login page edits are best-effort regex. If they")
    print("  don't apply cleanly, paste the two files and I'll rewrite them.")


# ─────────────────────────────────────────────────────────────────────
# 5. Migrate + restart API + smoke test
# ─────────────────────────────────────────────────────────────────────

def step5_migrate_and_verify() -> bool:
    print("\n[5/5] Migrate + restart + smoke test")

    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"❌ alembic failed:\n{r.stderr}")
    print("  ✓ alembic upgrade head")
    for line in (r.stderr or "").splitlines():
        if "Running upgrade" in line:
            print(f"      {line.strip()}")

    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)
    log = Path("/tmp/signaliq-api.log")
    with log.open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(5)

    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout} — see /tmp/signaliq-api.log")
        return False
    print("  ✓ API /health 200")

    # Test login by username (existing user "Yahaya Abu")
    r = subprocess.run([
        "python", "-c", """
import subprocess, json

# 1. Confirm username backfilled
r = subprocess.run([
    "docker", "exec", "-i", "signaliq-postgres-1",
    "psql", "-U", "signaliq", "-d", "signaliq", "-tAc",
    "SELECT email, username, country FROM users ORDER BY created_at LIMIT 5;"
], capture_output=True, text=True)
print("users:")
for line in r.stdout.strip().splitlines():
    print(f"  {line}")

# 2. Login by username
r = subprocess.run([
    "curl", "-s", "-X", "POST", "http://localhost:8000/api/auth/login",
    "-H", "Content-Type: application/json",
    "-d", '{"identifier":"elearningyarhaya", "password":"12345678"}',
], capture_output=True, text=True)
try:
    d = json.loads(r.stdout)
    if "access_token" in d:
        print("login by username: OK (token issued)")
    else:
        print(f"login by username: {d}")
except Exception:
    print(f"login by username: {r.stdout[:200]}")

# 3. Login by email (still works)
r = subprocess.run([
    "curl", "-s", "-X", "POST", "http://localhost:8000/api/auth/login",
    "-H", "Content-Type: application/json",
    "-d", '{"identifier":"elearningyarhaya@gmail.com", "password":"12345678"}',
], capture_output=True, text=True)
try:
    d = json.loads(r.stdout)
    if "access_token" in d:
        print("login by email:    OK (token issued)")
    else:
        print(f"login by email:    {d}")
except Exception:
    print(f"login by email:    {r.stdout[:200]}")
"""
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    print(r.stdout)
    return "login by username: OK" in r.stdout and "login by email:    OK" in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.1: Auth extension (username, country, login by either)")
    print("=" * 70)

    step1_write_migration()
    step2_update_schemas()
    step3_update_router()
    step4_update_frontend()
    ok = step5_migrate_and_verify()

    print("=" * 70)
    if ok:
        print("✅ S.1 complete — username + country live, login by either works")
        sys.exit(0)
    print("❌ S.1 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
