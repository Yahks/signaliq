"""
SignalIQ — Phase S.4.3: Set current_user_id contextvar in every Celery task
that makes LLM calls.

Problem (from worker log):
  [ai.debit_check] has_uid=False purpose=enrich.combined
  [ai.debit_check] has_uid=False purpose=embed.record
  ...

The token ledger only debits trading generators and manual API calls,
because only apps/worker/tasks/trading.py sets the current_user_id
contextvar. Every other task that calls the LLM (enrich, embed,
cluster, news_chains, briefs, etc.) runs with has_uid=False so the
router's debit hook silently skips.

Fix: apply the same pattern to every task that touches the LLM.

Strategy:
  1. Add a small helper in packages/core/context.py:
     `set_context_from_org(session, org_id)` — resolves the org owner,
     sets current_user_id + current_org_id contextvars.
  2. Call it at the top of each task's async body, right after the
     session is created and org RLS is set.
  3. Idempotent — if the contextvar is already set (nested call),
     don't overwrite.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTEXT = ROOT / "packages" / "core" / "context.py"
TASKS_DIR = ROOT / "apps" / "worker" / "tasks"


def _patch(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        print(f"  ⚠ {path.name}: file not found — skipping")
        return
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        print(f"  ⚠ {path.name}: pattern not found for '{label}' — skipping")
        return
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. context.py — set_context_from_org helper
# ─────────────────────────────────────────────────────────────────────

def step1_context_helper() -> None:
    print("\n[1/2] Add set_context_from_org helper to packages/core/context.py")

    src = CONTEXT.read_text()
    if "def set_context_from_org" in src or "async def set_context_from_org" in src:
        print("  ↷ helper already present")
        return

    helper = '''

# ─────────────────────────────────────────────────────────────────────
# Convenience helper — used at Celery task entrypoints
# ─────────────────────────────────────────────────────────────────────

async def set_context_from_org(session, org_id: str) -> str | None:
    """Resolve the org owner and set both contextvars.

    Idempotent: if current_user_id is already set, don't override.
    Returns the user_id (or None if the org has no owner).

    Called at the top of every Celery task that makes LLM calls, so
    the router's debit hook knows who to charge.
    """
    if current_user_id.get() is not None:
        return current_user_id.get()

    from sqlalchemy import text as _sql
    row = (await session.execute(_sql("""
        SELECT user_id::text FROM memberships
        WHERE org_id = :oid AND role = 'owner'
        LIMIT 1
    """), {"oid": org_id})).first()

    uid = row[0] if row else None
    if uid:
        current_user_id.set(uid)
    current_org_id.set(org_id)
    return uid
'''
    CONTEXT.write_text(src.rstrip() + "\n" + helper)
    print("  ✓ context.py: set_context_from_org added")


# ─────────────────────────────────────────────────────────────────────
# 2. Patch each task that makes LLM calls
# ─────────────────────────────────────────────────────────────────────

# (file, anchor_pattern_old, anchor_pattern_new, human_name)
TASK_PATCHES = [
    (
        "enrich.py",
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()''',
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        # Resolve the org owner so LLM calls debit the right user
        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)''',
        "enrich",
    ),
    (
        "embed.py",
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()''',
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)''',
        "embed",
    ),
    (
        "cluster.py",
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()''',
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)''',
        "cluster",
    ),
    (
        "pain_points.py",
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()''',
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)''',
        "pain_points",
    ),
    (
        "news_chains.py",
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()''',
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)''',
        "news_chains",
    ),
    (
        "briefs.py",
        '''        await session.execute(sql_text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()''',
        '''        await session.execute(sql_text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)''',
        "briefs",
    ),
    (
        "fetch_stock_news.py",
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))''',
        '''        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)''',
        "fetch_stock_news",
    ),
    (
        "prediction_v2.py",
        '''        await session.execute(sql_text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()''',
        '''        await session.execute(sql_text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        from packages.core.context import set_context_from_org
        await set_context_from_org(session, org_id)''',
        "prediction_v2",
    ),
    (
        "prediction_snapshots.py",
        '''        async with factory() as session:
            as_of = datetime.now(UTC)
            for comp in COMPETITIONS:''',
        '''        async with factory() as session:
            # Snapshot tasks are compute-only (no LLM) — no contextvar needed.
            # Left as-is by S.4.3.
            as_of = datetime.now(UTC)
            for comp in COMPETITIONS:''',
        "prediction_snapshots (no-op comment)",
    ),
]


def step2_patch_tasks() -> None:
    print("\n[2/2] Patch Celery tasks to set contextvar")

    for filename, old, new, label in TASK_PATCHES:
        _patch(TASKS_DIR / filename, old, new, label)


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.4.3: Contextvar in every LLM task")
    print("=" * 70)

    step1_context_helper()
    step2_patch_tasks()

    print("\n── Restart worker ──")
    subprocess.run(["pkill", "-9", "-f", "celery.*worker"], check=False)
    import time
    time.sleep(3)
    with Path("/tmp/signaliq-worker.log").open("w") as f:
        subprocess.Popen(
            ["celery", "-A", "apps.worker.celery_app", "worker",
             "--loglevel=info", "-Q", "default,ingestion", "--hostname=worker1@%h"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(8)

    ready = Path("/tmp/signaliq-worker.log").read_text()
    if "ready" not in ready:
        print("  ❌ worker didn't come up")
        sys.exit(1)
    print("  ✓ worker ready")

    print("=" * 70)
    print("✅ S.4.3 complete — all LLM tasks now set the user contextvar")
    print()
    print("Verify after a fresh task:")
    print("  tail -30 /tmp/signaliq-worker.log | grep debit_check")
    print("  Should show has_uid=True for enrich.combined, embed.record, etc.")
    print()
    print("NOTE: this does NOT retroactively fix past ledger gaps.")
    print("      Only forward-looking accounting is corrected.")
    print()
    print("Next: S.5 — BYOK (Bring Your Own Key)")


if __name__ == "__main__":
    main()
