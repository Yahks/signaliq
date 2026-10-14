"""S.6.1b.1b.1 — fix chain.verify so the 20-claim cap actually holds.

The root cause of the persistent json_validate_failed:

  chain.verify@3 told the model "For each sentence in the body:" AND
  "Return AT MOST 20 claims". The model followed the first instruction,
  produced ~40 claim objects for a 794-word body, hit max_tokens mid-array,
  and Groq's JSON validator rejected the truncated output with
  failed_generation='' (empty — the validator discarded it).

  Isolated tests passed because their bodies were short (250 words → ~15 claims).

Fix has three layers:
  1. chain.verify@4: rewrite the prompt so the cap is unmissable and the
     per-sentence instruction is subordinated. The model is told to extract
     the TOP 20 and explicitly NOT audit every sentence.
  2. max_tokens 4000 → 8000 for the verify call only. Headroom so even if
     the model ignores the prompt, the output completes as valid JSON.
  3. Parser slices parsed[:20] after json.loads. Hard ceiling regardless
     of what the model produces.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_EXTRA = ROOT / "packages" / "ai" / "prompts" / "seed_chain_extra.py"
VERIFIER = ROOT / "packages" / "ai" / "chain" / "verifier.py"


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
    src = src.replace(old, new, 1)
    path.write_text(src)
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. chain.verify@4 — the corrected prompt
# ─────────────────────────────────────────────────────────────────────

VERIFY_V4 = '''You are an evidence auditor.

POST BODY:
{{body}}

EVIDENCE (events and their sources):
{{evidence}}

TASK: Extract and audit the TOP 20 most important factual claims in the body.

Do NOT audit every sentence — you will run out of output space and the
response will be rejected. A partial audit of 20 claims is better than a
truncated audit of 40.

Prioritise in this exact order, stopping at 20:
  1. Claims of type "fact" that contain specific numbers, dates, or named entities
  2. Other claims of type "fact"
  3. Claims of type "inference"

Skip claims of type "recommendation" and "teaching" entirely.

For each extracted claim:
- Classify as: fact | inference | hypothesis | recommendation
- If fact: list the event id(s) that support it. If none, set "unsupported": true.
- If inference: list the event ids it is inferred FROM.
- Set confidence between 0.0 and 1.0.

HARD LIMIT: Return at most 20 claim objects. If you would produce more,
stop at 20 and omit the rest. Do not exceed 20.

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


def step1_seed_v4() -> None:
    print("\n[1/3] seed chain.verify@4")

    src = SEED_EXTRA.read_text()
    if "CHAIN_VERIFY_V4" in src:
        print("  ↷ already present")
        return

    addition = f'''

# ─────────────────────────────────────────────────────────────────────
# S.6.1b.1b.1 — chain.verify@4 (cap actually enforced in the prompt)
# ─────────────────────────────────────────────────────────────────────

CHAIN_VERIFY_V4 = """{VERIFY_V4}"""


def seed_s61b_1b_1() -> None:
    """Activate chain.verify@4 with the corrected 20-claim cap."""
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
                VALUES ('chain.verify', 4, 'fast', :template, CAST(:vars AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {{
                "template": CHAIN_VERIFY_V4,
                "vars": json.dumps(["body", "evidence"]),
            }},
        )
        session.commit()
    print("  seeded chain.verify@4")


if __name__ == "__main__":
    seed()
    seed_s61b_1()
    seed_s61b_1b()
    seed_s61b_1b_1()
'''
    src = src.rstrip() + addition
    SEED_EXTRA.write_text(src)
    print("  ✓ appended CHAIN_VERIFY_V4 + seed_s61b_1b_1")


# ─────────────────────────────────────────────────────────────────────
# 2. verifier.py — max_tokens 8000 + parser cap
# ─────────────────────────────────────────────────────────────────────

def step2_verifier() -> None:
    print("\n[2/3] verifier.py — max_tokens + parser cap")

    # 2a. max_tokens 4000 → 8000
    patch(
        VERIFIER,
        '''    log.info(
        "chain.verify.call",
        body_chars=len(body),
        prompt_chars=len(prompt),
        max_tokens=4000,
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
        max_tokens=4000,   # was 2000 — truncated on bodies > ~400 words
        json_mode=True,
        use_cache=use_cache,
    )''',
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
        "max_tokens 4000 → 8000",
    )

    # 2b. parser ceiling — slice parsed[:20] before the loop
    patch(
        VERIFIER,
        '''    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("expected array")
    except (json.JSONDecodeError, ValueError):
        log.warning("chain.verify.parse_failed", snippet=raw[:200])
        return VerificationResult(claims=[], unsupported_fact_count=0)

    valid_ids = {e["id"] for e in events}''',
        '''    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("expected array")
    except (json.JSONDecodeError, ValueError):
        log.warning("chain.verify.parse_failed", snippet=raw[:200])
        return VerificationResult(claims=[], unsupported_fact_count=0)

    # Hard ceiling — the prompt asks for ≤20; enforce it even if the model
    # produces more. Extra claims past #20 are dropped, not parsed.
    if len(parsed) > 20:
        log.info("chain.verify.parser_capped", produced=len(parsed), kept=20)
        parsed = parsed[:20]

    valid_ids = {e["id"] for e in events}''',
        "parser cap at 20",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Run seeds + verify end-to-end
# ─────────────────────────────────────────────────────────────────────

def step3_apply_verify() -> bool:
    print("\n[3/3] apply + verify")

    # Syntax check
    for f in [SEED_EXTRA, VERIFIER]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ syntax clean")

    # Run the seeds
    print("  running seeds")
    r = subprocess.run(
        ["python", "-c",
         "from packages.ai.prompts.seed_chain_extra import "
         "seed, seed_s61b_1, seed_s61b_1b, seed_s61b_1b_1; "
         "seed(); seed_s61b_1(); seed_s61b_1b(); seed_s61b_1b_1(); "
         "from packages.ai.prompts.loader import clear_cache; clear_cache()"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-400:])
        return False

    # Confirm chain.verify@4 is active
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        "SELECT name, version, is_active FROM prompt_versions "
        "WHERE name = 'chain.verify' ORDER BY version;"
    ], capture_output=True, text=True)
    print(r.stdout)

    # Run the same chain build and check verification_status
    print("  live chain build")
    code = r'''
import asyncio, json, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    F = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    from packages.db.session import get_owner_session_factory
    OF = get_owner_session_factory()
    async with OF() as s:
        row = (await s.execute(text("""
            SELECT o.id::text FROM orgs o
            JOIN memberships m ON m.org_id = o.id
            JOIN users u ON u.id = m.user_id
            WHERE u.username = 'tokentest' LIMIT 1
        """))).first()
    oid = row[0]

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
    print(f"  status:               {result.get('status')}")
    print(f"  verification_status:  {result.get('verification_status')}")
    print(f"  regenerated:          {result.get('regenerated')}")
    print(f"  unsupported_facts:    {result.get('unsupported_facts')}")
    print(f"  post_id:              {result.get('post_id')}")
    print(f"  words:                {result.get('words')}")

    if result.get("post_id"):
        async with F() as s:
            await s.execute(text(f"SET app.current_org_id = '{oid}'"))
            await s.commit()
            r = (await s.execute(text(
                "SELECT status, verification_status FROM generated_posts WHERE id = :pid"
            ), {"pid": result["post_id"]})).first()
        if r:
            print(f"  DB: status={r[0]}  verification_status={r[1]}")

    await engine.dispose()

asyncio.run(main())
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=300,
    )
    print(r.stdout[-1500:])
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False

    # Assert the goal: verification_status must be verified or unverified, not error
    ok = (
        "verification_status:  verified" in r.stdout
        or "verification_status:  unverified" in r.stdout
    )
    if ok:
        print("  ✓ verify ran successfully (status is verified or unverified)")
    else:
        print("  ❌ verify still failing — see the log above")
    return ok


def main() -> None:
    print("=" * 70)
    print("S.6.1b.1b.1 — fix chain.verify so the 20-claim cap holds")
    print("=" * 70)

    step1_seed_v4()
    step2_verifier()
    ok = step3_apply_verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.1b.1b.1 complete")
        print("   chain.verify@4 caps at 20 in the prompt, 8000 token headroom,")
        print("   parser slices to 20 regardless. verification_status is set.")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
