"""S.5.4c.3: replace the exception-text-matching replace logic with a
proper INSERT ... ON CONFLICT ... DO UPDATE upsert.

Current add_provider_key tries:
    add_credential(...)
  except Exception as exc:
    if "uq_ai_credentials_org_label" in str(exc) or "unique" in str(exc).lower():
        DELETE FROM ai_credentials WHERE org_id = :oid AND label = :label
        add_credential(...)   # retry

That's fragile:
  - Depends on the exact string Postgres / asyncpg emits (varies).
  - Two statements + two commits when one would do.
  - If the exception text doesn't match, the request 500s.

The fix: one `INSERT ... ON CONFLICT (org_id, label) DO UPDATE`. No
exception path. Idempotent. Re-add after delete is just an insert.
Re-add without delete is just an update.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRED_SVC = ROOT / "packages" / "core" / "credentials.py"
WEB = ROOT / "apps" / "web"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


UPSERT_FN = '''

async def upsert_credential(
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
    """Insert a credential, or update it if (org_id, label) already exists.

    Single statement, no exception handling. Idempotent — safe to call
    repeatedly with the same arguments. Clears exhausted_until on update
    (a re-added key should start fresh).
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
    })).first()

    await session.commit()
    log.info("credentials.upserted", org_id=org_id[:8], provider=provider,
             role=role, label=label)
    return {
        "id": row[0], "provider": provider, "model_name": model_name,
        "role": role, "label": label, "key_preview": mask_key(api_key),
        "is_active": True, "priority": priority,
        "created_at": row[1],
    }
'''


OLD_LOOP = '''    created: list[dict[str, Any]] = []
    for role, model_name, priority in PROVIDER_CATALOG[provider]:
        # Label is derived, unique per (org, label). Include role so
        # multiple rows for one provider don't collide.
        label = f"{provider}-{role}"
        try:
            cred = await add_credential(
                session, org_id,
                provider=provider, model_name=model_name, role=role,
                label=label, api_key=api_key, priority=priority,
            )
            created.append(cred)
        except Exception as exc:
            msg = str(exc)
            if "uq_ai_credentials_org_label" in msg or "unique" in msg.lower():
                # Already exists — delete the old, insert the fresh one.
                await session.execute(sql_text("""
                    DELETE FROM ai_credentials
                    WHERE org_id = :oid AND label = :label
                """), {"oid": org_id, "label": label})
                await session.commit()
                cred = await add_credential(
                    session, org_id,
                    provider=provider, model_name=model_name, role=role,
                    label=label, api_key=api_key, priority=priority,
                )
                created.append(cred)
            else:
                raise
    return created'''

NEW_LOOP = '''    created: list[dict[str, Any]] = []
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
    return created'''


def main() -> None:
    print("=" * 70)
    print("S.5.4c.3: upsert_credential — replace text-matching with ON CONFLICT")
    print("=" * 70)

    src = CRED_SVC.read_text()

    print("\n[1/4] add upsert_credential function")
    if "async def upsert_credential" in src:
        print("  ↷ already present")
    else:
        src = src.rstrip() + UPSERT_FN
        CRED_SVC.write_text(src)
        print("  ✓ added upsert_credential")

    print("\n[2/4] rewrite add_provider_key's loop to use upsert_credential")
    src = CRED_SVC.read_text()
    if NEW_LOOP in src and OLD_LOOP not in src:
        print("  ↷ already rewritten")
    elif OLD_LOOP in src:
        src = src.replace(OLD_LOOP, NEW_LOOP, 1)
        CRED_SVC.write_text(src)
        print("  ✓ add_provider_key now uses upsert_credential")
    else:
        fail("add_provider_key loop pattern not found — inspect manually")

    print("\n[3/4] python syntax check")
    r = subprocess.run(
        ["python", "-c",
         f"import ast; ast.parse(open('{CRED_SVC}').read())"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        fail(f"syntax: {r.stderr[:400]}")
    print("  ✓ credentials.py parses")

    print("\n[4/4] tsc (unchanged frontend — just confirming)")
    r = subprocess.run(
        ["./node_modules/.bin/tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        print("  ⚠ TS errors (unexpected — nothing in the frontend changed):")
        for line in (r.stdout or r.stderr).splitlines()[:10]:
            print(f"      {line}")
    else:
        print("  ✓ TypeScript clean")

    print("=" * 70)
    print("✅ S.5.4c.3 complete")
    print()
    print("Next: verify with 3 curl calls:")
    print("  1) POST real key  → 201, 2 rows")
    print("  2) POST same key  → 201, still 2 rows (update, not duplicate)")
    print("  3) DELETE one row, POST again → 201, back to 2 rows")
    print("=" * 70)


if __name__ == "__main__":
    main()
