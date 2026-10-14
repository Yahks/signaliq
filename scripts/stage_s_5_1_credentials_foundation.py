"""
SignalIQ — Phase S.5.1: BYOK foundation.

Ships:
  1. Migration 0036 — ai_credentials table
  2. packages/core/credentials.py — encryption + CRUD + rotation helpers

No router integration yet. No API endpoints. No UI.
Just the foundation, verified end-to-end.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "alembic" / "versions" / "0036_ai_credentials.py"
CREDENTIALS = ROOT / "packages" / "core" / "credentials.py"


def step1_check_cryptography() -> None:
    print("\n[1/5] Check cryptography is installed")
    r = subprocess.run(
        ["python", "-c", "import cryptography; print(cryptography.__version__)"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print("  ⚠ cryptography not installed — installing")
        r2 = subprocess.run(
            ["pip", "install", "cryptography"],
            capture_output=True, text=True,
        )
        if r2.returncode != 0:
            raise SystemExit(f"❌ pip install failed:\n{r2.stderr[-500:]}")
        print("  ✓ installed")
    else:
        print(f"  ✓ cryptography {r.stdout.strip()}")


MIGRATION_SRC = '''"""AI credentials — BYOK (Bring Your Own Key).

Each org stores its own LLM provider API keys. The router reads from
this table instead of the global .env, so every user pays for their
own LLM usage.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-20
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("api_key_enc", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("last_429_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exhausted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("org_id", "label", name="uq_ai_credentials_org_label"),
    )
    op.create_index(
        "ix_ai_credentials_org_role",
        "ai_credentials",
        ["org_id", "role", "is_active", "priority"],
    )

    op.execute("ALTER TABLE ai_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_credentials FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_ai_credentials ON ai_credentials
            USING (
                NULLIF(current_setting('app.current_org_id', true), '') IS NULL
                OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_ai_credentials ON ai_credentials")
    op.drop_index("ix_ai_credentials_org_role", table_name="ai_credentials")
    op.drop_table("ai_credentials")
'''


def step2_write_migration() -> None:
    print("\n[2/5] Write migration 0036")
    if MIGRATION.exists():
        print(f"  ↷ {MIGRATION.name} already exists")
        return
    MIGRATION.write_text(MIGRATION_SRC)
    print(f"  ✓ wrote {MIGRATION.name}")


CREDENTIALS_SRC = '''"""AI provider credentials — BYOK.

Each org stores its own LLM provider API keys. The router reads the
current org's credentials and calls the provider with the org's key,
not a global .env key.

Encryption:
  API keys are encrypted at rest with Fernet. The Fernet key is
  derived from `settings.secret_key` via SHA256. Rotating SECRET_KEY
  later means re-encrypting all stored credentials — a known cost.

Rotation:
  Multiple credentials per (org, role) are tried in priority order.
  When a provider returns 429, the router calls mark_exhausted() with
  a retry_after window; get_next_credential() skips exhausted keys
  until the window passes.

Cache:
  No caching. Credential reads are cheap (small table, indexed).
"""
from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import get_settings
from packages.core.logging import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Encryption
# ─────────────────────────────────────────────────────────────────────

_fernet_cache: Fernet | None = None


def _fernet() -> Fernet:
    global _fernet_cache
    if _fernet_cache is None:
        s = get_settings()
        raw = hashlib.sha256(s.secret_key.encode("utf-8")).digest()
        _fernet_cache = Fernet(base64.urlsafe_b64encode(raw))
    return _fernet_cache


def encrypt_key(plain: str) -> str:
    """Encrypt an API key for storage. Returns a URL-safe base64 string."""
    if not plain:
        raise ValueError("API key cannot be empty")
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_key(enc: str) -> str:
    """Decrypt a stored API key. Raises on tampering or wrong key."""
    return _fernet().decrypt(enc.encode("ascii")).decode("utf-8")


def mask_key(plain: str) -> str:
    """Return a mask like '****xyz4' for display. Never reveals the key."""
    if not plain or len(plain) < 8:
        return "****"
    return f"****{plain[-4:]}"


# ─────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────

async def list_credentials(
    session: AsyncSession,
    org_id: str,
    *,
    role: str | None = None,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """List credentials for an org. Never returns the plaintext key.

    Returns rows with `key_preview` (masked) instead of `api_key_enc`.
    """
    where = ["org_id = :oid"]
    params: dict[str, Any] = {"oid": org_id}
    if role:
        where.append("role = :role")
        params["role"] = role
    if not include_inactive:
        where.append("is_active = true")

    rows = (await session.execute(sql_text(f"""
        SELECT id::text, provider, model_name, role, label,
               api_key_enc, is_active, priority,
               exhausted_until, last_success_at, last_429_at,
               created_at, updated_at
        FROM ai_credentials
        WHERE {' AND '.join(where)}
        ORDER BY priority ASC, created_at ASC
    """), params)).all()

    out = []
    for r in rows:
        try:
            preview = mask_key(decrypt_key(r[5]))
        except Exception:
            preview = "****(invalid)"
        out.append({
            "id": r[0], "provider": r[1], "model_name": r[2], "role": r[3],
            "label": r[4], "key_preview": preview, "is_active": bool(r[6]),
            "priority": int(r[7]),
            "exhausted_until": r[8], "last_success_at": r[9], "last_429_at": r[10],
            "created_at": r[11], "updated_at": r[12],
        })
    return out


async def add_credential(
    session: AsyncSession,
    org_id: str,
    *,
    provider: str,
    model_name: str,
    role: str,
    label: str,
    api_key: str,
    priority: int = 100,
) -> dict[str, Any]:
    """Insert a new credential. Encrypts the key at rest.

    Raises ValueError on invalid input, or on unique (org, label) conflict.
    """
    provider = provider.strip().lower()
    model_name = model_name.strip()
    role = role.strip().lower()
    label = label.strip()
    api_key = api_key.strip()

    if not provider or not model_name or not role or not label or not api_key:
        raise ValueError("provider, model_name, role, label, api_key are all required")
    if role not in ("fast", "reasoning", "embedding"):
        raise ValueError(f"Invalid role: {role}")
    if len(label) < 2 or len(label) > 64:
        raise ValueError("Label must be 2-64 characters")
    if len(api_key) < 8:
        raise ValueError("API key looks too short")

    enc = encrypt_key(api_key)

    row = (await session.execute(sql_text("""
        INSERT INTO ai_credentials
            (org_id, provider, model_name, role, label, api_key_enc, priority)
        VALUES
            (:oid, :prov, :model, :role, :label, :enc, :prio)
        RETURNING id::text, created_at
    """), {
        "oid": org_id, "prov": provider, "model": model_name,
        "role": role, "label": label, "enc": enc, "prio": priority,
    })).first()

    await session.commit()
    log.info("credentials.added", org_id=org_id[:8], provider=provider,
             role=role, label=label)
    return {
        "id": row[0], "provider": provider, "model_name": model_name,
        "role": role, "label": label, "key_preview": mask_key(api_key),
        "is_active": True, "priority": priority,
        "created_at": row[1],
    }


async def delete_credential(
    session: AsyncSession, org_id: str, cred_id: str,
) -> bool:
    """Delete a credential. Returns True if a row was removed."""
    r = await session.execute(sql_text("""
        DELETE FROM ai_credentials
        WHERE id = :cid AND org_id = :oid
    """), {"cid": cred_id, "oid": org_id})
    await session.commit()
    return (r.rowcount or 0) > 0


async def toggle_credential(
    session: AsyncSession, org_id: str, cred_id: str, is_active: bool,
) -> bool:
    """Enable/disable a credential without deleting it."""
    r = await session.execute(sql_text("""
        UPDATE ai_credentials
        SET is_active = :a, updated_at = now()
        WHERE id = :cid AND org_id = :oid
    """), {"cid": cred_id, "oid": org_id, "a": is_active})
    await session.commit()
    return (r.rowcount or 0) > 0


# ─────────────────────────────────────────────────────────────────────
# Rotation helpers (used by the router)
# ─────────────────────────────────────────────────────────────────────

async def get_next_credential(
    session: AsyncSession, org_id: str, role: str,
) -> dict[str, Any] | None:
    """Return the highest-priority active credential that isn't exhausted.

    Returns None if no usable credential exists.
    """
    now = datetime.now(UTC)
    row = (await session.execute(sql_text("""
        SELECT id::text, provider, model_name, api_key_enc, priority
        FROM ai_credentials
        WHERE org_id = :oid
          AND role = :role
          AND is_active = true
          AND (exhausted_until IS NULL OR exhausted_until <= :now)
        ORDER BY priority ASC, created_at ASC
        LIMIT 1
    """), {"oid": org_id, "role": role, "now": now})).first()

    if not row:
        return None
    try:
        plain = decrypt_key(row[3])
    except Exception as exc:
        log.warning("credentials.decrypt_failed", cred_id=row[0][:8],
                    error=str(exc)[:120])
        return None
    return {
        "id": row[0], "provider": row[1], "model_name": row[2],
        "api_key": plain, "priority": int(row[4]),
    }


async def mark_exhausted(
    session: AsyncSession, cred_id: str, seconds: int,
) -> None:
    """Mark a credential as rate-limited for `seconds`."""
    now = datetime.now(UTC)
    until = now + timedelta(seconds=seconds)
    await session.execute(sql_text("""
        UPDATE ai_credentials
        SET last_429_at = :now,
            exhausted_until = :until,
            updated_at = now()
        WHERE id = :cid
    """), {"cid": cred_id, "now": now, "until": until})
    await session.commit()
    log.info("credentials.marked_exhausted",
             cred_id=cred_id[:8], seconds=seconds)


async def mark_success(session: AsyncSession, cred_id: str) -> None:
    """Record a successful call for a credential."""
    await session.execute(sql_text("""
        UPDATE ai_credentials
        SET last_success_at = now(),
            exhausted_until = NULL,
            updated_at = now()
        WHERE id = :cid
    """), {"cid": cred_id})
    await session.commit()
'''


def step3_write_module() -> None:
    print("\n[3/5] Write packages/core/credentials.py")
    CREDENTIALS.write_text(CREDENTIALS_SRC)
    print(f"  ✓ wrote {CREDENTIALS.relative_to(ROOT)}")


def step4_migrate() -> None:
    print("\n[4/5] Apply migration 0036")
    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"❌ alembic failed:\n{r.stderr}")
    print("  ✓ alembic upgrade head")


def step5_verify() -> bool:
    print("\n[5/5] Verify encrypt/decrypt + CRUD roundtrip")

    r = subprocess.run([
        "python", "-c", """
import asyncio, sys
sys.path.insert(0, '.')
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.core.credentials import (
    encrypt_key, decrypt_key, mask_key,
    list_credentials, add_credential, delete_credential,
    get_next_credential, mark_exhausted, mark_success,
)

async def main():
    # 1. Encrypt/decrypt roundtrip
    plain = "gsk_test_abcdefghijklmnop1234567890"
    enc = encrypt_key(plain)
    dec = decrypt_key(enc)
    assert dec == plain, "roundtrip failed"
    print(f"  ✓ encrypt/decrypt roundtrip")
    print(f"    plaintext: {plain}")
    print(f"    encrypted: {enc[:40]}...")
    print(f"    masked:    {mask_key(plain)}")

    # 2. Find demo org
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        row = (await s.execute(text(
            "SELECT o.id::text FROM orgs o JOIN memberships m ON m.org_id = o.id "
            "JOIN users u ON u.id = m.user_id WHERE u.username = 'demo' LIMIT 1"
        ))).first()
    org_id = row[0]
    print(f"  org_id: {org_id[:8]}…")

    # 3. Insert test credential
    async with factory() as s:
        await s.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await s.commit()

        existing = await list_credentials(s, org_id, role='reasoning')
        for e in existing:
            if e['label'] == 'test-groq-key':
                await delete_credential(s, org_id, e['id'])
                print(f"  ↷ removed prior test credential")

        cred = await add_credential(
            s, org_id,
            provider='groq',
            model_name='openai/gpt-oss-120b',
            role='reasoning',
            label='test-groq-key',
            api_key=plain,
        )
        print(f"  ✓ added credential: {cred['id'][:8]}… label={cred['label']}")

    # 4. List
    async with factory() as s:
        await s.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await s.commit()
        items = await list_credentials(s, org_id, role='reasoning')
        found = [x for x in items if x['label'] == 'test-groq-key']
        assert found, "list didn't find test credential"
        print(f"  ✓ listed: {found[0]['key_preview']}")

    # 5. get_next_credential
    async with factory() as s:
        await s.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await s.commit()
        nxt = await get_next_credential(s, org_id, 'reasoning')
        assert nxt, "get_next_credential returned None"
        assert nxt['api_key'] == plain, "decrypted key mismatch"
        print(f"  ✓ get_next_credential: provider={nxt['provider']} model={nxt['model_name']}")

    # 6. mark_exhausted + verify skip
    async with factory() as s:
        await s.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await s.commit()
        await mark_exhausted(s, nxt['id'], seconds=60)
        after = await get_next_credential(s, org_id, 'reasoning')
        assert after is None or after['id'] != nxt['id'], \
            "exhausted credential still returned"
        print(f"  ✓ mark_exhausted works — next call returns "
              f"{'None' if after is None else 'different credential'}")

    # 7. mark_success clears exhaustion
    async with factory() as s:
        await s.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await s.commit()
        await mark_success(s, nxt['id'])
        after = await get_next_credential(s, org_id, 'reasoning')
        assert after and after['id'] == nxt['id'], "mark_success didn't clear"
        print(f"  ✓ mark_success clears exhaustion")

    # 8. Cleanup
    async with factory() as s:
        await s.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await s.commit()
        ok = await delete_credential(s, org_id, nxt['id'])
        assert ok, "delete returned False"
        print(f"  ✓ deleted test credential")

    await engine.dispose()
    print()
    print("  ALL CHECKS PASSED")

asyncio.run(main())
        """.strip()
    ], cwd=ROOT, capture_output=True, text=True, timeout=60)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        return False
    return "ALL CHECKS PASSED" in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.5.1: BYOK foundation")
    print("=" * 70)

    step1_check_cryptography()
    step2_write_migration()
    step3_write_module()
    step4_migrate()
    ok = step5_verify()

    print("=" * 70)
    if ok:
        print("✅ S.5.1 complete — credentials foundation verified")
        print()
        print("Next: S.5.2 — wire the router to read the current org's")
        print("      credentials instead of the global .env keys.")
        sys.exit(0)
    print("❌ S.5.1 failed — see output above")
    sys.exit(1)


if __name__ == "__main__":
    main()
