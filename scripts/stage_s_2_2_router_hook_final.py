"""
SignalIQ — Phase S.2.2: Move the debit hook to the right function.

Root cause of S.2.1's failure: 'complete()' contains a large block of
DEAD CODE after `raise ProviderError(f"All models failed...")`.
That dead block is a duplicate of _try_complete's body, and it's where
my anchor kept matching.

The hook must live in _try_complete, right after its session.commit(),
where the ai_calls row is actually written for successful calls.

This script:
  1. Removes the misplaced hook from complete()'s dead code
  2. Adds the hook to _try_complete after its session.commit()
  3. Verifies with a real LLM call
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTER = ROOT / "packages" / "ai" / "router.py"


def step1_remove_misplaced_hook() -> None:
    print("\n[1/4] Remove misplaced hook from complete()'s dead code")

    src = ROUTER.read_text()

    # The misplaced hook, exactly as it was inserted
    misplaced = '''        # Debit the current user for the tokens this call consumed.
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

'''

    if misplaced in src:
        src = src.replace(misplaced, "", 1)
        ROUTER.write_text(src)
        print("  ✓ removed misplaced hook")
    else:
        print("  ⚠ misplaced hook not found (already removed?)")


def step2_add_hook_in_try_complete() -> None:
    print("\n[2/4] Add debit hook in _try_complete after its session.commit()")

    src = ROUTER.read_text()

    # Anchor on the _try_complete INSERT block (short version, no RETURNING id)
    anchor = '''    session.execute(
        sql_text("""
            INSERT INTO ai_calls (
                org_id, model_id, purpose, input_tokens, output_tokens,
                latency_ms, status, error, request_hash, response_payload
            ) VALUES (
                :org_id, :model_id, :purpose, :in_tok, :out_tok,
                :latency, :status, :error, :hash, CAST(:payload AS jsonb)
            )
        """),
        {
            "org_id": org_id,
            "model_id": model_id,
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
    session.commit()

    if status != "ok" or result is None:
        raise ProviderError(err_msg or "unknown error")'''

    replacement = '''    session.execute(
        sql_text("""
            INSERT INTO ai_calls (
                org_id, model_id, purpose, input_tokens, output_tokens,
                latency_ms, status, error, request_hash, response_payload
            ) VALUES (
                :org_id, :model_id, :purpose, :in_tok, :out_tok,
                :latency, :status, :error, :hash, CAST(:payload AS jsonb)
            )
        """),
        {
            "org_id": org_id,
            "model_id": model_id,
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
    session.commit()

    # Debit the current user for the tokens this call consumed.
    if status == "ok" and result is not None:
        try:
            from packages.core.context import current_user_id
            uid = current_user_id.get()
            log.info("ai.debit_check", has_uid=bool(uid), purpose=purpose)
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
                    else:
                        log.info(
                            "ai.debited",
                            user_id=uid[:8], amount=total, purpose=purpose,
                        )
        except Exception as exc:
            log.warning("ai.debit_failed", error=str(exc)[:200])

    if status != "ok" or result is None:
        raise ProviderError(err_msg or "unknown error")'''

    if replacement in src:
        print("  ↷ hook already in _try_complete")
        return

    if anchor not in src:
        raise SystemExit(
            "❌ _try_complete INSERT anchor not found.\n"
            "   Show me the region around _try_complete's session.commit()."
        )

    src = src.replace(anchor, replacement, 1)
    ROUTER.write_text(src)
    print("  ✓ hook added in _try_complete")


def step3_verify() -> bool:
    print("\n[3/4] Verify hook is in the right function")
    r = subprocess.run([
        "python", "-c", """
import inspect
from packages.ai.router import complete, _try_complete

c_src = inspect.getsource(complete)
t_src = inspect.getsource(_try_complete)

c_has_hook = 'current_user_id' in c_src
t_has_hook = 'current_user_id' in t_src

print(f'complete() has hook:      {c_has_hook}')
print(f'_try_complete() has hook: {t_has_hook}')

if c_has_hook:
    raise SystemExit('❌ hook still in complete() — should be removed')
if not t_has_hook:
    raise SystemExit('❌ hook not in _try_complete — insertion failed')

print('✓ hook is in exactly the right place')
"""
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-500:])
        return False
    return True


def step4_real_debit_test() -> bool:
    print("\n[4/4] Real debit test (unique prompt, bypass cache)")

    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)
    log = Path("/tmp/signaliq-api.log")
    with log.open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(5)

    r = subprocess.run([
        "python", "-c", """
import asyncio, uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.core.context import current_user_id, current_org_id
from packages.ai.router import complete
from packages.db.session import get_owner_session_factory

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with get_owner_session_factory()() as s:
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

    async with factory() as s:
        b_before = (await s.execute(text(
            "SELECT balance FROM user_tokens WHERE user_id = :u"
        ), {"u": user_id})).scalar()
    print(f"balance before: {b_before}")

    unique = str(uuid.uuid4())[:8]
    resp = await complete(
        role="reasoning",
        system="Answer in one word.",
        user=f"Say hi for {unique}.",
        purpose="s2.2.debit_test",
        temperature=0.7,
        max_tokens=200,
        use_cache=False,
    )
    print(f"response: {resp[:60]}")

    async with factory() as s:
        b_after = (await s.execute(text(
            "SELECT balance FROM user_tokens WHERE user_id = :u"
        ), {"u": user_id})).scalar()
        consumed = (await s.execute(text(
            "SELECT lifetime_consumed FROM user_tokens WHERE user_id = :u"
        ), {"u": user_id})).scalar()
    print(f"balance after:  {b_after}")
    print(f"consumed delta: {consumed}")

    await engine.dispose()

asyncio.run(main())
        """.strip()
    ], cwd=ROOT, capture_output=True, text=True, timeout=60)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False

    # Success means balance dropped
    import re
    m = re.search(r"balance before: (\d+).*balance after:\s+(\d+)", r.stdout, re.DOTALL)
    if not m:
        return False
    return int(m.group(1)) > int(m.group(2))


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.2.2: Router debit hook (right function)")
    print("=" * 70)

    step1_remove_misplaced_hook()
    step2_add_hook_in_try_complete()
    ok_verify = step3_verify()
    if not ok_verify:
        sys.exit(1)

    ok = step4_real_debit_test()

    print("=" * 70)
    if ok:
        print("✅ S.2.2 complete — debit fires on real LLM calls")
        sys.exit(0)
    print("❌ S.2.2 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
