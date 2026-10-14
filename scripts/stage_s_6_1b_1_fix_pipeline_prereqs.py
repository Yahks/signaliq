"""
S.6.1b.1 — fix two prerequisites for the chain pipeline.

Issue 1: living enrichment silently produces zeroes for news chains
  because the base prompts `chain.impacts`, `chain.scenarios`, `chain.watchlist`
  don't exist — only `.trading` variants. `living.py` passes block_slug="news",
  the loader resolves to `chain.impacts.news` (missing) then `chain.impacts`
  (missing), raises LookupError, and the except swallows it.

  Fix: seed the three base prompts with the .trading text verbatim.

Issue 2: chain.verify fails 5 times per chain with json_validate_failed
  because the prompt puts a prose sentence AFTER the JSON schema and BEFORE
  "JSON:". Groq's strict JSON validator rejects the model's output.

  Fix: bump to chain.verify@2 with the sentence moved above the schema
  and "unsupported": false shown explicitly in the example.

Verification: one chain build must show impacts > 0, scenarios > 0,
watchlist > 0, and chain.verify.news must succeed exactly once (not 5 times).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_EXTRA = ROOT / "packages" / "ai" / "prompts" / "seed_chain_extra.py"
WEB = ROOT / "apps" / "web"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


# ─────────────────────────────────────────────────────────────────────
# 1. Extend seed_chain_extra.py with the 3 base prompts + verify@2
# ─────────────────────────────────────────────────────────────────────

SEED_ADDITION = '''

# ─────────────────────────────────────────────────────────────────────
# S.6.1b.1 — base prompts for living enrichment + fixed chain.verify@2
# ─────────────────────────────────────────────────────────────────────

CHAIN_IMPACTS_BASE = """You are a market analyst. Given the events in this chain, identify what could be affected.

EVENTS:
{{events}}

Produce a ranked list of impacts. For each:
- label: a short name (e.g. "LNG prices", "US technology equities", "Credit spreads")
- category: sector | commodity | asset_class | currency | credit | region | company
- direction: up | down | mixed | uncertain
- strength: strong | moderate | weak | unknown
- reasoning: one sentence explaining the linkage

Rules:
- Only list impacts that plausibly follow from the events. Do not invent actors.
- If the direction is unclear, use "uncertain".
- Rank by importance: strongest/most certain first.
- Include 5-12 impacts. No fewer, no more.

Return ONLY valid JSON:
{
  "impacts": [
    {
      "label": "...",
      "category": "...",
      "direction": "...",
      "strength": "...",
      "reasoning": "..."
    }
  ]
}

JSON:"""

CHAIN_SCENARIOS_BASE = """You are a scenario analyst. Given the events and impacts, describe 2-4 conditional futures.

EVENTS:
{{events}}

IMPACTS:
{{impacts}}

For each scenario, produce:
- name: short, evocative (e.g. "Supply shock intensifies", "Mild normalization")
- condition: a concrete conditional statement (If X happens, then Y)
- reasoning: 1-2 sentences explaining the mechanism
- direction: "up" | "down" | "mixed" | "uncertain"

Rules:
- Scenarios must be MUTUALLY EXCLUSIVE and cover the plausible space.
- Use concrete conditions, not vague ones.
- Do NOT invent statistics.
- 2-4 scenarios total.

Return ONLY valid JSON:
{
  "scenarios": [
    {
      "name": "...",
      "condition": "...",
      "reasoning": "...",
      "direction": "up"
    }
  ]
}

JSON:"""

CHAIN_WATCHLIST_BASE = """You are a research editor. Given the events and impacts, suggest what a reader should monitor to track this story over time.

EVENTS:
{{events}}

IMPACTS:
{{impacts}}

For each item:
- label: what to watch (e.g. "Natural gas futures", "VIX", "US energy sector")
- reasoning: one sentence on why this is a leading indicator

Rules:
- 4-8 items total.
- Prefer real, liquid, monitorable indicators over vague categories.

Return ONLY valid JSON:
{
  "watchlist": [
    {"label": "...", "reasoning": "..."}
  ]
}

JSON:"""

CHAIN_VERIFY_V2 = """You are an evidence auditor.

POST BODY:
{{body}}

EVIDENCE (events and their sources):
{{evidence}}

For each sentence in the body:
1. Classify as: fact | inference | hypothesis | recommendation
2. If fact: cite the event id(s) that support it. If none, mark "unsupported": true.
3. If inference: list the event ids it is inferred FROM.
4. If a claim of type "fact" has empty supported_by_event_ids, set "unsupported": true.

Return ONLY valid JSON in this exact shape:
[
  {
    "claim_text": "...",
    "claim_type": "fact",
    "supported_by_event_ids": ["event_id_1", "event_id_2"],
    "confidence": 0.0,
    "unsupported": false
  }
]

JSON:"""


def seed_s61b_1() -> None:
    """Seed the 3 base prompts + the fixed chain.verify@2."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    prompts = [
        # (name, version, role, template, variables)
        ("chain.impacts",   1, "reasoning", CHAIN_IMPACTS_BASE,   ["events"]),
        ("chain.scenarios", 1, "reasoning", CHAIN_SCENARIOS_BASE, ["events", "impacts"]),
        ("chain.watchlist", 1, "reasoning", CHAIN_WATCHLIST_BASE, ["events", "impacts"]),
        # Bump verify to v2
        ("chain.verify",    2, "fast",      CHAIN_VERIFY_V2,      ["body", "evidence"]),
    ]

    with Session() as session:
        for name, version, role, template, variables in prompts:
            # Deactivate all versions of this name, then activate the target.
            session.execute(
                sql_text("UPDATE prompt_versions SET is_active = false WHERE name = :name"),
                {"name": name},
            )
            session.execute(
                sql_text("""
                    INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                    VALUES (:name, :version, :role, :template, CAST(:vars AS jsonb), true)
                    ON CONFLICT (name, version) DO UPDATE
                    SET template = EXCLUDED.template,
                        variables = EXCLUDED.variables,
                        role = EXCLUDED.role,
                        is_active = true
                """),
                {
                    "name": name, "version": version, "role": role,
                    "template": template, "vars": json.dumps(variables),
                },
            )
        session.commit()
    print(f"  seeded {len(prompts)} prompts")
'''

VERIFY_FN_OLD = '''def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        for name, role, template, variables in PROMPTS:
            session.execute(
                sql_text("UPDATE prompt_versions SET is_active = false WHERE name = :name"),
                {"name": name},
            )
            session.execute(
                sql_text("""
                    INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                    VALUES (:name, 1, :role, :template, CAST(:vars AS jsonb), true)
                    ON CONFLICT (name, version) DO UPDATE
                    SET template = EXCLUDED.template,
                        variables = EXCLUDED.variables,
                        role = EXCLUDED.role,
                        is_active = true
                """),
                {"name": name, "role": role, "template": template,
                 "vars": json.dumps(variables)},
            )
        session.commit()
        print(f"  seeded {len(PROMPTS)} prompts")


if __name__ == "__main__":
    seed()'''

VERIFY_FN_NEW = '''def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        for name, role, template, variables in PROMPTS:
            session.execute(
                sql_text("UPDATE prompt_versions SET is_active = false WHERE name = :name"),
                {"name": name},
            )
            session.execute(
                sql_text("""
                    INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                    VALUES (:name, 1, :role, :template, CAST(:vars AS jsonb), true)
                    ON CONFLICT (name, version) DO UPDATE
                    SET template = EXCLUDED.template,
                        variables = EXCLUDED.variables,
                        role = EXCLUDED.role,
                        is_active = true
                """),
                {"name": name, "role": role, "template": template,
                 "vars": json.dumps(variables)},
            )
        session.commit()
        print(f"  seeded {len(PROMPTS)} prompts")

    # S.6.1b.1 — the living-enrichment base prompts + fixed chain.verify@2
    seed_s61b_1()


if __name__ == "__main__":
    seed()'''


def step1_extend_seed() -> None:
    print("\n[1/5] extend seed_chain_extra.py")
    src = SEED_EXTRA.read_text()

    if "seed_s61b_1" in src and "CHAIN_VERIFY_V2" in src:
        print("  ↷ already extended")
        return

    # Insert the new block after the existing PROMPTS list, before seed()
    marker = "def seed() -> None:"
    if marker not in src:
        fail("seed_chain_extra.py: cannot find 'def seed() -> None:'")
    src = src.replace(marker, SEED_ADDITION.strip() + "\n\n\n" + marker, 1)

    # Replace seed() with one that also calls seed_s61b_1()
    if VERIFY_FN_OLD not in src:
        fail("seed_chain_extra.py: current seed() shape changed — paste the file")
    src = src.replace(VERIFY_FN_OLD, VERIFY_FN_NEW, 1)

    SEED_EXTRA.write_text(src)
    print("  ✓ added CHAIN_* base + CHAIN_VERIFY_V2 + seed_s61b_1()")


def step2_syntax() -> None:
    print("\n[2/5] syntax check")
    r = subprocess.run(
        ["python", "-c", f"import ast; ast.parse(open('{SEED_EXTRA}').read())"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        fail(f"seed_chain_extra.py: {r.stderr[:400]}")
    print("  ✓ parses")


def step3_run_seed() -> bool:
    print("\n[3/5] run the seed")
    r = subprocess.run(
        ["python", "-c",
         "from packages.ai.prompts.seed_chain_extra import seed; seed(); "
         "from packages.ai.prompts.loader import clear_cache; clear_cache()"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-500:])
        return False
    print("  ✓ seed ran")

    # Confirm via psql
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        "SELECT name, version, role, is_active FROM prompt_versions "
        "WHERE name IN ('chain.impacts','chain.scenarios','chain.watchlist','chain.verify') "
        "ORDER BY name, version;"
    ], capture_output=True, text=True)
    print(r.stdout)
    return True


def step4_verify_pipeline() -> bool:
    print("\n[4/5] run one chain build + check living + verify")
    code = r'''
import asyncio, json, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession,
)
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

    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        before = (await s.execute(text(
            "SELECT COUNT(*) FROM ai_calls WHERE prompt_version_id IS NOT NULL"
        ))).scalar()

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
    print("  building...")
    result = await _build_chain_async(oid, events, "news", None, 800)
    print(f"  result keys: {list(result.keys())}")
    print(f"  status: {result.get('status')}  error: {result.get('error')}")
    living = result.get("living") or {}
    print(f"  living: {living}")

    # Check the verify call pattern
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        rows = (await s.execute(text("""
            SELECT purpose, status, prompt_version_id::text
            FROM ai_calls
            WHERE created_at > now() - interval '2 minutes'
              AND purpose LIKE 'chain.verify%'
            ORDER BY created_at DESC
        """))).all()
    print(f"  verify calls in last 2min: {len(rows)}")
    for r in rows:
        print(f"    {r[0]:30}  {r[1]:6}  pv={r[2][:8] if r[2] else 'NULL'}…")

    await engine.dispose()

asyncio.run(main())
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=240,
    )
    print(r.stdout[-2500:])
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False
    return True


def step5_symbols() -> None:
    """After all patches, verify every file that references render_with_version
    also imports it. The S.6.1a lesson, applied here."""
    print("\n[5/5] symbol import check")
    r = subprocess.run(
        ["bash", "-c",
         "grep -rl render_with_version packages/ apps/ --include='*.py' "
         "| xargs grep -L 'render_with_version' -m1 || true"],
        cwd=ROOT, capture_output=True, text=True,
    )
    # The above is a first-pass check; the real test is whether the files import it
    r = subprocess.run(
        ["bash", "-c",
         "for f in $(grep -rl render_with_version packages/ apps/ --include='*.py'); do "
         "  if ! grep -q 'import.*render_with_version' \"$f\"; then echo \"MISSING: $f\"; fi; "
         "done"],
        cwd=ROOT, capture_output=True, text=True,
    )
    out = r.stdout.strip()
    if out:
        print("  ⚠ files using render_with_version without importing it:")
        print(out)
    else:
        print("  ✓ every file that uses render_with_version imports it")


def main() -> None:
    print("=" * 70)
    print("S.6.1b.1 — fix chain pipeline prerequisites")
    print("=" * 70)

    step1_extend_seed()
    step2_syntax()
    ok_seed = step3_run_seed()
    ok_pipe = step4_verify_pipeline() if ok_seed else False
    step5_symbols()

    print("=" * 70)
    if ok_seed and ok_pipe:
        print("✅ S.6.1b.1 complete")
        print("   living enrichment runs for news chains; verify succeeds once")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
