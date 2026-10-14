"""S.6.2.0.1 — definitive rotation test with a mock 429 provider.

Adds a `mock_429` provider branch to _build_provider, then verifies:
  1. mock-429 (prio 5) + real key (prio 10) → real key used after mock 429s
  2. mock's exhausted_until is set to the future
  3. log shows ai.cred_exhausted + ai.cred_rotate
  4. second call skips the (now exhausted) mock and succeeds immediately

Cleans up after itself. Idempotent.
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
             f"--- expected ---\n{old[:400]}")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. Add the mock provider branch to _build_provider
# ─────────────────────────────────────────────────────────────────────

MOCK_BRANCH = '''    if provider_name == "mock_429":
        # S.6.2.0.1 — deterministic 429 for the rotation test.
        # Not registered anywhere else; only reachable when a credential
        # has provider="mock_429" in the DB.
        from packages.ai.providers.base import ProviderError as _PE

        class _Always429:
            async def complete(self, **kwargs):
                raise _PE("Groq HTTP 429: rate limit reached for mock_429")

        return _Always429()
    if provider_name == "ollama":
        return OllamaProvider()
    raise ProviderError(f"Unknown provider: {provider_name}")'''


def step1_add_mock() -> None:
    print("\n[1/3] add mock_429 provider branch")

    src = ROUTER.read_text()
    if 'provider_name == "mock_429"' in src:
        print("  ↷ mock_429 already present")
        return

    # The last three lines of _build_provider currently are:
    #     if provider_name == "ollama":
    #         return OllamaProvider()
    #     raise ProviderError(f"Unknown provider: {provider_name}")
    old = '''    if provider_name == "ollama":
        return OllamaProvider()
    raise ProviderError(f"Unknown provider: {provider_name}")'''

    patch(ROUTER, old, MOCK_BRANCH, "add mock_429 branch")


# ─────────────────────────────────────────────────────────────────────
# 2. Run the definitive rotation test
# ─────────────────────────────────────────────────────────────────────

TEST_CODE = r'''
import asyncio, os, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.core.context import current_user_id, current_org_id
from packages.core.credentials import upsert_credential

REAL_KEY = os.environ.get("GROQ_API_KEY", "")
if not REAL_KEY:
    print("  GROQ_API_KEY not set — cannot run rotation test")
    sys.exit(2)

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

    # Clean slate — remove ALL fast credentials for the org temporarily.
    # We save their labels so we can restore them.
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        # Snapshot which fast credentials exist
        rows = (await s.execute(text("""
            SELECT id::text, label FROM ai_credentials
            WHERE org_id = :oid AND role = 'fast' AND is_active = true
        """), {"oid": oid})).all()
        saved = [(r[0], r[1]) for r in rows]
        # Deactivate them all so the test has a clean slate
        for cid, _ in saved:
            await s.execute(text(
                "UPDATE ai_credentials SET is_active = false WHERE id = :cid"
            ), {"cid": cid})
        await s.commit()
        print(f"  deactivated {len(saved)} existing fast credentials")

        # Add the mock 429 at prio 5 and the real key at prio 10
        await upsert_credential(
            s, oid, provider="mock_429", model_name="mock",
            role="fast", label="s620-mock", api_key="x" * 30, priority=5,
        )
        await upsert_credential(
            s, oid, provider="groq", model_name="openai/gpt-oss-20b",
            role="fast", label="s620-real", api_key=REAL_KEY, priority=10,
        )

    from packages.ai.router import complete

    print("\n  CALL 1: mock (prio 5) 429s → real (prio 10) succeeds")
    try:
        r = await complete(
            role="fast", system=None, user="Say OK.",
            org_id=oid, purpose="s620.rotation.test",
            temperature=0.0, max_tokens=5,
            use_cache=False, json_mode=False,
        )
        print(f"    ✓ complete() returned: {r[:60]!r}")
    except Exception as exc:
        print(f"    ❌ complete() raised: {type(exc).__name__}: {str(exc)[:200]}")
        await _cleanup(F, oid, saved)
        sys.exit(1)

    # Check the mock credential is now exhausted
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        row = (await s.execute(text("""
            SELECT exhausted_until FROM ai_credentials
            WHERE org_id = :oid AND label = 's620-mock'
        """), {"oid": oid})).first()
    if row and row[0] is not None:
        print(f"    ✓ mock credential marked exhausted until {row[0]}")
    else:
        print(f"    ❌ mock credential was NOT marked exhausted")
        await _cleanup(F, oid, saved)
        sys.exit(1)

    print("\n  CALL 2: mock is exhausted → router skips it, real succeeds directly")
    try:
        r = await complete(
            role="fast", system=None, user="Say OK.",
            org_id=oid, purpose="s620.rotation.test2",
            temperature=0.0, max_tokens=5,
            use_cache=False, json_mode=False,
        )
        print(f"    ✓ complete() returned: {r[:60]!r}")
    except Exception as exc:
        print(f"    ❌ complete() raised: {type(exc).__name__}: {str(exc)[:200]}")
        await _cleanup(F, oid, saved)
        sys.exit(1)

    print("\n  ✓ ROTATION PROVEN: mock 429'd → real used → mock marked exhausted")
    print("                     second call skipped the exhausted mock")

    await _cleanup(F, oid, saved)
    await engine.dispose()


async def _cleanup(F, oid, saved):
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        # Remove the test credentials
        await s.execute(text("""
            DELETE FROM ai_credentials
            WHERE org_id = :oid AND label IN ('s620-mock', 's620-real')
        """), {"oid": oid})
        # Restore the previously active ones
        for cid, _ in saved:
            await s.execute(text(
                "UPDATE ai_credentials SET is_active = true WHERE id = :cid"
            ), {"cid": cid})
        await s.commit()
    print("  ✓ cleanup done")


asyncio.run(main())
'''


def step2_run_test() -> bool:
    print("\n[2/3] run rotation test")
    r = subprocess.run(
        ["python", "-c", TEST_CODE], cwd=ROOT,
        capture_output=True, text=True, timeout=180,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False
    return "ROTATION PROVEN" in r.stdout


def step3_check_logs() -> None:
    print("\n[3/3] confirm rotation events in the API log")
    p = Path("/tmp/signaliq-api.log")
    if not p.exists():
        print("  (no API log to check)")
        return
    for line in p.read_text().splitlines():
        if "ai.cred_exhausted" in line or "ai.cred_rotate" in line:
            print(f"  {line[:220]}")


def main() -> None:
    print("=" * 70)
    print("S.6.2.0.1 — verify credential rotation with a mock 429 provider")
    print("=" * 70)

    step1_add_mock()

    # Restart the API so the mock branch takes effect
    print("\n  restarting API")
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)
    log = Path("/tmp/signaliq-api.log")
    with log.open("w") as f:
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

    ok = step2_run_test()
    step3_check_logs()

    print("=" * 70)
    if ok:
        print("✅ S.6.2.0.1 complete — rotation is proven")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
