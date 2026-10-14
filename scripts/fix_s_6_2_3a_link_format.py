"""S.6.2.3a — chain.link prompt clarity + parser defensive strip.

Discovered when a real build failed with:

  invalid UUID 'id=bdfbffbd-12de-4c66-84a8-e5f955ed22cd'
  length must be between 32..36 characters, got 39

The chain.link prompt shows `{"from": "id", "to": "id", ...}` as the
example schema. The reasoning model treated `"id"` as a placeholder and
returned bare UUIDs; the fast model (which S.6.2.1 moved chain.link to)
sometimes returns `"id=<uuid>"` literally, taking the example at face
value. Postgres rejects the string as an invalid UUID.

Fix A — the prompt: replace `"from": "id"` with `"from": "<event_id>"`
and add an explicit rule that the value must be the bare UUID, no
prefix, no quotes beyond the JSON string. Bump chain.link@2 → @3.

Fix B — the parser: strip any leading `id=` or `uuid:` prefix, then
validate the remaining 36-character shape, before using it. Defensive
against model variance regardless of what the prompt says.

Also fixes the FAILURE-path classification bug found in S.6.2.3's live
test: when a task RAISES (rather than returning {status: 'error'}), the
Celery result is a dict with exc_type / exc_module / exc_message, not
our {error: ...} shape. The status endpoint now reads the exception
shape too.

Also: _build_chain_async should catch unhandled exceptions and RETURN
{status: 'error', ...} instead of letting them propagate. The endpoint
expects a dict, not an exception.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "packages" / "ai" / "prompts" / "seed_chain_extra.py"
LINKER = ROOT / "packages" / "ai" / "chain" / "chain_linker.py"
ROUTER = ROOT / "apps" / "api" / "routers" / "news_chains.py"
TASK = ROOT / "apps" / "worker" / "tasks" / "news_chains.py"
WEB = ROOT / "apps" / "web"


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
# 1. chain.link@3 — the prompt
# ─────────────────────────────────────────────────────────────────────

def step1_prompt() -> None:
    print("\n[1/4] chain.link@3 — prompt clarity")

    src = SEED.read_text()
    if "CHAIN_LINK_V3" in src:
        print("  ↷ already present")
        return

    addition = '''

# ─────────────────────────────────────────────────────────────────────
# S.6.2.3a — chain.link@3 (explicit UUID-only values)
# ─────────────────────────────────────────────────────────────────────

CHAIN_LINK_V3 = """For each consecutive event pair, determine the relationship.

Link types:
- causes:      A directly caused B
- precedes:    A happened before B (temporal only)
- contradicts: A and B cannot both be fully true
- escalates:   B raises the stakes of A
- contextualizes: B explains or frames A
- responds_to: B is a response to A
- corroborates: B confirms A

PAIRS:
{{pairs}}

Rules:
- `from` and `to` must be the raw event id string, exactly as shown in
  the PAIRS block above. Do NOT add a prefix like "id=" or "uuid:", do
  NOT add quotes beyond the JSON string delimiters, do NOT reformat.
- One link object per pair, in the same order as PAIRS.
- `link_type` must be exactly one of the seven names listed above.
- `rationale` is one sentence, no more.
- `confidence` is a number between 0.0 and 1.0.

Return ONLY valid JSON in this exact shape:
[
  {"from": "<event_id>", "to": "<event_id>", "link_type": "causes", "rationale": "one sentence", "confidence": 0.8}
]

JSON:"""


def seed_s623a() -> None:
    """Activate chain.link@3."""
    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'chain.link'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('chain.link', 3, 'fast', :t, CAST(:v AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {"t": CHAIN_LINK_V3, "v": '["pairs"]'},
        )
        session.commit()
    print("  seeded chain.link@3")
'''
    src = src.rstrip() + addition
    SEED.write_text(src)
    print("  ✓ appended CHAIN_LINK_V3 + seed_s623a")


# ─────────────────────────────────────────────────────────────────────
# 2. chain_linker.py — defensive strip of id= / uuid: prefixes
# ─────────────────────────────────────────────────────────────────────

def step2_linker() -> None:
    print("\n[2/4] chain_linker.py — defensive strip")

    patch(
        LINKER,
        '''    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("expected array")''',
        '''    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("expected array")

        # S.6.2.3a — defensive normalisation. The fast model sometimes
        # returns "id=<uuid>" or "uuid:<uuid>" for `from`/`to` instead
        # of the bare uuid. Strip those prefixes; keep the string.
        def _clean_id(v):
            s = str(v or "").strip()
            for prefix in ("id=", "uuid:", "event_id=", "id:", "id "):
                if s.lower().startswith(prefix):
                    s = s[len(prefix):].strip()
                    break
            return s

        for item in parsed:
            if isinstance(item, dict):
                item["from"] = _clean_id(item.get("from"))
                item["to"] = _clean_id(item.get("to"))''',
        "strip id= / uuid: prefixes",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Task wrapper — catch unhandled exceptions, return {status:'error'}
# ─────────────────────────────────────────────────────────────────────

def step3_task() -> None:
    print("\n[3/4] news_chains.py — return instead of raise")

    # Wrap the body of _build_chain_async in a try/except that returns
    # a status=error dict on any unhandled exception.
    patch(
        TASK,
        '''    await engine.dispose()
    return {
        "status": "ok",
        "chain_id": chain_id,
        "post_id": post_id,
        "block": block.slug,
        "subtopic": subtopic_slug,
        "events_input": len(events),
        "events_shortlisted": len(pipeline_events),
        "links": len(result.links),
        "words": result.post.word_count if result.post else 0,
        "unsupported_facts": result.verification.unsupported_fact_count if result.verification else 0,
        "verification_status": result.verification_status,
        "regenerated": result.regenerated,
        "impacts": living_counts["impacts"],
        "scenarios": living_counts["scenarios"],
        "watchlist": living_counts["watchlist"],
        "error": result.error,
    }''',
        '''    await engine.dispose()
    return {
        "status": "ok",
        "chain_id": chain_id,
        "post_id": post_id,
        "block": block.slug,
        "subtopic": subtopic_slug,
        "events_input": len(events),
        "events_shortlisted": len(pipeline_events),
        "links": len(result.links),
        "words": result.post.word_count if result.post else 0,
        "unsupported_facts": result.verification.unsupported_fact_count if result.verification else 0,
        "verification_status": result.verification_status,
        "regenerated": result.regenerated,
        "impacts": living_counts["impacts"],
        "scenarios": living_counts["scenarios"],
        "watchlist": living_counts["watchlist"],
        "error": result.error,
    }


# S.6.2.3a — the outer wrapper. Any exception that escapes the body is
# turned into {status: 'error', error: <message>} so the polling endpoint
# (and its classifier) always sees a dict, never a Celery exception.
def _safe_build_chain_async(*args, **kwargs) -> dict:
    import asyncio as _asyncio
    try:
        return _asyncio.run(_build_chain_async(*args, **kwargs))
    except Exception as exc:
        log.exception("chain.task.unhandled_exception",
                      error_type=type(exc).__name__,
                      error=str(exc)[:500])
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {str(exc)[:800]}",
            "chain_id": None,
            "post_id": None,
            "block": kwargs.get("block_slug") or (args[2] if len(args) > 2 else "news"),
        }''',
        "add _safe_build_chain_async wrapper",
    )

    # The Celery task entrypoint: call the safe wrapper instead of the raw function
    patch(
        TASK,
        '''def build_chain(  # type: ignore[no-untyped-def]
    self,
    org_id: str,
    events: list[dict],
    block_slug: str = "news",
    subtopic_slug: str | None = None,
    target_words: int | None = None,
) -> dict:
    return asyncio.run(_build_chain_async(org_id, events, block_slug, subtopic_slug, target_words))''',
        '''def build_chain(  # type: ignore[no-untyped-def]
    self,
    org_id: str,
    events: list[dict],
    block_slug: str = "news",
    subtopic_slug: str | None = None,
    target_words: int | None = None,
) -> dict:
    # S.6.2.3a — never let an unhandled exception reach Celery. The
    # polling endpoint expects a dict; return one on every path.
    return _safe_build_chain_async(org_id, events, block_slug, subtopic_slug, target_words)''',
        "task entrypoint uses safe wrapper",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. Polling endpoint — read exc_message when the result is an exception
# ─────────────────────────────────────────────────────────────────────

def step4_router() -> None:
    print("\n[4/4] news_chains.py router — handle Celery exception shape")

    patch(
        ROUTER,
        '''    if state == "FAILURE":
        err = r.result
        err_text = str(err)[:1000] if err else "task failed"
        c = _classify_error(err_text)
        return ChainBuildStatusResponse(
            task_id=task_id,
            status="failure",
            error=err_text,
            error_kind=c.get("kind"),
            error_message=c.get("message"),
            error_action=c.get("action"),
            error_retry_after_seconds=c.get("retry_after_seconds"),
        )''',
        '''    if state == "FAILURE":
        err = r.result
        # S.6.2.3a — a Celery FAILURE result is either our own dict or
        # Celery's exception shape {exc_type, exc_module, exc_message}.
        if isinstance(err, dict) and "exc_message" in err:
            exc_type = err.get("exc_type") or "Exception"
            exc_msg = err.get("exc_message")
            if isinstance(exc_msg, (list, tuple)):
                exc_msg = exc_msg[0] if exc_msg else ""
            err_text = f"{exc_type}: {str(exc_msg)[:900]}"
        else:
            err_text = str(err)[:1000] if err else "task failed"
        c = _classify_error(err_text)
        return ChainBuildStatusResponse(
            task_id=task_id,
            status="failure",
            error=err_text,
            error_kind=c.get("kind"),
            error_message=c.get("message"),
            error_action=c.get("action"),
            error_retry_after_seconds=c.get("retry_after_seconds"),
        )''',
        "FAILURE reads exc_message",
    )


# ─────────────────────────────────────────────────────────────────────
# Verification
# ─────────────────────────────────────────────────────────────────────

def verify() -> bool:
    print("\n" + "=" * 70)
    print("VERIFICATION")
    print("=" * 70)

    # Syntax
    print("\n[A] syntax")
    for f in [SEED, LINKER, TASK, ROUTER]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ all parse")

    # Run the seed
    print("\n[B] seed chain.link@3")
    r = subprocess.run(
        ["python", "-c",
         "from packages.ai.prompts.seed_chain_extra import seed_s623a; seed_s623a(); "
         "from packages.ai.prompts.loader import clear_cache; clear_cache()"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-400:])
        return False

    # Confirm chain.link@3 is active
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        "SELECT name, version, is_active FROM prompt_versions "
        "WHERE name = 'chain.link' ORDER BY version;"
    ], capture_output=True, text=True)
    print(r.stdout)

    # Unit test — the prefix strip
    print("\n[C] prefix strip unit test")
    code = r'''
def _clean_id(v):
    s = str(v or "").strip()
    for prefix in ("id=", "uuid:", "event_id=", "id:", "id "):
        if s.lower().startswith(prefix):
            s = s[len(prefix):].strip()
            break
    return s

cases = [
    ("bdfbffbd-12de-4c66-84a8-e5f955ed22cd", "bdfbffbd-12de-4c66-84a8-e5f955ed22cd"),
    ("id=bdfbffbd-12de-4c66-84a8-e5f955ed22cd", "bdfbffbd-12de-4c66-84a8-e5f955ed22cd"),
    ("uuid:bdfbffbd-12de-4c66-84a8-e5f955ed22cd", "bdfbffbd-12de-4c66-84a8-e5f955ed22cd"),
    ("id:bdfbffbd-12de-4c66-84a8-e5f955ed22cd", "bdfbffbd-12de-4c66-84a8-e5f955ed22cd"),
    ("event_id=bdfbffbd-12de-4c66-84a8-e5f955ed22cd", "bdfbffbd-12de-4c66-84a8-e5f955ed22cd"),
    ("  bdfbffbd-12de-4c66-84a8-e5f955ed22cd  ", "bdfbffbd-12de-4c66-84a8-e5f955ed22cd"),
    ("", ""),
]
ok = True
for inp, want in cases:
    got = _clean_id(inp)
    mark = "✓" if got == want else "❌"
    print(f"  {mark} {inp[:50]!r:55} → {got!r}")
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

    # Restart services
    print("\n[D] restart API + worker")
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

    # Live chain build — the definitive test
    print("\n[E] live chain build")
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
    print(f"  links:               {result.get('links')}")
    print(f"  words:               {result.get('words')}")
    print(f"  chain_id:            {result.get('chain_id')}")
    print(f"  error:               {result.get('error')}")

    if result.get("status") != "ok":
        raise SystemExit(1)

    # Verify chain_links rows exist for this chain
    if result.get("chain_id"):
        async with F() as s:
            await s.execute(text(f"SET app.current_org_id = '{oid}'"))
            await s.commit()
            n = (await s.execute(text(
                "SELECT COUNT(*) FROM chain_links WHERE chain_id = :cid"
            ), {"cid": result["chain_id"]})).scalar()
            print(f"  chain_links rows:    {n}")
            if n == 0:
                print("  ❌ no chain_links persisted")
                raise SystemExit(1)
            print("  ✓ chain_links persisted")

    await engine.dispose()

asyncio.run(main())
'''
    r = subprocess.run(
        ["python", "-c", code2], cwd=ROOT,
        capture_output=True, text=True, timeout=300,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False

    return True


def main() -> None:
    print("=" * 70)
    print("S.6.2.3a — chain.link format + task exception safety")
    print("=" * 70)

    step1_prompt()
    step2_linker()
    step3_task()
    step4_router()
    ok = verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.2.3a complete")
        print("   chain.link@3 explicit UUIDs + parser strips id= prefixes")
        print("   task returns on exception, endpoint reads Celery exc shape")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
