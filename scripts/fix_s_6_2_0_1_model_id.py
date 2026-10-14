"""S.6.2.0.1 — restore ai_calls.model_id for credentialed calls.

The S.6.2.0 router rewrite replaced _try_complete (which resolved an
ai_models row and passed its id) with _try_with_credential (which takes
a credential and never touches ai_models). The INSERT in the rewrite
passed NULL for model_id, so every credentialed call since then has an
empty model_id — the DB can no longer answer 'which model did what'.

Fix: before the INSERT, look up the ai_models row for the credential's
(provider, model_name) and pass its id. Fall back to NULL when the
provider+model isn't in the catalog (a user's newer model that we
haven't registered), which keeps the row usable and the structlog line
authoritative.

Verified: one credentialed call writes a non-NULL model_id.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTER = ROOT / "packages" / "ai" / "router.py"


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


def step1_patch_router() -> None:
    print("\n[1/3] patch router.py")

    # Insert the lookup just before the ai_calls INSERT. The INSERT uses
    # `:model_id` already; we need to resolve it and add it to the params.
    patch(
        ROUTER,
        '''    # Record the attempt (success or failure) in ai_calls.
    import json as _json
    payload_json = None if not result else _json.dumps({"text": result.text})
    session.execute(
        sql_text("""
            INSERT INTO ai_calls (
                org_id, model_id, prompt_version_id, purpose, input_tokens, output_tokens,
                latency_ms, status, error, request_hash, response_payload
            ) VALUES (
                :org_id, NULL, :prompt_version_id, :purpose, :in_tok, :out_tok,
                :latency, :status, :error, :hash, CAST(:payload AS jsonb)
            )
        """),
        {
            "org_id": org_id,
            "prompt_version_id": prompt_version_id,
            "purpose": purpose,
            "in_tok": result.input_tokens if result else None,
            "out_tok": result.output_tokens if result else None,
            "latency": latency_ms,
            "status": status,
            "error": err_msg,
            "hash": req_hash,
            "payload": payload_json,
        },
    )
    session.commit()''',
        '''    # Record the attempt (success or failure) in ai_calls.
    import json as _json
    payload_json = None if not result else _json.dumps({"text": result.text})

    # S.6.2.0.1 — resolve model_id from the ai_models catalog so the row
    # is joinable. NULL when this credential's provider+model isn't in
    # the catalog (a newer model we haven't registered).
    model_row = session.execute(
        sql_text("""
            SELECT id::text FROM ai_models
            WHERE provider = :p AND model_name = :m
            ORDER BY created_at ASC
            LIMIT 1
        """),
        {"p": provider_name, "m": model_name},
    ).first()
    model_id = model_row[0] if model_row else None

    session.execute(
        sql_text("""
            INSERT INTO ai_calls (
                org_id, model_id, prompt_version_id, purpose, input_tokens, output_tokens,
                latency_ms, status, error, request_hash, response_payload
            ) VALUES (
                :org_id, :model_id, :prompt_version_id, :purpose, :in_tok, :out_tok,
                :latency, :status, :error, :hash, CAST(:payload AS jsonb)
            )
        """),
        {
            "org_id": org_id,
            "model_id": model_id,
            "prompt_version_id": prompt_version_id,
            "purpose": purpose,
            "in_tok": result.input_tokens if result else None,
            "out_tok": result.output_tokens if result else None,
            "latency": latency_ms,
            "status": status,
            "error": err_msg,
            "hash": req_hash,
            "payload": payload_json,
        },
    )
    session.commit()''',
        "resolve model_id before INSERT",
    )


def step2_restart() -> bool:
    print("\n[2/3] restart API + worker")
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    subprocess.run(["pkill", "-9", "-f", "celery.*worker"], check=False)
    time.sleep(3)
    api_log = Path("/tmp/signaliq-api.log")
    with api_log.open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    worker_log = Path("/tmp/signaliq-worker.log")
    with worker_log.open("w") as f:
        subprocess.Popen(
            ["celery", "-A", "apps.worker.celery_app", "worker",
             "-l", "info", "-Q", "default,ingestion", "--hostname=worker1@%h"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(8)
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout}")
        return False
    print("  ✓ API up")
    return True


def step3_verify() -> bool:
    print("\n[3/3] verify model_id is now written")

    code = r'''
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

    from packages.ai.router import complete

    # One trivial call
    try:
        r = await complete(
            role="fast", system=None, user="Say OK.",
            org_id=oid, purpose="s6.2.0.1.test",
            temperature=0.0, max_tokens=5,
            use_cache=False, json_mode=False,
        )
        print(f"  complete() returned: {r[:30]!r}")
    except Exception as exc:
        print(f"  complete() raised: {type(exc).__name__}: {str(exc)[:200]}")
        return

    # Check the most recent ai_calls row for this purpose
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        row = (await s.execute(text("""
            SELECT id::text, model_id::text, purpose, status
            FROM ai_calls
            WHERE purpose = 's6.2.0.1.test'
            ORDER BY created_at DESC
            LIMIT 1
        """))).first()

    if not row:
        print("  ❌ no ai_calls row found")
        return

    call_id, model_id, purpose, status = row
    print(f"  ai_calls row:")
    print(f"    purpose  = {purpose}")
    print(f"    status   = {status}")
    print(f"    model_id = {model_id}")

    if model_id:
        # Join to ai_models to confirm it resolves
        async with F() as s:
            m = (await s.execute(text("""
                SELECT provider, model_name FROM ai_models WHERE id = :mid
            """), {"mid": model_id})).first()
        if m:
            print(f"    → resolves to: {m[0]}/{m[1]}")
            print()
            print("  ✓ model_id populated and joinable")
            print("  ✓ S.6.2.0.1 verified")
        else:
            print(f"  ⚠ model_id set but no matching ai_models row")
    else:
        print("  ❌ model_id is still NULL")

    await engine.dispose()

asyncio.run(main())
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=120,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-600:])
        return False
    return "S.6.2.0.1 verified" in r.stdout


def main() -> None:
    print("=" * 70)
    print("S.6.2.0.1 — restore ai_calls.model_id for credentialed calls")
    print("=" * 70)

    step1_patch_router()
    ok_restart = step2_restart()
    ok_verify = step3_verify() if ok_restart else False

    print("=" * 70)
    if ok_restart and ok_verify:
        print("✅ S.6.2.0.1 complete")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
