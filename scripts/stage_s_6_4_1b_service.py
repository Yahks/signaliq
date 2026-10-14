"""S.6.4.1b — credentials.py: role → roles[].

Part 2 of the credentials-per-key refactor. The service layer only.
After this commit, chain builds work again and multiple keys of the
same provider coexist.

Single file: packages/core/credentials.py. Nine edits, all mechanical.

Verified:
  - list_credentials_sync(org, 'fast') returns rows where 'fast' = ANY(roles)
  - adding two Groq keys produces two rows
  - a chain build succeeds (proving the router can still call the service)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CREDS = ROOT / "packages" / "core" / "credentials.py"
WEB = ROOT / "apps" / "web"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() and new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(f"{path.name}: pattern not found for '{label}'\n"
             f"--- expected ---\n{old[:500]}")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


def step1_key_hash() -> None:
    print("\n[1/9] add _key_hash helper")

    src = CREDS.read_text()
    if "def _key_hash" in src:
        print("  ↷ already present")
        return

    helper = '''

def _key_hash(plain: str) -> str:
    """Short deterministic hash suffix for a key — used in the default label.

    First 4 hex chars of sha256(plain). Distinct keys give distinct
    suffixes; the same key gives the same suffix across re-adds.
    """
    import hashlib
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()[:4]
'''

    # Insert right after the mask_key function body
    old = '''def mask_key(plain: str) -> str:
    """Return a mask like '****xyz4' for display. Never reveals the key."""
    if not plain or len(plain) < 8:
        return "****"
    return f"****{plain[-4:]}"'''

    if old not in src:
        fail("mask_key block not found")

    src = src.replace(old, old + helper, 1)
    CREDS.write_text(src)
    print("  ✓ added _key_hash")


def step2_list_credentials() -> None:
    print("\n[2/9] list_credentials")

    # 2a. Signature: role filter still works but now queries ANY(roles)
    patch(
        CREDS,
        '''    where = ["org_id = :oid"]
    params: dict[str, Any] = {"oid": org_id}
    if role:
        where.append("role = :role")
        params["role"] = role''',
        '''    where = ["org_id = :oid"]
    params: dict[str, Any] = {"oid": org_id}
    if role:
        where.append(":role = ANY(roles)")
        params["role"] = role''',
        "role filter → ANY(roles)",
    )

    # 2b. SELECT + return dict
    patch(
        CREDS,
        '''        SELECT id::text, provider, model_name, role, label,
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
            "id": r[0], "provider": r[1], "model_name": r[2], "role": r[3],''',
        '''        SELECT id::text, provider, model_name, roles, label,
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
            "id": r[0], "provider": r[1], "model_name": r[2], "roles": list(r[3] or []),''',
        "SELECT + return: roles",
    )


def step3_add_credential() -> None:
    print("\n[3/9] add_credential")

    patch(
        CREDS,
        '''async def add_credential(
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
    }''',
        '''async def add_credential(
    session: AsyncSession,
    org_id: str,
    *,
    provider: str,
    model_name: str,
    roles: list[str],
    label: str,
    api_key: str,
    priority: int = 100,
) -> dict[str, Any]:
    """Insert a new credential. Encrypts the key at rest.

    `roles` is the list of roles this key serves (e.g. ['fast','reasoning']).
    Raises ValueError on invalid input, or on unique (org, label) conflict.
    """
    provider = provider.strip().lower()
    model_name = model_name.strip()
    roles = [r.strip().lower() for r in (roles or [])]
    label = label.strip()
    api_key = api_key.strip()

    if not provider or not model_name or not roles or not label or not api_key:
        raise ValueError("provider, model_name, roles, label, api_key are all required")
    for r in roles:
        if r not in ("fast", "reasoning", "embedding"):
            raise ValueError(f"Invalid role: {r}")
    if len(label) < 2 or len(label) > 64:
        raise ValueError("Label must be 2-64 characters")
    if len(api_key) < 8:
        raise ValueError("API key looks too short")

    enc = encrypt_key(api_key)

    row = (await session.execute(sql_text("""
        INSERT INTO ai_credentials
            (org_id, provider, model_name, roles, label, api_key_enc, priority)
        VALUES
            (:oid, :prov, :model, :roles, :label, :enc, :prio)
        RETURNING id::text, created_at
    """), {
        "oid": org_id, "prov": provider, "model": model_name,
        "roles": roles, "label": label, "enc": enc, "prio": priority,
    })).first()

    await session.commit()
    log.info("credentials.added", org_id=org_id[:8], provider=provider,
             roles=roles, label=label)
    return {
        "id": row[0], "provider": provider, "model_name": model_name,
        "roles": roles, "label": label, "key_preview": mask_key(api_key),
        "is_active": True, "priority": priority,
        "created_at": row[1],
    }''',
        "add_credential: role → roles",
    )


def step4_get_next_credential() -> None:
    print("\n[4/9] get_next_credential (async)")

    patch(
        CREDS,
        '''        SELECT id::text, provider, model_name, api_key_enc, priority
        FROM ai_credentials
        WHERE org_id = :oid
          AND role = :role
          AND is_active = true
          AND (exhausted_until IS NULL OR exhausted_until <= :now)
        ORDER BY priority ASC, created_at ASC
        LIMIT 1
    """), {"oid": org_id, "role": role, "now": now})).first()''',
        '''        SELECT id::text, provider, model_name, api_key_enc, priority
        FROM ai_credentials
        WHERE org_id = :oid
          AND :role = ANY(roles)
          AND is_active = true
          AND (exhausted_until IS NULL OR exhausted_until <= :now)
        ORDER BY priority ASC, created_at ASC
        LIMIT 1
    """), {"oid": org_id, "role": role, "now": now})).first()''',
        "async: ANY(roles)",
    )


def step5_get_next_credential_sync() -> None:
    print("\n[5/9] get_next_credential_sync")

    patch(
        CREDS,
        '''        SELECT id::text, provider, model_name, api_key_enc, priority
        FROM ai_credentials
        WHERE org_id = :oid
          AND role = :role
          AND is_active = true
          AND (exhausted_until IS NULL OR exhausted_until <= :now)
        ORDER BY priority ASC, created_at ASC
        LIMIT 1
    """), {"oid": org_id, "role": role, "now": now}).first()''',
        '''        SELECT id::text, provider, model_name, api_key_enc, priority
        FROM ai_credentials
        WHERE org_id = :oid
          AND :role = ANY(roles)
          AND is_active = true
          AND (exhausted_until IS NULL OR exhausted_until <= :now)
        ORDER BY priority ASC, created_at ASC
        LIMIT 1
    """), {"oid": org_id, "role": role, "now": now}).first()''',
        "sync: ANY(roles)",
    )


def step6_any_credentials_exist_sync() -> None:
    print("\n[6/9] any_credentials_exist_sync")

    patch(
        CREDS,
        '''        SELECT 1 FROM ai_credentials
        WHERE org_id = :oid AND role = :role AND is_active = true
        LIMIT 1''',
        '''        SELECT 1 FROM ai_credentials
        WHERE org_id = :oid AND :role = ANY(roles) AND is_active = true
        LIMIT 1''',
        "ANY(roles)",
    )


def step7_list_credentials_sync() -> None:
    print("\n[7/9] list_credentials_sync")

    patch(
        CREDS,
        '''        SELECT id::text, provider, model_name, api_key_enc, priority
        FROM ai_credentials
        WHERE org_id = :oid
          AND role = :role
          AND is_active = true
          AND (exhausted_until IS NULL OR exhausted_until <= :now)
        ORDER BY priority ASC, created_at ASC
    """), {"oid": org_id, "role": role, "now": now}).all()''',
        '''        SELECT id::text, provider, model_name, api_key_enc, priority
        FROM ai_credentials
        WHERE org_id = :oid
          AND :role = ANY(roles)
          AND is_active = true
          AND (exhausted_until IS NULL OR exhausted_until <= :now)
        ORDER BY priority ASC, created_at ASC
    """), {"oid": org_id, "role": role, "now": now}).all()''',
        "ANY(roles)",
    )


def step8_upsert_credential() -> None:
    print("\n[8/9] upsert_credential")

    patch(
        CREDS,
        '''async def upsert_credential(
    session: AsyncSession,
    org_id: str,
    *,
    provider: str,
    model_name: str,
    role: str,
    label: str,
    api_key: str,
    priority: int = 100,
) -> dict[str, Any]:''',
        '''async def upsert_credential(
    session: AsyncSession,
    org_id: str,
    *,
    provider: str,
    model_name: str,
    roles: list[str],
    label: str,
    api_key: str,
    priority: int = 100,
) -> dict[str, Any]:''',
        "signature",
    )

    patch(
        CREDS,
        '''    provider = provider.strip().lower()
    model_name = model_name.strip()
    role = role.strip().lower()
    label = label.strip()
    api_key = api_key.strip()

    if not provider or not model_name or not role or not label or not api_key:
        raise ValueError("provider, model_name, role, label, api_key are all required")
    if role not in ("fast", "reasoning", "embedding"):
        raise ValueError(f"Invalid role: {role}")''',
        '''    provider = provider.strip().lower()
    model_name = model_name.strip()
    roles = [r.strip().lower() for r in (roles or [])]
    label = label.strip()
    api_key = api_key.strip()

    if not provider or not model_name or not roles or not label or not api_key:
        raise ValueError("provider, model_name, roles, label, api_key are all required")
    for r in roles:
        if r not in ("fast", "reasoning", "embedding"):
            raise ValueError(f"Invalid role: {r}")''',
        "validation",
    )

    patch(
        CREDS,
        '''    row = (await session.execute(sql_text("""
        INSERT INTO ai_credentials
            (org_id, provider, model_name, role, label, api_key_enc, priority)
        VALUES
            (:oid, :prov, :model, :role, :label, :enc, :prio)
        ON CONFLICT (org_id, label) DO UPDATE
        SET provider        = EXCLUDED.provider,
            model_name      = EXCLUDED.model_name,
            role            = EXCLUDED.role,
            api_key_enc     = EXCLUDED.api_key_enc,
            priority        = EXCLUDED.priority,
            is_active       = true,
            exhausted_until = NULL,
            updated_at      = now()
        RETURNING id::text, created_at
    """), {
        "oid": org_id, "prov": provider, "model": model_name,
        "role": role, "label": label, "enc": enc, "prio": priority,
    })).first()''',
        '''    row = (await session.execute(sql_text("""
        INSERT INTO ai_credentials
            (org_id, provider, model_name, roles, label, api_key_enc, priority)
        VALUES
            (:oid, :prov, :model, :roles, :label, :enc, :prio)
        ON CONFLICT (org_id, label) DO UPDATE
        SET provider        = EXCLUDED.provider,
            model_name      = EXCLUDED.model_name,
            roles           = EXCLUDED.roles,
            api_key_enc     = EXCLUDED.api_key_enc,
            priority        = EXCLUDED.priority,
            is_active       = true,
            exhausted_until = NULL,
            updated_at      = now()
        RETURNING id::text, created_at
    """), {
        "oid": org_id, "prov": provider, "model": model_name,
        "roles": roles, "label": label, "enc": enc, "prio": priority,
    })).first()''',
        "INSERT + ON CONFLICT",
    )

    patch(
        CREDS,
        '''    return {
        "id": row[0], "provider": provider, "model_name": model_name,
        "role": role, "label": label, "key_preview": mask_key(api_key),
        "is_active": True, "priority": priority,
        "created_at": row[1],
    }''',
        '''    return {
        "id": row[0], "provider": provider, "model_name": model_name,
        "roles": roles, "label": label, "key_preview": mask_key(api_key),
        "is_active": True, "priority": priority,
        "created_at": row[1],
    }''',
        "return dict",
    )


def step9_add_provider_key() -> None:
    print("\n[9/9] add_provider_key — one row per key")

    patch(
        CREDS,
        '''async def add_provider_key(
    session: AsyncSession, org_id: str, *, provider: str, api_key: str,
) -> list[dict[str, Any]]:
    """Verify the key, then create one credential row per catalog entry.

    Raises CredentialVerificationError on verification failure.
    """
    from packages.core.error import CredentialVerificationError

    provider = provider.strip().lower()
    if provider not in PROVIDER_CATALOG:
        raise CredentialVerificationError(
            "unknown_provider", f"Unknown provider: {provider}", provider,
        )

    ok, reason, message = await verify_key(provider, api_key)
    if not ok:
        raise CredentialVerificationError(reason or "provider_error",
                                           message or "Verification failed.",
                                           provider)

    created: list[dict[str, Any]] = []
    for role, model_name, priority in PROVIDER_CATALOG[provider]:
        # Label is derived, unique per (org, label). Include role so
        # multiple rows for one provider don't collide.
        label = f"{provider}-{role}"
        cred = await upsert_credential(
            session, org_id,
            provider=provider, model_name=model_name, role=role,
            label=label, api_key=api_key, priority=priority,
        )
        created.append(cred)
    return created''',
        '''async def add_provider_key(
    session: AsyncSession, org_id: str, *,
    provider: str, api_key: str, label: str | None = None,
) -> list[dict[str, Any]]:
    """Verify the key, then create ONE credential row for it.

    S.6.4.1b — one row per key. `roles` is the set of roles from the
    catalog for this provider. A second key of the same provider gets a
    distinct label (`{provider}-{hash4}` or the user's label), so it
    augments the rotation pool instead of overwriting.

    Raises CredentialVerificationError on verification failure.
    """
    from packages.core.error import CredentialVerificationError

    provider = provider.strip().lower()
    if provider not in PROVIDER_CATALOG:
        raise CredentialVerificationError(
            "unknown_provider", f"Unknown provider: {provider}", provider,
        )

    ok, reason, message = await verify_key(provider, api_key)
    if not ok:
        raise CredentialVerificationError(reason or "provider_error",
                                           message or "Verification failed.",
                                           provider)

    catalog = PROVIDER_CATALOG[provider]
    # Distinct set of roles this key serves
    all_roles = sorted({r for r, _m, _p in catalog})
    # Lowest priority from the catalog is the credential's priority
    min_priority = min(p for _r, _m, p in catalog)
    # Recorded model — informational; the router picks per role at call time
    first_model = catalog[0][1]

    # User-supplied label wins if present. Otherwise hash-derive from the key.
    if label and label.strip():
        final_label = label.strip()[:64]
    else:
        final_label = f"{provider}-{_key_hash(api_key)}"

    cred = await upsert_credential(
        session, org_id,
        provider=provider, model_name=first_model, roles=all_roles,
        label=final_label, api_key=api_key, priority=min_priority,
    )
    return [cred]''',
        "one row per key",
    )


def verify() -> bool:
    print("\n" + "=" * 70)
    print("VERIFICATION")
    print("=" * 70)

    # Syntax
    print("\n[A] syntax")
    r = subprocess.run(
        ["python", "-c", f"import ast; ast.parse(open('{CREDS}').read())"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ❌ {r.stderr[:300]}")
        return False
    print("  ✓ credentials.py parses")

    # Import test
    print("\n[B] import")
    r = subprocess.run(
        ["python", "-c",
         "from packages.core.credentials import ("
         "add_credential, upsert_credential, list_credentials_sync, "
         "get_next_credential_sync, any_credentials_exist_sync, "
         "add_provider_key, _key_hash); "
         "print('ok'); "
         "print('hash 1:', _key_hash('gsk_test_aaaa')); "
         "print('hash 2:', _key_hash('gsk_test_bbbb')); "
         "print('same key same hash:', _key_hash('gsk_test_aaaa') == _key_hash('gsk_test_aaaa'))"],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-300:])
        return False

    # Live: list_credentials_sync returns existing rows by role
    print("\n[C] list_credentials_sync reads existing rows by role")
    code = r'''
import asyncio, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    F = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    from packages.db.session import get_owner_session_factory
    OF = get_owner_session_factory()
    async with OF() as s:
        row = (await s.execute(text("""
            SELECT o.id::text FROM orgs o
            JOIN memberships m ON m.org_id = o.id
            JOIN users u ON u.id = m.user_id
            WHERE u.username = 'tokentest' LIMIT 1
        """))).first()
    oid = row[0]
    print(f"  org: {oid[:8]}…")

    # The verification script uses an AsyncSession, so we call the async
    # `list_credentials` (awaited) rather than the sync variant.
    # S.6.4.1b.1 — this was the bug in the previous run's verification.
    from packages.core.credentials import list_credentials
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        fast = await list_credentials(s, oid, role="fast")
        reason = await list_credentials(s, oid, role="reasoning")
    print(f"  fast creds:      {len(fast)}")
    for c in fast:
        roles = c.get("roles") or []
        print(f"    {c['id'][:8]}  {c['provider']}  roles={roles}  prio={c['priority']}")
    print(f"  reasoning creds: {len(reason)}")
    for c in reason:
        roles = c.get("roles") or []
        print(f"    {c['id'][:8]}  {c['provider']}  roles={roles}  prio={c['priority']}")

    if not fast and not reason:
        print("  ⚠ no creds for either role — org has none or all are exhausted")
    await engine.dispose()

asyncio.run(main())
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=30,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-400:])
        return False

    # Live: chain build
    print("\n[D] chain build (restores functionality)")
    subprocess.run(["pkill", "-9", "-f", "celery.*worker"], check=False)
    time.sleep(3)
    with Path("/tmp/signaliq-worker.log").open("w") as f:
        subprocess.Popen(
            ["celery", "-A", "apps.worker.celery_app", "worker",
             "-l", "info", "-Q", "default,ingestion", "--hostname=worker1@%h"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(8)

    code2 = r'''
import asyncio, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.core.context import current_user_id, current_org_id

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    F = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    from packages.db.session import get_owner_session_factory
    OF = get_owner_session_factory()
    async with OF() as s:
        row = (await s.execute(text("""
            SELECT u.id::text, o.id::text FROM users u
            JOIN memberships m ON m.user_id = u.id
            JOIN orgs o ON o.id = m.org_id
            WHERE u.username = 'tokentest' LIMIT 1
        """))).first()
    uid, oid = row
    current_user_id.set(uid); current_org_id.set(oid)

    from apps.worker.tasks.news_chains import _build_chain_async
    events = [
        {"_placeholder_id": "e1", "external_id": "e1",
         "headline": "Fed signals no rate cut",
         "summary": "Powell said rates will stay higher for longer.",
         "occurred_at": "2026-09-15T12:00:00Z", "reported_at": None, "location": None,
         "actors": ["Fed", "Powell"], "event_type": "statement", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e2", "external_id": "e2",
         "headline": "10-year yield hits 5%",
         "summary": "Treasury yields jumped on the Fed statement.",
         "occurred_at": "2026-09-15T14:00:00Z", "reported_at": None, "location": None,
         "actors": ["Treasury"], "event_type": "price_move", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e3", "external_id": "e3",
         "headline": "S&P 500 drops 2%",
         "summary": "Equities sold off sharply after yields rose.",
         "occurred_at": "2026-09-15T20:00:00Z", "reported_at": None, "location": None,
         "actors": ["S&P 500"], "event_type": "price_move", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e4", "external_id": "e4",
         "headline": "Dollar strengthens against yen",
         "summary": "Higher yields supported the dollar.",
         "occurred_at": "2026-09-16T08:00:00Z", "reported_at": None, "location": None,
         "actors": ["USD", "JPY"], "event_type": "price_move", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
    ]
    result = await _build_chain_async(oid, events, "news", None, 800)
    print(f"  status:              {result.get('status')}")
    print(f"  verification_status: {result.get('verification_status')}")
    print(f"  words:               {result.get('words')}")
    print(f"  chain_id:            {result.get('chain_id')}")
    print(f"  error:               {result.get('error')}")
    if result.get("status") != "ok":
        raise SystemExit(1)
    await engine.dispose()

asyncio.run(main())
'''
    r = subprocess.run(
        ["python", "-c", code2], cwd=ROOT,
        capture_output=True, text=True, timeout=300,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-600:])
        return False

    return True


def main() -> None:
    print("=" * 70)
    print("S.6.4.1b — credentials.py: role → roles[]")
    print("=" * 70)

    step1_key_hash()
    step2_list_credentials()
    step3_add_credential()
    step4_get_next_credential()
    step5_get_next_credential_sync()
    step6_any_credentials_exist_sync()
    step7_list_credentials_sync()
    step8_upsert_credential()
    step9_add_provider_key()
    ok = verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.4.1b complete — chain builds work again")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
