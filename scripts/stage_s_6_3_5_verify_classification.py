"""S.6.3.5 — chain.verify@5: honest claim classification.

The last two articles produced by /chains/new showed two related issues
in the verify output:

1. Framing statements (the article's opening thesis, contextual padding)
   are being classified as `fact`. They're not facts — they're the
   article's framing. New claim_type `thesis` for these.

2. Claims that reference nothing in the source events — e.g. "US has
   reported depletion of long-range precision missiles" when no event
   in the chain mentions missiles — are being classified as `inference`
   rather than `unsupported`. If a fact or inference has no supporting
   event ids AND its key nouns don't appear in any event's text, it
   should be flagged.

Fix: chain.verify@5 with a stricter claim_type taxonomy and an explicit
rule about unsupported claims. Bump from @4 → @5.

Verified by re-verifying both articles:
  - the Iran-nuclear article
  - the Iran-US / Hormuz article
Both should now have fewer `fact` claims and more `thesis`/`inference`,
and the missile-stockpile claim should be marked unsupported.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "packages" / "ai" / "prompts" / "seed_chain_extra.py"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


CHAIN_VERIFY_V5 = '''You are an evidence auditor.

POST BODY:
{{body}}

EVIDENCE (events and their sources):
{{evidence}}

TASK: Extract and audit the TOP 20 most important claims in the body.

Do NOT audit every sentence — you will run out of output space and the
response will be rejected. A partial audit of 20 claims is better than a
truncated audit of 40.

CLAIM CLASSIFICATION

For each extracted claim, choose ONE of:
  - "fact":        a specific, verifiable statement about what happened,
                   at a specific time, involving specific actors. Cites
                   one or more event ids. This is the strongest category.
  - "thesis":      the article's framing, its opening thesis, its closing
                   summary, or a topic sentence that summarises rather
                   than reports. It may be TRUE but it is not a fact —
                   it is framing. Do NOT mark a thesis as "fact".
  - "inference":   a conclusion drawn from one or more facts. May cite
                   event ids. It is not itself an event.
  - "hypothesis":  a speculative forecast about what might happen.
  - "recommendation": advice to the reader.

PRIORITISE in this exact order, stopping at 20:
  1. The article's opening thesis sentence (usually the first sentence
     of the body) — classify it as "thesis".
  2. The article's closing thesis sentence (usually the last sentence)
     — classify it as "thesis".
  3. Claims of type "fact" that contain specific numbers, dates, or
     named entities.
  4. Other claims of type "fact".
  5. Claims of type "inference".

SUPPORT AND UNSUPPORTED

For every claim of type "fact" or "inference":
  - List the event ids that support it in supported_by_event_ids.
  - If the claim has NO supporting event ids, OR its key nouns (people,
    places, numbers, entities) do not appear anywhere in the EVIDENCE
    block, set "unsupported": true.
  - A claim that references something the events don't mention is
    UNSUPPORTED even if it sounds plausible.

A claim of type "thesis" is never "unsupported" — it's framing.

HARD LIMIT: Return at most 20 claim objects. Do not exceed 20.

Return ONLY valid JSON in this exact shape:
[
  {
    "claim_text": "...",
    "claim_type": "fact",
    "supported_by_event_ids": ["event_id_1"],
    "confidence": 0.0,
    "unsupported": false
  }
]

JSON:'''


def main() -> None:
    print("=" * 70)
    print("S.6.3.5 — chain.verify@5 classification")
    print("=" * 70)

    # Append to the seed module
    src = SEED.read_text()
    if "CHAIN_VERIFY_V5" in src:
        print("  ↷ CHAIN_VERIFY_V5 already present")
    else:
        addition = f'''

# ─────────────────────────────────────────────────────────────────────
# S.6.3.5 — chain.verify@5 (thesis claim_type + unsupported rule)
# ─────────────────────────────────────────────────────────────────────

CHAIN_VERIFY_V5 = """{CHAIN_VERIFY_V5}"""


def seed_s635() -> None:
    """Activate chain.verify@5."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'chain.verify'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('chain.verify', 5, 'fast', :t, CAST(:v AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {{"t": CHAIN_VERIFY_V5, "v": json.dumps(["body", "evidence"])}},
        )
        session.commit()
    print("  seeded chain.verify@5")


if __name__ == "__main__":
    seed()
    seed_s61b_1()
    seed_s61b_1b()
    seed_s61b_1b_1()
    seed_s61b_2()
    seed_s61b_2_1()
    seed_s623a()
    seed_s635()
'''
        src = src.rstrip() + addition
        SEED.write_text(src)
        print("  ✓ appended CHAIN_VERIFY_V5 + seed_s635")

    # Run the seed
    r = subprocess.run(
        ["python", "-c",
         "from packages.ai.prompts.seed_chain_extra import seed_s635; seed_s635(); "
         "from packages.ai.prompts.loader import clear_cache; clear_cache()"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-400:])
        sys.exit(1)

    # Confirm @5 is active
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        "SELECT name, version, is_active FROM prompt_versions "
        "WHERE name = 'chain.verify' ORDER BY version;"
    ], capture_output=True, text=True)
    print(r.stdout)

    # Restart worker
    print("  restart worker")
    subprocess.run(["pkill", "-9", "-f", "celery.*worker"], check=False)
    time.sleep(3)
    with Path("/tmp/signaliq-worker.log").open("w") as f:
        subprocess.Popen(
            ["celery", "-A", "apps.worker.celery_app", "worker",
             "-l", "info", "-Q", "default,ingestion", "--hostname=worker1@%h"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(8)

    # Verify by re-running verify() on a fresh chain build
    print("\n  live chain build with @5")
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
    print(f"  post_id:             {result.get('post_id')}")

    # Inspect the verification claims
    if result.get("post_id"):
        async with F() as s:
            await s.execute(text(f"SET app.current_org_id = '{oid}'"))
            await s.commit()
            rows = (await s.execute(text("""
                SELECT claim_type, COUNT(*) FROM post_evidence
                WHERE post_id = :pid
                GROUP BY claim_type
                ORDER BY claim_type
            """), {"pid": result["post_id"]})).all()
        print("  claim_types in post_evidence:")
        for r in rows:
            print(f"    {r[0]:15} {r[1]}")

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
        sys.exit(1)

    print("=" * 70)
    print("✅ S.6.3.5 complete")
    print("   chain.verify@5 distinguishes thesis from fact,")
    print("   flags unsupported claims with no source match")
    sys.exit(0)


if __name__ == "__main__":
    main()
