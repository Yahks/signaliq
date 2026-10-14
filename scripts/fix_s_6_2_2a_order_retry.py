"""S.6.2.2a — chain.order + chain.link: bigger max_tokens, one retry on
json_validate_failed.

S.6.2.1 moved chain.order and chain.link to the fast model. The fast
model sometimes fails Groq's JSON validator (failed_generation=''),
causing the whole chain build to fail. Observed intermittently: the
S.6.2.1 comparison run succeeded, the S.6.2.2 integration run failed
on chain.order.

Two mitigations:
  1. max_tokens 800 → 2000 for chain.order (1500 for chain.link) — gives
     the fast model headroom so a longer response isn't truncated.
  2. Wrap the call in a one-shot retry on ProviderError whose message
     contains 'json_validate_failed'. The model is stochastic; a second
     attempt usually parses.

If this doesn't hold up across runs, revert S.6.2.1 (chain.order and
chain.link back to the reasoning model).
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "packages" / "ai" / "chain" / "chain_builder.py"
LINKER = ROOT / "packages" / "ai" / "chain" / "chain_linker.py"


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


def step1_order() -> None:
    print("\n[1/2] patch order_chain")

    patch(
        BUILDER,
        '''    prompt, _pv = render_with_version("chain.order", block_slug=block_slug, events=_format_events(chain_events))
    raw = await complete(
        role="fast",       # S.6.2.1 — was "reasoning"; structured-output
                           # task, moves ~1,000 TPM off the 8k/min reasoning cap
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose="chain.order",
        temperature=0.2,
        max_tokens=800,
        json_mode=True,
        use_cache=use_cache,
    )''',
        '''    prompt, _pv = render_with_version("chain.order", block_slug=block_slug, events=_format_events(chain_events))

    # S.6.2.2a — the fast model occasionally fails Groq's JSON validator
    # (json_validate_failed with failed_generation=''). A single retry
    # almost always succeeds; the model is stochastic.
    raw = None
    for _attempt in range(2):
        try:
            raw = await complete(
                role="fast",       # S.6.2.1 — moves ~1,000 TPM off the
                                   # 8k/min reasoning cap
                system=None,
                user=prompt,
                org_id=org_id,
                prompt_version_id=_pv,
                purpose="chain.order",
                temperature=0.2,
                max_tokens=2000,   # S.6.2.2a — was 800; give the fast
                                   # model room so JSON isn't truncated
                json_mode=True,
                use_cache=use_cache,
            )
            break
        except Exception as _exc:
            msg = str(_exc)
            if "json_validate_failed" in msg or "Failed to validate JSON" in msg:
                if _attempt == 0:
                    log.warning("chain.order.retry",
                                reason="json_validate_failed")
                    continue
            raise
    if raw is None:
        raise RuntimeError("order_chain: retry loop exited without result")''',
        "order retry + max_tokens",
    )


def step2_link() -> None:
    print("\n[2/2] patch link_chain")

    patch(
        LINKER,
        '''    prompt, _pv = render_with_version("chain.link", block_slug=block_slug, pairs=_format_pairs(sequence, by_id))
    raw = await complete(
        role="fast",       # S.6.2.1 — was "reasoning"; 7-way classification
                           # task, moves ~1,300 TPM off the reasoning cap
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose="chain.link",
        temperature=0.2,
        max_tokens=2000,
        json_mode=True,
        use_cache=use_cache,
    )''',
        '''    prompt, _pv = render_with_version("chain.link", block_slug=block_slug, pairs=_format_pairs(sequence, by_id))

    # S.6.2.2a — same retry discipline as order_chain.
    raw = None
    for _attempt in range(2):
        try:
            raw = await complete(
                role="fast",       # S.6.2.1 — moves ~1,300 TPM off the
                                   # reasoning cap
                system=None,
                user=prompt,
                org_id=org_id,
                prompt_version_id=_pv,
                purpose="chain.link",
                temperature=0.2,
                max_tokens=2500,   # S.6.2.2a — was 2000; more headroom
                json_mode=True,
                use_cache=use_cache,
            )
            break
        except Exception as _exc:
            msg = str(_exc)
            if "json_validate_failed" in msg or "Failed to validate JSON" in msg:
                if _attempt == 0:
                    log.warning("chain.link.retry",
                                reason="json_validate_failed")
                    continue
            raise
    if raw is None:
        raise RuntimeError("link_chain: retry loop exited without result")''',
        "link retry + max_tokens",
    )


def step3_verify() -> bool:
    print("\n[3/3] verify with a fresh build")

    for f in [BUILDER, LINKER]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ syntax clean")

    subprocess.run(["pkill", "-9", "-f", "celery.*worker"], check=False)
    time.sleep(3)
    worker_log = Path("/tmp/signaliq-worker.log")
    with worker_log.open("w") as f:
        subprocess.Popen(
            ["celery", "-A", "apps.worker.celery_app", "worker",
             "-l", "info", "-Q", "default,ingestion", "--hostname=worker1@%h"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(8)

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
    print(f"  unsupported_facts:   {result.get('unsupported_facts')}")
    print(f"  words:               {result.get('words')}")
    print(f"  chain_id:            {result.get('chain_id')}")
    print(f"  error:               {result.get('error')}")

    if result.get("status") != "ok" or not result.get("chain_id"):
        raise SystemExit(1)

    await engine.dispose()

asyncio.run(main())
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=300,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-600:])
        return False

    # Check for retries in the log
    for logpath in ["/tmp/signaliq-worker.log"]:
        p = Path(logpath)
        if p.exists():
            for line in p.read_text().splitlines():
                if "chain.order.retry" in line or "chain.link.retry" in line:
                    print(f"  retry event: {line[:180]}")
    return True


def main() -> None:
    print("=" * 70)
    print("S.6.2.2a — order/link retry + bigger max_tokens")
    print("=" * 70)

    step1_order()
    step2_link()
    ok = step3_verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.2.2a complete — build succeeded with retry safety net")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
