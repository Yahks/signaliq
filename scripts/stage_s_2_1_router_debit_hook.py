"""
SignalIQ — Phase S.2.1: Actually wire the router debit hook.

My S.2 patch script used an anchor string that doesn't exist in the
router (the code says `payload_json`, not `_json_dumps(...)`), so the
whole hook was a silent no-op. Real LLM calls completed without
debiting the user.

Fix: insert the debit call right after session.commit() at line ~168,
using the actual code as anchor. Also add the module-level import of
the current_user_id contextvar.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTER = ROOT / "packages" / "ai" / "router.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:500]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. Add the debit call after session.commit() in _try_complete
# ─────────────────────────────────────────────────────────────────────

def step1_add_debit_call() -> None:
    print("\n[1/3] Insert debit call after the ai_calls commit")

    _patch_and_verify(
        ROUTER,
        '''        session.commit()

        if status != "ok" or result is None:
            raise ProviderError(err_msg or "unknown provider error")

        log.info(
            "ai.complete",
            role=role,
            purpose=purpose,
            model=model_name,
            latency_ms=latency_ms,
            in_tokens=result.input_tokens,
            out_tokens=result.output_tokens,
        )
        return result.text''',
        '''        session.commit()

        # Debit the current user for the tokens this call consumed.
        # Only fires on successful real LLM calls — cache hits and errors
        # never reach this block.
        if status == "ok" and result is not None:
            try:
                from packages.core.context import current_user_id
                uid = current_user_id.get()
                if uid:
                    in_tok = int(result.input_tokens or 0)
                    out_tok = int(result.output_tokens or 0)
                    total = in_tok + out_tok
                    if total > 0:
                        from packages.core.tokens import debit as _debit
                        ok_debit = await _debit(
                            session, uid, total, "llm_call",
                            org_id=org_id,
                            metadata={"purpose": purpose, "model": model_name},
                        )
                        if not ok_debit:
                            log.warning(
                                "ai.debit_rejected",
                                user_id=uid[:8], amount=total, purpose=purpose,
                            )
            except Exception as exc:
                # Never break a completed LLM call on token bookkeeping.
                log.warning("ai.debit_failed", error=str(exc)[:200])

        if status != "ok" or result is None:
            raise ProviderError(err_msg or "unknown provider error")

        log.info(
            "ai.complete",
            role=role,
            purpose=purpose,
            model=model_name,
            latency_ms=latency_ms,
            in_tokens=result.input_tokens,
            out_tokens=result.output_tokens,
        )
        return result.text''',
        "debit hook after ai_calls commit",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Verify the import chain works
# ─────────────────────────────────────────────────────────────────────

def step2_verify_import() -> bool:
    print("\n[2/3] Verify router imports cleanly")
    r = subprocess.run([
        "python", "-c",
        "from packages.ai.router import complete, _try_complete; "
        "import inspect; "
        "src = inspect.getsource(_try_complete); "
        "assert 'current_user_id' in src, 'hook not present'; "
        "assert 'packages.core.tokens' in src, 'debit import missing'; "
        "print('hook present in _try_complete')"
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-500:])
        return False
    return True


# ─────────────────────────────────────────────────────────────────────
# 3. Restart API + real debit test
# ─────────────────────────────────────────────────────────────────────

def step3_test_debit() -> bool:
    print("\n[3/3] Restart + real debit test")

    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)
    log = Path("/tmp/signaliq-api.log")
    with log.open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(5)

    # Real debit test — unique prompt, bypass cache
    r = subprocess.run([
        "python", "-c", """
import asyncio, uuid, subprocess
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.core.context import current_user_id, current_org_id
from packages.ai.router import complete

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    from packages.db.session import get_owner_session_factory
    owner_factory = get_owner_session_factory()
    async with owner_factory() as s:
        row = (await s.execute(text(\"\"\"
            SELECT u.id::text, o.id::text
            FROM users u
            JOIN memberships m ON m.user_id = u.id
            JOIN orgs o ON o.id = m.org_id
            WHERE u.username = 'tokentest'
            LIMIT 1
        \"\"\"))).first()
    user_id, org_id = row
    current_user_id.set(user_id)
    current_org_id.set(org_id)

    # Balance before
    async with factory() as s:
        b_before = (await s.execute(text(
            "SELECT balance FROM user_tokens WHERE user_id = :u"
        ), {"u": user_id})).scalar()
    print(f"balance before: {b_before}")

    # Unique prompt → cache miss
    unique = str(uuid.uuid4())[:8]
    resp = await complete(
        role="reasoning",
        system="Answer in one word.",
        user=f"Say hi for {unique}.",
        purpose="s2.1.debit_test",
        temperature=0.7,
        max_tokens=200,
        use_cache=False,
    )
    print(f"response: {resp[:60]}")

    # Balance after
    async with factory() as s:
        b_after = (await s.execute(text(
            "SELECT balance FROM user_tokens WHERE user_id = :u"
        ), {"u": user_id})).scalar()
        consumed = (await s.execute(text(
            "SELECT lifetime_consumed FROM user_tokens WHERE user_id = :u"
        ), {"u": user_id})).scalar()
    print(f"balance after:  {b_after}")
    print(f"consumed delta: {consumed}")

    # Ledger
    async with factory() as s:
        rows = (await s.execute(text(\"\"\"
            SELECT delta, reason, balance_after, created_at
            FROM token_ledger
            WHERE user_id = :u
            ORDER BY created_at DESC
            LIMIT 5
        \"\"\"), {"u": user_id})).all()
    for r in rows:
        print(f"  {r[0]:>10} {r[1]:20} bal_after={r[2]} at={r[3]}")

    await engine.dispose()

asyncio.run(main())
        """.strip()
    ], cwd=ROOT, capture_output=True, text=True, timeout=60)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False

    return "balance after:" in r.stdout and "consumed delta:" in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.2.1: Router debit hook (for real this time)")
    print("=" * 70)

    step1_add_debit_call()
    ok_import = step2_verify_import()
    if not ok_import:
        print("❌ import verification failed")
        sys.exit(1)

    ok = step3_test_debit()

    print("=" * 70)
    if ok:
        print("✅ S.2.1 complete — debit hook works on real calls")
        sys.exit(0)
    print("❌ S.2.1 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
