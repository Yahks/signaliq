"""S.6.3.5a — verify payload size + 413 handling.

S.6.3.5 added ~1,500 tokens of prompt (the classification taxonomy +
priority rules). The verify call then sent:
  prompt_chars=10131 (~2,500 input tokens)
  max_tokens=8000
  total ~10,500 tokens
... to gpt-oss-20b, whose context is 8,192 tokens. Groq returned 413
Payload Too Large.

The router then treated the 413 as a rate limit — because its 429 check
matched the substring "rate" in the phrase "Credential rate-limited"
that appeared in the error text itself. The credential was marked
exhausted for 15 minutes on a payload error.

Two fixes:
1. verify(): truncate body to 5000 chars (was 8000) and lower
   max_tokens to 6000. Peak ~1,200 in + 6,000 out = 7,200, under the
   limit. 6,000 is still enough for 20 claims.
2. Router 429 detection: match the actual rate-limit signatures
   (429, rate_limit_exceeded, TPM), not the substring "rate".
   Everything else is a real error and does not exhaust the credential.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ROOT / "packages" / "ai" / "chain" / "verifier.py"
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


# ─────────────────────────────────────────────────────────────────────
# 1. verifier.py — smaller input cap + smaller output cap
# ─────────────────────────────────────────────────────────────────────

def step1_verifier() -> None:
    print("\n[1/3] verifier.py — payload size")

    # The body truncation: 8000 -> 5000
    patch(
        VERIFIER,
        '''    prompt, _pv = render_with_version(
        "chain.verify",
        block_slug=block_slug,
        body=body[:8000],
        evidence=_format_evidence(events),
    )''',
        '''    # S.6.3.5a — verify prompt grew with the classification taxonomy.
    # Cap the body at 5000 chars (was 8000) so the combined prompt +
    # max_tokens fits the fast model's 8,192-token context window.
    body_capped = body[:5000] if body else ""
    prompt, _pv = render_with_version(
        "chain.verify",
        block_slug=block_slug,
        body=body_capped,
        evidence=_format_evidence(events),
    )''',
        "body[:8000] → body[:5000]",
    )

    # max_tokens 8000 -> 6000
    patch(
        VERIFIER,
        '''    log.info(
        "chain.verify.call",
        body_chars=len(body),
        prompt_chars=len(prompt),
        max_tokens=8000,
        block=block_slug,
    )

    raw = await complete(
        role="fast",
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose=f"chain.verify.{block_slug or 'default'}",
        temperature=0.1,
        max_tokens=8000,   # headroom: even if the model ignores the 20-cap
                           # prompt, the JSON completes rather than truncating
        json_mode=True,
        use_cache=use_cache,
    )''',
        '''    # S.6.3.5a — cap output at 6000 so prompt (~1,200 tokens) + output
    # (max 6,000) = 7,200, under the fast model's 8,192 context. Still
    # enough for 20 claim objects (~40-60 tokens each = ~1,200 tokens).
    log.info(
        "chain.verify.call",
        body_chars=len(body_capped),
        prompt_chars=len(prompt),
        max_tokens=6000,
        block=block_slug,
    )

    raw = await complete(
        role="fast",
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose=f"chain.verify.{block_slug or 'default'}",
        temperature=0.1,
        max_tokens=6000,   # S.6.3.5a — was 8000; prompt grew, so this
                           # keeps prompt+output under the model's context
        json_mode=True,
        use_cache=use_cache,
    )''',
        "max_tokens 8000 → 6000",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. router.py — 413 is not a rate limit
# ─────────────────────────────────────────────────────────────────────

def step2_router() -> None:
    print("\n[2/3] router.py — correct 429 detection")

    patch(
        ROUTER,
        '''    # ── 429 → mark exhausted and raise CredentialsExhaustedError ──
    if status == "error" and err_msg:
        msg_lower = err_msg.lower()
        if "429" in err_msg or "rate" in msg_lower or "quota" in msg_lower:
            try:
                retry_after = parse_retry_after(err_msg, default_seconds=900)
                mark_exhausted_sync(session, cred_id, seconds=retry_after)
                log.info(
                    "ai.cred_exhausted",
                    cred_id=cred_id[:8],
                    provider=provider_name,
                    retry_after=retry_after,
                )
            except Exception as _exc:
                log.warning("router.mark_exhausted_failed", error=str(_exc)[:120])
            raise CredentialsExhaustedError(
                f"Credential rate-limited ({provider_name}): {err_msg[:200]}"
            )
        # Non-429 error → abort, don't rotate.
        raise ProviderError(err_msg)''',
        '''    # ── Real rate limit → mark exhausted + rotate ────────────────
    # S.6.3.5a — the previous check matched the bare substring "rate",
    # which also appears in our own "Credential rate-limited" wrapper text
    # and in "Request too large" bodies. That caused 413 Payload Too Large
    # to exhaust a credential for 15 minutes on a request that was simply
    # too big, not rate-limited. Match only the actual rate-limit
    # signatures:
    if status == "error" and err_msg:
        msg_lower = err_msg.lower()
        is_rate_limit = (
            "http 429" in msg_lower
            or "rate_limit_exceeded" in msg_lower
            or "tokens per minute" in msg_lower
            or "tpm" in msg_lower
            or "requests per minute" in msg_lower
            or "rpm" in msg_lower
        )
        if is_rate_limit:
            try:
                retry_after = parse_retry_after(err_msg, default_seconds=900)
                mark_exhausted_sync(session, cred_id, seconds=retry_after)
                log.info(
                    "ai.cred_exhausted",
                    cred_id=cred_id[:8],
                    provider=provider_name,
                    retry_after=retry_after,
                )
            except Exception as _exc:
                log.warning("router.mark_exhausted_failed", error=str(_exc)[:120])
            raise CredentialsExhaustedError(
                f"Credential rate-limited ({provider_name}): {err_msg[:200]}"
            )
        # Every other error (413, 400, 401, 500) is a real failure.
        # Do NOT rotate — a fresh credential won't fix a malformed request.
        raise ProviderError(err_msg)''',
        "match real rate-limit signatures only",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Verify
# ─────────────────────────────────────────────────────────────────────

def step3_verify() -> bool:
    print("\n[3/3] verify")

    for f in [VERIFIER, ROUTER]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ all parse")

    # Unit test the 429 detection logic
    print("\n  429 detection unit test")
    code = r'''
def is_rate_limit(err_msg: str) -> bool:
    m = err_msg.lower()
    return (
        "http 429" in m
        or "rate_limit_exceeded" in m
        or "tokens per minute" in m
        or "tpm" in m
        or "requests per minute" in m
        or "rpm" in m
    )

cases = [
    # Groq's real rate limit — should match
    ('Groq HTTP 429: {"error":{"message":"Rate limit reached for model openai/gpt-oss-120b...on tokens per minute (TPM): Limit 8000, Used 6354"}}', True),
    ('Groq HTTP 429: {"error":{"code":"rate_limit_exceeded"}}', True),
    # 413 payload too large — should NOT match
    ('Groq HTTP 413: {"error":{"message":"Request too large for model openai/gpt-oss-20b"}}', False),
    # 400 json validate — should NOT match
    ('Groq HTTP 400: {"error":{"message":"Failed to validate JSON","code":"json_validate_failed"}}', False),
    # 401 — should NOT match
    ('Groq HTTP 401: {"error":{"message":"Invalid API Key"}}', False),
    # Our own wrapper text on a 413 — should NOT match
    ('Credential rate-limited (groq): Groq HTTP 413: Request too large', False),
    # Ambiguous rate text without HTTP code — this is what we want to be careful about
    # but for now, tests pass because they don't contain the signatures
    ('Some other error mentioning the word rate', False),
]

ok = True
for msg, want in cases:
    got = is_rate_limit(msg)
    mark = "✓" if got == want else "❌"
    print(f"  {mark} match={got!s:5}  expected={want!s:5}  {msg[:70]!r}")
    if got != want:
        ok = False

raise SystemExit(0 if ok else 1)
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=15,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-300:])
        return False

    # Restart API + worker
    print("  restart API + worker")
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    subprocess.run(["pkill", "-9", "-f", "celery.*worker"], check=False)
    time.sleep(3)
    with Path("/tmp/signaliq-api.log").open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    with Path("/tmp/signaliq-worker.log").open("w") as f:
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

    # Live test: the exact 4-event input that hit 413
    print("\n  live chain build (the input that hit 413)")
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
         "headline": "Iranian MPs working on nuclear weapons",
         "summary": "Ghanfari said members of parliament are actively working to acquire nuclear weapons.",
         "occurred_at": "2026-09-10T12:00:00Z", "reported_at": None, "location": None,
         "actors": ["Iranian MPs"], "event_type": "statement", "status": "claim",
         "confidence": 0.7, "sources": [], "topics": []},
        {"_placeholder_id": "e2", "external_id": "e2",
         "headline": "Draft NPT withdrawal being prepared",
         "summary": "Parliamentarians are drafting a withdrawal of Iran from the NPT.",
         "occurred_at": "2026-09-11T12:00:00Z", "reported_at": None, "location": None,
         "actors": ["Iranian MPs"], "event_type": "statement", "status": "claim",
         "confidence": 0.7, "sources": [], "topics": []},
        {"_placeholder_id": "e3", "external_id": "e3",
         "headline": "Iran signals nuclear intent if threatened",
         "summary": "Iran publicly stated it may seek nuclear weapons if its existence is threatened.",
         "occurred_at": "2026-09-12T12:00:00Z", "reported_at": None, "location": None,
         "actors": ["Iran"], "event_type": "statement", "status": "claim",
         "confidence": 0.7, "sources": [], "topics": []},
        {"_placeholder_id": "e4", "external_id": "e4",
         "headline": "Netanyahu warns nuclear Iran means Israel's end",
         "summary": "Netanyahu said a nuclear-armed Iran would spell the end of the State of Israel.",
         "occurred_at": "2026-09-13T12:00:00Z", "reported_at": None, "location": None,
         "actors": ["Benjamin Netanyahu", "Israel"], "event_type": "statement", "status": "fact",
         "confidence": 0.95, "sources": [], "topics": []},
    ]
    result = await _build_chain_async(oid, events, "news", None, 1000)
    print(f"  status:              {result.get('status')}")
    print(f"  verification_status: {result.get('verification_status')}")
    print(f"  unsupported_facts:   {result.get('unsupported_facts')}")
    print(f"  words:               {result.get('words')}")

    if result.get("verification_status") == "error":
        print("  ❌ verify still failed")
        raise SystemExit(1)
    if result.get("verification_status") in ("verified", "unverified"):
        print(f"  ✓ verify ran ({result.get('verification_status')})")

    # Count claim types
    if result.get("post_id"):
        async with F() as s:
            await s.execute(text(f"SET app.current_org_id = '{oid}'"))
            await s.commit()
            rows = (await s.execute(text("""
                SELECT claim_type, COUNT(*) FROM post_evidence
                WHERE post_id = :pid GROUP BY claim_type ORDER BY claim_type
            """), {"pid": result["post_id"]})).all()
        print("  claim_types in post_evidence:")
        for r in rows:
            print(f"    {r[0]:15} {r[1]}")
        if not any(r[0] == "thesis" for r in rows):
            print("  ⚠ no 'thesis' claims classified (expected at least the opening sentence)")

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
    print("S.6.3.5a — verify payload size + 413 handling")
    print("=" * 70)

    step1_verifier()
    step2_router()
    ok = step3_verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.3.5a complete")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
