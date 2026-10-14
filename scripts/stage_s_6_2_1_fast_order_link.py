"""S.6.2.1 — move chain.order and chain.link to the fast model.

Rationale: the reasoning model (gpt-oss-120b) has an 8,000 TPM limit. A
single chain build uses ~9,600 TPM on it. chain.order (~1,000) and
chain.link (~1,300) are ~2,300 of that — ~24%. Both are structured-output
classification tasks with tolerant parsers and graceful fallbacks; the
fast model (gpt-oss-20b, separate rate-limit pool) can do them.

Changes:
  - chain_builder.py order_chain(): role="reasoning" → role="fast"
  - chain_linker.py link_chain():  role="reasoning" → role="fast"
  - chain.order@1 → @2 and chain.link@1 → @2 (version bump marks the
    boundary so a future query can compare pre/post outputs)

Verification (per decisions Q1, Q2):
  - Runs one chain build BEFORE the change, records tokens + article
  - Applies the change
  - Runs the same chain build AFTER, records tokens + article
  - Compares reasoning-model tokens (should drop ~2,300)
  - Compares fast-model tokens (should rise ~2,300)
  - Prints both articles so we can read them side by side
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "packages" / "ai" / "chain" / "chain_builder.py"
LINKER = ROOT / "packages" / "ai" / "chain" / "chain_linker.py"
SEED = ROOT / "packages" / "ai" / "prompts" / "seed_chain_extra.py"


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
# The before/after build runner — shared by both phases
# ─────────────────────────────────────────────────────────────────────

BUILD_RUNNER = r'''
import asyncio, json, sys, time
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.core.context import current_user_id, current_org_id

async def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "before"
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

    # Mark the start time so we can filter ai_calls to just this build
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        t0 = (await s.execute(text("SELECT now()"))).scalar()

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
    post_id = result.get("post_id")

    # Fetch the article body
    body = None
    if post_id:
        async with F() as s:
            await s.execute(text(f"SET app.current_org_id = '{oid}'"))
            await s.commit()
            row = (await s.execute(text(
                "SELECT title, body, word_count, verification_status FROM generated_posts WHERE id = :pid"
            ), {"pid": post_id})).first()
            if row:
                body = {"title": row[0], "body": row[1], "word_count": row[2], "verification_status": row[3]}

    # Fetch the ai_calls for this build window
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        calls = (await s.execute(text("""
            SELECT purpose, model_id::text, input_tokens, output_tokens
            FROM ai_calls
            WHERE created_at >= :t0
              AND purpose LIKE 'chain.%'
            ORDER BY created_at
        """), {"t0": t0})).all()

    # Summarize by purpose + model
    from collections import defaultdict
    by_purpose = defaultdict(lambda: defaultdict(int))
    for c in calls:
        by_purpose[c[0]][c[1]] += (c[2] or 0) + (c[3] or 0)

    # Also summarize by model
    by_model = defaultdict(int)
    for c in calls:
        by_model[c[1]] += (c[2] or 0) + (c[3] or 0)

    print(f"\n=== PHASE: {phase} ===")
    print(f"chain_id: {result.get('chain_id')}")
    print(f"post_id:  {post_id}")
    print(f"status:   {result.get('status')}")
    print(f"verify:   {result.get('verification_status')}")
    print(f"unsupported: {result.get('unsupported_facts')}")
    print(f"words:    {result.get('words')}")
    print()
    print("per-purpose tokens:")
    for purpose in sorted(by_purpose):
        for model in sorted(by_purpose[purpose], key=lambda m: m or ""):
            model_str = model if model else "(none)"
            print(f"  {purpose:25} {model_str:38} {by_purpose[purpose][model]:6}")
    print()
    print("per-model totals:")
    for model in sorted(by_model, key=lambda m: m or ""):
        model_str = model if model else "(none)"
        print(f"  {model_str:38} {by_model[model]:6}")
    print()
    if body:
        print(f"ARTICLE TITLE: {body['title']}")
        print(f"ARTICLE WORDS: {body['word_count']}")
        print(f"VERIFY STATUS: {body['verification_status']}")
        print("ARTICLE BODY (first 1500 chars):")
        print("-----")
        print((body['body'] or "")[:1500])
        print("-----")

    await engine.dispose()

asyncio.run(main())
'''


def run_build(phase: str) -> bool:
    print(f"\n[run] chain build ({phase})")
    r = subprocess.run(
        ["python", "-c", BUILD_RUNNER, phase], cwd=ROOT,
        capture_output=True, text=True, timeout=300,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False
    return True


# ─────────────────────────────────────────────────────────────────────
# Steps
# ─────────────────────────────────────────────────────────────────────

def step1_before() -> bool:
    print("\n[1/4] BEFORE: build with current code (order + link on reasoning)")
    return run_build("before")


def step2_apply() -> None:
    print("\n[2/4] apply the change")

    patch(
        BUILDER,
        '''    raw = await complete(
        role="reasoning",
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose="chain.order",''',
        '''    raw = await complete(
        role="fast",       # S.6.2.1 — was "reasoning"; structured-output
                           # task, moves ~1,000 TPM off the 8k/min reasoning cap
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose="chain.order",''',
        "order_chain → fast",
    )

    patch(
        LINKER,
        '''    raw = await complete(
        role="reasoning",
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose="chain.link",''',
        '''    raw = await complete(
        role="fast",       # S.6.2.1 — was "reasoning"; 7-way classification
                           # task, moves ~1,300 TPM off the reasoning cap
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose="chain.link",''',
        "link_chain → fast",
    )

    # Version bumps for chain.order and chain.link
    src = SEED.read_text()
    if "seed_s621" in src:
        print("  ↷ version bumps already applied")
        return

    addition = '''

# ─────────────────────────────────────────────────────────────────────
# S.6.2.1 — bump chain.order / chain.link to v2 (model role change)
# ─────────────────────────────────────────────────────────────────────

def seed_s621() -> None:
    """Re-activate chain.order and chain.link at @2 with the same text.

    The text didn't change; the version bump marks the boundary where the
    model moved from reasoning to fast, so a future query can compare
    pre/post outputs and success rates.
    """
    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        for name, role in [("chain.order", "fast"), ("chain.link", "fast")]:
            # Fetch the current active template
            row = session.execute(
                sql_text("SELECT template FROM prompt_versions "
                         "WHERE name = :n AND is_active = true LIMIT 1"),
                {"n": name},
            ).first()
            if not row:
                print(f"  ⚠ no active {name} — skipping")
                continue
            tpl = row[0]

            session.execute(
                sql_text("UPDATE prompt_versions SET is_active = false WHERE name = :n"),
                {"n": name},
            )
            session.execute(
                sql_text("""
                    INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                    VALUES (:n, 2, :r, :t, '[]'::jsonb, true)
                    ON CONFLICT (name, version) DO UPDATE
                    SET template = EXCLUDED.template,
                        role = EXCLUDED.role,
                        is_active = true
                """),
                {"n": name, "r": role, "t": tpl},
            )
        session.commit()
    print("  seeded chain.order@2 and chain.link@2 (role=fast)")


if __name__ == "__main__":
    # NOTE: this __main__ block is overwritten by the previous seeding
    # scripts' pattern — kept here so seed_s621() runs when imported.
    pass
'''
    src = src.rstrip() + addition
    SEED.write_text(src)
    print("  ✓ added seed_s621")


def step3_after() -> bool:
    print("\n[3/4] AFTER: build with fast model for order + link")

    # Run the seed to activate v2
    r = subprocess.run(
        ["python", "-c",
         "from packages.ai.prompts.seed_chain_extra import seed_s621; seed_s621(); "
         "from packages.ai.prompts.loader import clear_cache; clear_cache()"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-400:])
        return False

    # Restart the API and worker so the new code is loaded
    print("  restarting API + worker")
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

    return run_build("after")


def main() -> None:
    print("=" * 70)
    print("S.6.2.1 — move chain.order + chain.link to the fast model")
    print("=" * 70)

    ok_before = step1_before()
    if not ok_before:
        print("\n❌ BEFORE build failed — stopping")
        sys.exit(1)

    step2_apply()

    ok_after = step3_after()
    if not ok_after:
        print("\n❌ AFTER build failed — see output")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("S.6.2.1 applied and built — compare the two phases above")
    print()
    print("What to look for:")
    print("  1. The 'after' build's per-model totals show chain.order and")
    print("     chain.link moved to the fast-model id (not c029d4c4-...).")
    print("  2. Reasoning-model total dropped by ~2,300 tokens.")
    print("  3. The article (title, words, verify status) is comparable.")
    print("=" * 70)


if __name__ == "__main__":
    main()
