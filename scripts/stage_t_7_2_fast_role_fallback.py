"""
SignalIQ — Phase T.7.2: Fast-role provider fallback.

Reactivates 4 working fast-role models in priority order (Groq primary,
then Gemini, then two OpenRouter, then local Ollama) and deletes 3
retired models that returned 404.

Priority is set by created_at: we push the fallbacks into the future
so they sort AFTER the active Groq model.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION_31 = ROOT / "alembic" / "versions" / "0031_fast_role_fallback_chain.py"


MIGRATION_SRC = '''"""Fast-role fallback chain for Groq 429s.

Reactivates 4 working fast-role fallbacks in priority order and removes
3 retired models that returned 404 during testing.

Priority (created_at ascending):
  1. groq/openai/gpt-oss-20b          (already active, unchanged)
  2. gemini/gemini-flash-latest       (reactivated, created_at = now()+1s)
  3. openrouter/nvidia/nemotron-3-... (reactivated, created_at = now()+2s)
  4. openrouter/nex-agi/nex-n2.5-mini (reactivated, created_at = now()+3s)
  5. ollama/qwen2.5:0.5b-instruct     (reactivated, created_at = now()+4s)

Deleted (tested, returned 404 or connection error):
  - groq/llama-3.3-70b-versatile
  - openrouter/google/gemini-2.0-flash-exp:free
  - openrouter/google/gemma-4-31b-it:free

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (provider, model_name) to reactivate, in priority order
REACTIVATE = [
    ("gemini", "gemini-flash-latest"),
    ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
    ("openrouter", "nex-agi/nex-n2.5-mini:free"),
    ("ollama", "qwen2.5:0.5b-instruct"),
]

# (provider, model_name) to delete (tested, no longer valid)
DELETE = [
    ("groq", "llama-3.3-70b-versatile"),
    ("openrouter", "google/gemini-2.0-flash-exp:free"),
    ("openrouter", "google/gemma-4-31b-it:free"),
]


def upgrade() -> None:
    # Reactivate + bump created_at so they sort after the primary
    for i, (provider, model_name) in enumerate(REACTIVATE):
        offset_seconds = i + 1
        p = provider.replace("'", "''")
        m = model_name.replace("'", "''")
        op.execute(f"""
            UPDATE ai_models
            SET is_active = true,
                created_at = now() + INTERVAL '{offset_seconds} seconds'
            WHERE provider = '{p}'
              AND model_name = '{m}'
              AND role = 'fast'
        """)

    # Delete retired models
    for provider, model_name in DELETE:
        p = provider.replace("'", "''")
        m = model_name.replace("'", "''")
        op.execute(f"""
            DELETE FROM ai_models
            WHERE provider = '{p}'
              AND model_name = '{m}'
              AND role = 'fast'
              AND is_active = false
        """)


def downgrade() -> None:
    # Deactivate the 4 we activated (without restoring created_at)
    for provider, model_name in REACTIVATE:
        p = provider.replace("'", "''")
        m = model_name.replace("'", "''")
        op.execute(f"""
            UPDATE ai_models
            SET is_active = false
            WHERE provider = '{p}' AND model_name = '{m}' AND role = 'fast'
        """)
    # Note: deleted rows are not restored — this is a one-way cleanup
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


def step1_write_migration() -> None:
    print("\n[1/4] Write migration 0031")
    if MIGRATION_31.exists():
        print(f"  ↷ {MIGRATION_31.name} already exists")
        return
    MIGRATION_31.write_text(MIGRATION_SRC)
    print(f"  ✓ wrote {MIGRATION_31.name}")


def step2_delete_retired() -> None:
    print("\n[2/4] Delete retired models (tested, returned 404)")
    rc, _, err = run_sql("""
        DELETE FROM ai_models
        WHERE role = 'fast'
          AND is_active = false
          AND (
              (provider = 'groq' AND model_name = 'llama-3.3-70b-versatile')
              OR (provider = 'openrouter' AND model_name = 'google/gemini-2.0-flash-exp:free')
              OR (provider = 'openrouter' AND model_name = 'google/gemma-4-31b-it:free')
          );
    """)
    if rc != 0:
        raise SystemExit(f"❌ delete failed:\n{err}")
    print("  ✓ removed 3 retired models")


def step3_reactivate() -> None:
    print("\n[3/4] Reactivate 4 working models in priority order")

    reactivate = [
        ("gemini", "gemini-flash-latest"),
        ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
        ("openrouter", "nex-agi/nex-n2.5-mini:free"),
        ("ollama", "qwen2.5:0.5b-instruct"),
    ]

    for i, (provider, model_name) in enumerate(reactivate):
        offset = i + 1
        rc, out, err = run_sql(f"""
            UPDATE ai_models
            SET is_active = true,
                created_at = now() + INTERVAL '{offset} seconds'
            WHERE provider = '{provider}'
              AND model_name = '{model_name}'
              AND role = 'fast'
        """)
        if rc != 0:
            raise SystemExit(f"❌ reactivate {provider}/{model_name} failed:\n{err}")
        print(f"  ✓ {provider}/{model_name}")


def step4_verify_chain() -> bool:
    print("\n[4/4] Verify the fast-role chain")

    print("  Fast-role chain (in fallback order):")
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-c",
         "SELECT provider, model_name, is_active FROM ai_models "
         "WHERE role = 'fast' ORDER BY created_at;"],
        capture_output=True, text=True,
    )
    print(r.stdout)

    # Test the router with Groq deactivated
    print("  Test: deactivate Groq fast → expect Gemini fallback")

    def run_sql_simple(sql: str) -> int:
        r = subprocess.run(
            ["docker", "exec", "-i", "signaliq-postgres-1",
             "psql", "-U", "signaliq", "-d", "signaliq", "-v", "ON_ERROR_STOP=1"],
            input=sql, capture_output=True, text=True,
        )
        return r.returncode

    run_sql_simple(
        "UPDATE ai_models SET is_active=false "
        "WHERE role='fast' AND provider='groq';"
    )
    try:
        r = subprocess.run([
            "python", "-c", """
import asyncio, sys
sys.path.insert(0, '.')
from packages.ai.router import complete

async def main():
    try:
        text = await complete(
            role='fast',
            system='Reply with one word.',
            user='Say hello.',
            purpose='t7.2.fast_fallback_test',
            temperature=0.0,
            max_tokens=50,
            use_cache=False,
        )
        print(f'OK: {text[:80]}')
    except Exception as e:
        print(f'ERR: {type(e).__name__}: {str(e)[:200]}')

asyncio.run(main())
            """.strip()
        ], cwd=".", capture_output=True, text=True, timeout=90)
        print(f"  {r.stdout.strip()[-200:]}")
        test_ok = "OK:" in r.stdout
    finally:
        run_sql_simple(
            "UPDATE ai_models SET is_active=true "
            "WHERE role='fast' AND provider='groq';"
        )
        print("  ✓ Groq reactivated")

    return test_ok


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.7.2: Fast-role provider fallback")
    print("=" * 70)

    step1_write_migration()
    step2_delete_retired()
    step3_reactivate()

    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"❌ alembic failed:\n{r.stderr}")
    print("\n  ✓ alembic upgrade head")
    for line in (r.stderr or "").splitlines():
        if "Running upgrade" in line:
            print(f"      {line.strip()}")

    ok = step4_verify_chain()

    print("=" * 70)
    if ok:
        print("✅ T.7.2 complete — fast-role fallback chain live")
        print()
        print("Fast-role order:")
        print("  1. groq/openai/gpt-oss-20b (primary)")
        print("  2. gemini/gemini-flash-latest (fallback)")
        print("  3. openrouter/nvidia/nemotron-3-super-120b-a12b:free")
        print("  4. openrouter/nex-agi/nex-n2.5-mini:free")
        print("  5. ollama/qwen2.5:0.5b-instruct (local last resort)")
        sys.exit(0)
    print("❌ T.7.2 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
