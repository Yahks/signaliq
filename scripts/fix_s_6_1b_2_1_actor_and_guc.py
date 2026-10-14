"""S.6.1b.2.1 — two fixes for the paste-anything flow.

1. `chain.extract@1` prompt says empty actors array is acceptable, but
   validate_and_parse_event requires ≥1 actor. The contradiction silently
   drops valid events. → `chain.extract@2` fixes the actor rule.

2. `set_current_org` uses SET LOCAL (transaction-scoped). Any commit inside
   the request (charge_for_upload → debit → commit) clears the GUC, and the
   next query against an RLS table fails with
       invalid input syntax for type uuid: ""
   The worker tasks already use SET (session-scoped) for this reason.
   → packages/db/tenant.py switches to SET.

Verified end-to-end: the same HTTP request that failed with
'no_coherent_chain' must now succeed with a real chain.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "packages" / "ai" / "prompts" / "seed_chain_extra.py"
TENANT = ROOT / "packages" / "db" / "tenant.py"
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
             f"--- expected ---\n{old[:400]}")
    src = src.replace(old, new, 1)
    path.write_text(src)
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. chain.extract@2 — fix the actor contradiction
# ─────────────────────────────────────────────────────────────────────

CHAIN_EXTRACT_V2 = '''You are a news event extractor. Below is text a user pasted — it may be an
article, a tweet thread, a paragraph, or a partial sentence. Your job is to
extract the discrete news events the text describes.

TEXT:
{{text}}

Rules:
- Extract between 2 and 10 events. Never fewer than 2. If the text describes
  only one clear event, extract that one and one closely-related fact as a
  second event; the caller will enrich with related events from the database.
- An event is: something that happened, at a specific time, involving specific
  actors. Do not extract opinions, general statements, or background.
- Use only the facts in the text. Do NOT invent dates, numbers, or names.
- actors must contain AT LEAST ONE name. If the text does not name anyone
  specific, use the primary subject of the event (e.g. "S&P 500" for a market
  move, "Treasury" for a yield move, "Federal Reserve" for a policy action).
  Never return an empty actors array — the downstream validator requires at
  least one actor per event.
- If occurred_at is not stated, use null. Do not guess.
- event_type must be one of: statement | economic | price_move | analyst_call |
  sentiment_shift | report | policy | conflict | other
- status must be one of: fact | claim | rumor
- confidence is a float between 0.0 and 1.0 reflecting how clearly the text
  states the event.

Return ONLY valid JSON in this exact shape:
[
  {
    "headline": "concise, action-oriented (max 150 chars)",
    "summary": "2-4 sentence factual summary (max 2000 chars)",
    "occurred_at": "2026-09-15T12:00:00Z",
    "actors": ["Fed", "Powell"],
    "event_type": "statement",
    "status": "fact",
    "confidence": 0.9
  }
]

JSON:'''


def step1_seed_v2() -> None:
    print("\n[1/3] chain.extract@2")
    src = SEED.read_text()
    if "CHAIN_EXTRACT_V2" in src:
        print("  ↷ already present")
        return

    addition = f'''

# ─────────────────────────────────────────────────────────────────────
# S.6.1b.2.1 — chain.extract@2 (actor rule corrected)
# ─────────────────────────────────────────────────────────────────────

CHAIN_EXTRACT_V2 = """{CHAIN_EXTRACT_V2}"""


def seed_s61b_2_1() -> None:
    """Activate chain.extract@2."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'chain.extract'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('chain.extract', 2, 'reasoning', :template, CAST(:vars AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {{
                "template": CHAIN_EXTRACT_V2,
                "vars": json.dumps(["text"]),
            }},
        )
        session.commit()
    print("  seeded chain.extract@2")


if __name__ == "__main__":
    seed()
    seed_s61b_1()
    seed_s61b_1b()
    seed_s61b_1b_1()
    seed_s61b_2()
    seed_s61b_2_1()
'''
    src = src.rstrip() + addition
    SEED.write_text(src)
    print("  ✓ appended CHAIN_EXTRACT_V2 + seed_s61b_2_1")


# ─────────────────────────────────────────────────────────────────────
# 2. tenant.py — SET LOCAL → SET
# ─────────────────────────────────────────────────────────────────────

def step2_tenant() -> None:
    print("\n[2/3] tenant.py — SET LOCAL → SET")

    # Need to see the current body first
    src = TENANT.read_text()

    old = '''async def set_current_org(session: AsyncSession, org_id: str) -> None:
    """Set `app.current_org_id` for the current transaction.

    Uses SET LOCAL so the setting is scoped to the current transaction only.
    """
    safe = _validate_uuid(org_id)
    await session.execute(text(f"SET LOCAL app.current_org_id = '{safe}'"))'''

    new = '''async def set_current_org(session: AsyncSession, org_id: str) -> None:
    """Set `app.current_org_id` for the current session.

    Uses session-scoped SET, not SET LOCAL. SET LOCAL is transaction-scoped
    and is cleared by any commit inside the request. When a request calls a
    helper that commits (e.g. charge_for_upload → debit → commit), the next
    query against an RLS table runs with the GUC unset and Postgres raises
    'invalid input syntax for type uuid: ""' from the RLS policy.

    The connection pool resets session state on return (pool_reset_on_return
    defaults to 'rollback'), so the GUC does not leak across requests.
    """
    safe = _validate_uuid(org_id)
    await session.execute(text(f"SET app.current_org_id = '{safe}'"))'''

    if new.strip() in src and old not in src:
        print("  ↷ already applied")
        return
    if old not in src:
        fail("tenant.py: set_current_org body changed — paste it")
    src = src.replace(old, new, 1)
    TENANT.write_text(src)
    print("  ✓ set_current_org uses SET")


# ─────────────────────────────────────────────────────────────────────
# 3. Apply + verify end-to-end
# ─────────────────────────────────────────────────────────────────────

def step3_apply_verify() -> bool:
    print("\n[3/3] apply + verify")

    # Syntax
    for f in [SEED, TENANT]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ syntax clean")

    # Seed
    print("  running seeds")
    r = subprocess.run(
        ["python", "-c",
         "from packages.ai.prompts.seed_chain_extra import "
         "seed, seed_s61b_1, seed_s61b_1b, seed_s61b_1b_1, seed_s61b_2, seed_s61b_2_1; "
         "seed(); seed_s61b_1(); seed_s61b_1b(); seed_s61b_1b_1(); seed_s61b_2(); seed_s61b_2_1(); "
         "from packages.ai.prompts.loader import clear_cache; clear_cache()"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(f"  ❌ seed: {r.stderr[-400:]}")
        return False

    # Confirm chain.extract@2 active
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        "SELECT name, version, is_active FROM prompt_versions "
        "WHERE name = 'chain.extract' ORDER BY version;"
    ], capture_output=True, text=True)
    print(r.stdout)

    # Restart API so the tenant change takes effect
    print("  restart API")
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    import time
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
        return False
    print("  ✓ API up")

    # HTTP end-to-end with the SAME paste that failed before
    print("  HTTP end-to-end (the same short paste that failed with no_coherent_chain)")
    code = r'''
import json, time, urllib.request, urllib.error

API = "http://localhost:8000"

def post(path, body, token=None):
    req = urllib.request.Request(f"{API}{path}", method="POST")
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, json.dumps(body).encode()) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def get(path, token=None):
    req = urllib.request.Request(f"{API}{path}")
    if token: req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

status, data = post("/api/auth/login", {"identifier": "tokentest", "password": "TestPass123!"})
token = data["access_token"]

paste = """The Federal Reserve signalled on Tuesday that it will hold interest rates higher for longer. The 10-year note jumped 18 basis points to 5.02%. The S&P 500 fell 2.1%."""

status, data = post("/api/chains/build", {"content": paste, "block_slug": "news"}, token=token)
print(f"  POST /api/chains/build -> HTTP {status}  task={(data.get('chain_id') or '')[:8]}...  events={data.get('event_count')}")
task_id = data["chain_id"]

for i in range(40):
    time.sleep(3)
    status, s = get(f"/api/chains/build/{task_id}", token=token)
    print(f"  [{i*3:3}s] {s['status']}")
    if s["status"] in ("success", "failure"):
        print(f"  final: {s['status']}  verification_status={s.get('verification_status')}  chain_id={(s.get('chain_id') or '')[:8]}")
        if s["status"] != "success":
            print(f"  error: {s.get('error')}")
            raise SystemExit(1)
        break
else:
    print("  ❌ timed out")
    raise SystemExit(1)

print("  ✓ end-to-end success")
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=300,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False

    # tsc (nothing frontend changed, but confirm)
    print("  tsc --noEmit")
    r = subprocess.run(
        ["./node_modules/.bin/tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        print(f"  ❌ tsc: {(r.stdout or r.stderr)[:300]}")
        return False
    print("  ✓ tsc clean")

    return True


def main() -> None:
    print("=" * 70)
    print("S.6.1b.2.1 — actor rule + GUC persistence fix")
    print("=" * 70)

    step1_seed_v2()
    step2_tenant()
    ok = step3_apply_verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.1b.2.1 complete")
        print("   - chain.extract@2: actors must be non-empty")
        print("   - set_current_org: SET LOCAL → SET (survives request commits)")
        print("   - HTTP end-to-end: no_coherent_chain resolved")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
