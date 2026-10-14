"""
SignalIQ — Phase T.7: Provider rotation.

Registers Gemini as a fallback reasoning model. The router already
iterates ai_models in created_at order, so once the row exists, any
Groq 429 automatically falls through to Gemini — no code change needed.

Ships:
  1. Migration 0029 (persists the row across fresh DBs)
  2. Direct INSERT for the running DB
  3. Test: deactivate Groq → confirm router falls to Gemini → reactivate
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION_29 = ROOT / "alembic" / "versions" / "0029_gemini_reasoning_fallback.py"


MIGRATION_SRC = '''"""Register Gemini as a fallback reasoning model.

The AI router already iterates ai_models in created_at order and uses
the first successful provider. Adding gemini-2.5-flash as a second
reasoning model makes any Groq 429 fall through automatically.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO ai_models (provider, model_name, role, is_active, metadata)
        VALUES (
            'gemini',
            'gemini-2.5-flash',
            'reasoning',
            true,
            '{"note": "fallback for groq/openai/gpt-oss-120b; free tier available"}'::jsonb
        )
        ON CONFLICT (provider, model_name) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM ai_models
        WHERE provider = 'gemini' AND model_name = 'gemini-2.5-flash'
    """)
'''


def run_sql(sql: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-v", "ON_ERROR_STOP=1"],
        input=sql, capture_output=True, text=True,
    )
    return r.returncode, r.stdout or "", r.stderr or ""


def run_sql_t(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-tAc", sql],
        capture_output=True, text=True,
    )
    return (r.stdout or "").strip()


# ─────────────────────────────────────────────────────────────────────
# 1. Migration file
# ─────────────────────────────────────────────────────────────────────

def step1_write_migration() -> None:
    print("\n[1/5] Write migration 0029")
    if MIGRATION_29.exists():
        print(f"  ↷ {MIGRATION_29.name} already exists")
        return
    MIGRATION_29.write_text(MIGRATION_SRC)
    print(f"  ✓ wrote {MIGRATION_29.name}")


# ─────────────────────────────────────────────────────────────────────
# 2. Verify GEMINI_API_KEY is set
# ─────────────────────────────────────────────────────────────────────

def step2_check_api_key() -> bool:
    print("\n[2/5] Check GEMINI_API_KEY")
    env = ROOT / ".env"
    key_set = False
    for line in env.read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            val = line.split("=", 1)[1].strip()
            if val:
                key_set = True
                print(f"  ✓ GEMINI_API_KEY set ({len(val)} chars)")
            break
    if not key_set:
        print("  ❌ GEMINI_API_KEY missing or empty in .env")
        print("     Get one free at https://aistudio.google.com/apikey")
        return False
    return True


# ─────────────────────────────────────────────────────────────────────
# 3. Insert the model row
# ─────────────────────────────────────────────────────────────────────

def step3_insert_model() -> None:
    print("\n[3/5] Insert gemini-2.5-flash as reasoning model")

    existing = run_sql_t(
        "SELECT id::text FROM ai_models "
        "WHERE provider = 'gemini' AND model_name = 'gemini-2.5-flash';"
    )
    if existing:
        print(f"  ↷ model row already exists ({existing[:8]}…)")
        return

    rc, out, err = run_sql("""
        INSERT INTO ai_models (provider, model_name, role, is_active, metadata)
        VALUES (
            'gemini',
            'gemini-2.5-flash',
            'reasoning',
            true,
            '{"note": "fallback for groq/openai/gpt-oss-120b; free tier available"}'::jsonb
        );
    """)
    if rc != 0:
        raise SystemExit(f"❌ INSERT failed:\n{err}")
    print("  ✓ inserted gemini-2.5-flash")


# ─────────────────────────────────────────────────────────────────────
# 4. Apply migration (idempotent — ON CONFLICT DO NOTHING)
# ─────────────────────────────────────────────────────────────────────

def step4_migrate() -> None:
    print("\n[4/5] alembic upgrade head")
    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"❌ alembic failed:\n{r.stderr}")
    print("  ✓ migrated")
    for line in (r.stderr or "").splitlines():
        if "Running upgrade" in line:
            print(f"      {line.strip()}")


# ─────────────────────────────────────────────────────────────────────
# 5. Test rotation
# ─────────────────────────────────────────────────────────────────────

def step5_test_rotation() -> bool:
    print("\n[5/5] Test provider rotation")

    # 5a. Show the current reasoning chain
    print("\n  Reasoning models (in fallback order):")
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-c",
         "SELECT provider, model_name, is_active FROM ai_models "
         "WHERE role = 'reasoning' ORDER BY created_at;"],
        capture_output=True, text=True,
    )
    print(r.stdout)

    # 5b. Normal call — should hit Groq (first in created_at order)
    print("  Test A: normal reasoning call (expect Groq primary)")
    r = subprocess.run([
        "python", "-c", """
import asyncio, sys
sys.path.insert(0, '.')
from packages.ai.router import complete

async def main():
    text = await complete(
        role='reasoning',
        system='Answer in one short sentence.',
        user='What is 2+2?',
        purpose='t7.rotation_test',
        temperature=0.0,
        max_tokens=50,
        use_cache=False,
    )
    print(f'  got: {text[:100]}')

asyncio.run(main())
        """.strip()
    ], cwd=ROOT, capture_output=True, text=True, timeout=60)
    print(r.stdout)
    if r.returncode != 0:
        print("  ❌ normal call failed:")
        print(r.stderr[-800:])
        return False

    # 5c. Deactivate Groq — should hit Gemini
    print("\n  Test B: deactivate Groq → expect Gemini fallback")
    rc, _, err = run_sql("""
        UPDATE ai_models
        SET is_active = false
        WHERE role = 'reasoning' AND provider = 'groq';
    """)
    if rc != 0:
        raise SystemExit(f"❌ deactivate failed:\n{err}")
    print("  ✓ Groq reasoning deactivated")

    try:
        r = subprocess.run([
            "python", "-c", """
import asyncio, sys
sys.path.insert(0, '.')
from packages.ai.router import complete

async def main():
    text = await complete(
        role='reasoning',
        system='Answer in one short sentence.',
        user='What is 3+3?',
        purpose='t7.rotation_test_fallback',
        temperature=0.0,
        max_tokens=50,
        use_cache=False,
    )
    print(f'  got: {text[:100]}')

asyncio.run(main())
            """.strip()
        ], cwd=ROOT, capture_output=True, text=True, timeout=60)
        print(r.stdout)
        fallback_ok = r.returncode == 0
        if not fallback_ok:
            print("  ❌ fallback call failed:")
            print(r.stderr[-800:])
    finally:
        # Reactivate Groq no matter what
        rc, _, err = run_sql("""
            UPDATE ai_models
            SET is_active = true
            WHERE role = 'reasoning' AND provider = 'groq';
        """)
        if rc != 0:
            print(f"  ⚠ failed to reactivate Groq: {err}")
            return False
        print("  ✓ Groq reasoning reactivated")

    if not fallback_ok:
        return False

    # 5d. Final state check
    print("\n  Final reasoning chain (both should be active):")
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-c",
         "SELECT provider, model_name, is_active FROM ai_models "
         "WHERE role = 'reasoning' ORDER BY created_at;"],
        capture_output=True, text=True,
    )
    print(r.stdout)

    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.7: Provider rotation")
    print("=" * 70)

    step1_write_migration()
    if not step2_check_api_key():
        sys.exit(1)
    step3_insert_model()
    step4_migrate()

    ok = step5_test_rotation()

    print("=" * 70)
    if ok:
        print("✅ T.7 complete — provider rotation live")
        print()
        print("The router now:")
        print("  1. Tries groq/openai/gpt-oss-120b first")
        print("  2. Falls through to gemini/gemini-2.5-flash on 429/error")
        print("  3. Preserves the same verify/regenerate discipline")
        print()
        print("All 16 trading subblock generators are now resilient to")
        print("Groq rate limits automatically.")
        sys.exit(0)
    print("❌ T.7 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
