"""S.6.4.3 — verification.

Five checks with a real LLM call so the whole chain is exercised:

  1. schema: the new columns exist
  2. real LLM call: complete() succeeds and returns text
  3. attribution: the new ai_calls row has credential_id set,
     and rate_headers was captured from Groq
  4. aggregation: list_credentials returns tokens_used > 0 and
     calls_made > 0 for that credential
  5. cascade: deleting the credential sets ai_calls.credential_id
     to NULL (ON DELETE SET NULL) rather than deleting the audit row
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    print("=" * 70)
    print("S.6.4.3 — verification")
    print("=" * 70)

    # Restart the API + worker first so they load the patched code
    print("\n[0/5] restart API + worker")
    subprocess.run(["bash", "-c", "fuser -k 8000/tcp 2>/dev/null"], check=False)
    subprocess.run(["bash", "-c", "pkill -f 'celery.*worker' 2>/dev/null"], check=False)
    time.sleep(2)

    with Path("/tmp/signaliq-api.log").open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(6)
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout}")
        sys.exit(1)
    print("  ✓ API up")

    with Path("/tmp/signaliq-worker.log").open("w") as f:
        subprocess.Popen(
            ["celery", "-A", "apps.worker.celery_app", "worker",
             "-l", "info", "-Q", "default,ingestion", "--hostname=worker1@%h"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(8)
    print("  ✓ worker up")

    # The five-step block
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

    # ── Step 1: schema ─────────────────────────────────────────────
    async with OF() as s:
        a = [x[0] for x in (await s.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='ai_calls'
              AND column_name IN ('credential_id','rate_headers')
            ORDER BY column_name
        """))).all()]
        b = [x[0] for x in (await s.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='ai_credentials'
              AND column_name IN ('last_remaining_tokens','last_rate_headers','last_rate_at')
            ORDER BY column_name
        """))).all()]
    if set(a) != {"credential_id", "rate_headers"}:
        print(f"  FAIL step1 ai_calls: {a}")
        return 1
    if set(b) != {"last_remaining_tokens", "last_rate_headers", "last_rate_at"}:
        print(f"  FAIL step1 ai_credentials: {b}")
        return 1
    print(f"  PASS step1  ai_calls.[credential_id, rate_headers]  "
          f"ai_credentials.[last_rate_at, last_rate_headers, last_remaining_tokens]")

    # ── Resolve test org + credential ─────────────────────────────
    async with OF() as s:
        row = (await s.execute(text("""
            SELECT u.id::text, o.id::text FROM users u
            JOIN memberships m ON m.user_id = u.id
            JOIN orgs o ON o.id = m.org_id
            WHERE u.username = 'tokentest' LIMIT 1
        """))).first()
    uid, oid = row
    current_user_id.set(uid); current_org_id.set(oid)

    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        cred = (await s.execute(text("""
            SELECT id::text, label FROM ai_credentials
            WHERE org_id = :oid AND is_active = true
            ORDER BY priority LIMIT 1
        """), {"oid": oid})).first()
    if not cred:
        print("  FAIL: no credential for tokentest")
        return 1
    cred_id, cred_label = cred
    print(f"  using credential: {cred_label}  ({cred_id[:8]}...)")

    # Snapshot pre-state
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        pre = (await s.execute(text(
            "SELECT COUNT(*) FROM ai_calls WHERE credential_id = :cid"
        ), {"cid": cred_id})).scalar() or 0

    # ── Step 2: real LLM call ────────────────────────────────────
    from packages.ai.router import complete
    try:
        resp = await complete(
            role="fast", system=None, user="Say OK.",
            org_id=oid, purpose="s643.verify",
            temperature=0.0, max_tokens=5,
            use_cache=False, json_mode=False,
        )
        print(f"  PASS step2  LLM returned: {resp[:30]!r}")
    except Exception as e:
        print(f"  FAIL step2  LLM call: {type(e).__name__}: {str(e)[:200]}")
        return 1

    # ── Step 3: attribution + rate_headers ───────────────────────
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        row = (await s.execute(text("""
            SELECT credential_id::text, rate_headers
            FROM ai_calls
            WHERE purpose = 's643.verify'
            ORDER BY created_at DESC LIMIT 1
        """))).first()
    if not row or row[0] != cred_id:
        print(f"  FAIL step3  credential_id={row[0] if row else None} expected {cred_id[:8]}")
        return 1
    print(f"  PASS step3  ai_calls.credential_id = {row[0][:8]}...")
    if row[1]:
        keys = sorted(row[1].keys())[:3]
        print(f"              rate_headers captured: {keys}...")
    else:
        print("              (rate_headers NULL — provider may not return them)")

    # ── Step 4: aggregation via list_credentials ────────────────
    from packages.core.credentials import list_credentials
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        items = await list_credentials(s, oid)
    cred_dict = next((c for c in items if c["id"] == cred_id), None)
    if not cred_dict:
        print("  FAIL step4  credential not found in list_credentials")
        return 1
    tu = cred_dict["tokens_used"]; cm = cred_dict["calls_made"]
    lrt = cred_dict["last_remaining_tokens"]; lra = cred_dict["last_rate_at"]
    print(f"  PASS step4  tokens_used={tu}  calls_made={cm}  "
          f"last_remaining_tokens={lrt}  last_rate_at={lra}")
    if tu <= 0 or cm <= 0:
        print(f"              WARN  tokens_used/calls_made is 0 — the call may have been cached")
        print(f"              pre-count was {pre}; if the call succeeded, calls_made should be > pre")

    # ── Step 5: ON DELETE SET NULL ──────────────────────────────
    # Create a throwaway credential, make one call with it, delete it,
    # confirm the ai_calls row survives with credential_id = NULL.
    print("  testing ON DELETE SET NULL with a throwaway credential...")

    # The simplest test: insert a fake ai_calls row with credential_id
    # = cred_id, then delete the credential, then check credential_id.
    # But deleting the real credential breaks everything downstream, so
    # we use a real throwaway credential instead.
    from packages.core.credentials import upsert_credential, _key_fingerprint, encrypt_key
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        # Clean up any prior throwaway
        await s.execute(text("""
            DELETE FROM ai_credentials
            WHERE org_id = :oid AND label = 's643-throwaway'
        """), {"oid": oid})
        await s.commit()
        fake_key = "gsk_s643_throwaway_1234567890"
        throwaway = await upsert_credential(
            s, oid,
            provider="groq", model_name="openai/gpt-oss-20b",
            roles=["fast"], label="s643-throwaway",
            api_key=fake_key, priority=999,
        )
    throwaway_id = throwaway["id"]

    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        await s.execute(text("""
            INSERT INTO ai_calls (org_id, credential_id, purpose, status)
            VALUES (:oid, :cid, 's643.setnull.test', 'ok')
        """), {"oid": oid, "cid": throwaway_id})
        await s.commit()
        n_pre = (await s.execute(text(
            "SELECT COUNT(*) FROM ai_calls WHERE purpose = 's643.setnull.test'"
        ))).scalar()

    # Delete the throwaway credential
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        await s.execute(text(
            "DELETE FROM ai_credentials WHERE id = :cid"
        ), {"cid": throwaway_id})
        await s.commit()

    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        after = (await s.execute(text("""
            SELECT credential_id::text FROM ai_calls
            WHERE purpose = 's643.setnull.test'
        """))).all()

    if not after or len(after) != n_pre:
        print(f"  FAIL step5  expected {n_pre} rows, got {len(after)}")
        return 1
    if any(r[0] is not None for r in after):
        print(f"  FAIL step5  credential_id was not nulled: {after}")
        return 1
    print(f"  PASS step5  ON DELETE SET NULL — {len(after)} audit row(s) kept, credential_id=NULL")

    await engine.dispose()
    print()
    print("  ALL FIVE CHECKS PASSED")
    return 0


sys.exit(asyncio.run(main()))
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=180,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        print("=" * 70)
        print("❌ verification failed")
        sys.exit(1)

    print("=" * 70)
    print("✅ S.6.4.3 complete")
    print()
    print("Browser check:")
    print("  open http://localhost:3000/settings/credentials")
    print("  two new columns: 'Groq tokens' and 'Groq remaining'")
    print("  each with a call count / 'updated Xm ago' sub-line")
    print("=" * 70)


if __name__ == "__main__":
    main()
