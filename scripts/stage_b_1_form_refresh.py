"""
SignalIQ — Phase B.1: form snapshot refresh task.

Wires the existing (orphan) form writer to a daily Celery beat schedule.

Writers already exist in packages/prediction/form/recent.py:
  - compute_form_for_competition(session, competition=..., as_of=...)
  - save_form_snapshots(session, snapshots=[...])

This script:
  1. Writes apps/worker/tasks/prediction_snapshots.py (form-only, H2H in B.2)
  2. Registers it in apps/worker/tasks/__init__.py
  3. Adds a daily beat entry at 01:00 UTC
  4. Runs once against the live DB and verifies row count

Idempotent. Fails loudly.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "prediction_snapshots.py"
TASKS_INIT = ROOT / "apps" / "worker" / "tasks" / "__init__.py"
BEAT = ROOT / "apps" / "worker" / "beat_schedule.py"

COMPETITIONS = ["EPL"]


def _patch(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"\n❌ {path.relative_to(ROOT)} does not exist")
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:400]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. Task file
# ─────────────────────────────────────────────────────────────────────

TASK_SRC = '''"""Prediction snapshot refresh tasks.

Fills form_snapshots (and, in a future stage, h2h_snapshots) from finished
matches so dixon_coles_v2 can layer recent form on the base model.

Design:
  - One task per snapshot kind (form here; H2H is a separate task).
  - Ordered in beat_schedule BEFORE dixon_coles_v2 prediction task.
  - Idempotent: upserts per (team, competition, as_of_date).
  - No LLM, no external API — purely derived from matches table.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

# TODO: derive from SELECT DISTINCT competition FROM matches WHERE status='finished'
COMPETITIONS = ["EPL"]


async def _refresh_form_async() -> dict:
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from packages.core.config import get_settings
    from packages.prediction.form.recent import (
        compute_form_for_competition,
        save_form_snapshots,
    )

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_pre_ping=True, pool_recycle=180,
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    total_saved = 0
    by_comp: dict[str, int] = {}

    try:
        async with factory() as session:
            as_of = datetime.now(UTC).date()
            for comp in COMPETITIONS:
                try:
                    snapshots = await compute_form_for_competition(
                        session, competition=comp, as_of=as_of,
                    )
                    saved = await save_form_snapshots(session, snapshots=snapshots)
                    by_comp[comp] = saved
                    total_saved += saved
                    log.info(
                        "form_snapshots.saved",
                        competition=comp, as_of=str(as_of), count=saved,
                    )
                except Exception as exc:
                    log.warning(
                        "form_snapshots.failed",
                        competition=comp, error=str(exc)[:200],
                    )
                    by_comp[comp] = -1
            await session.commit()
    finally:
        await engine.dispose()

    return {"total_saved": total_saved, "by_competition": by_comp}


@celery_app.task(name="tasks.refresh_form_snapshots", bind=True)
def refresh_form_snapshots(self) -> dict:  # type: ignore[no-untyped-def]
    """Daily: recompute per-team rolling form snapshots."""
    return asyncio.run(_refresh_form_async())
'''


def step1_write_task() -> None:
    if TASK_FILE.exists():
        print(f"  ↷ {TASK_FILE.name} already exists")
        return
    TASK_FILE.write_text(TASK_SRC)
    print(f"  ✓ wrote {TASK_FILE.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 2. Register in tasks/__init__.py
# ─────────────────────────────────────────────────────────────────────

def step2_register() -> None:
    src = TASKS_INIT.read_text()
    if "prediction_snapshots" in src:
        print(f"  ↷ {TASKS_INIT.name}: already registered")
        return
    src = src.rstrip() + "\nfrom apps.worker.tasks import prediction_snapshots  # noqa: F401\n"
    TASKS_INIT.write_text(src)
    print(f"  ✓ {TASKS_INIT.name}: registered")


# ─────────────────────────────────────────────────────────────────────
# 3. Beat schedule entry
# ─────────────────────────────────────────────────────────────────────

def step3_beat_entry() -> None:
    _patch(
        BEAT,
        """        # Recompute external source reliability daily at 03:00 UTC
        # (10 min after score_predictions has had a chance to run in the day)
        "score-external-reliability": {
            "task": "tasks.score_external_reliability",
            "schedule": crontab(hour="3", minute="0"),
        },
    }""",
        """        # Refresh per-team form snapshots daily at 01:00 UTC
        # (feeds dixon_coles_v2's recent-form signal)
        "refresh-form-snapshots": {
            "task": "tasks.refresh_form_snapshots",
            "schedule": crontab(hour="1", minute="0"),
        },
        # Recompute external source reliability daily at 03:00 UTC
        # (10 min after score_predictions has had a chance to run in the day)
        "score-external-reliability": {
            "task": "tasks.score_external_reliability",
            "schedule": crontab(hour="3", minute="0"),
        },
    }""",
        "refresh-form-snapshots beat entry",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. Run once + verify
# ─────────────────────────────────────────────────────────────────────

def step4_run_and_verify() -> bool:
    async def _run() -> bool:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
        sys.path.insert(0, str(ROOT))
        from packages.core.config import get_settings
        from apps.worker.tasks.prediction_snapshots import _refresh_form_async

        # Pre-count
        pre_engine = create_async_engine(
            get_settings().database_url, pool_pre_ping=True,
        )
        pre_factory = async_sessionmaker(bind=pre_engine, class_=AsyncSession, expire_on_commit=False)
        async with pre_factory() as s:
            pre = (await s.execute(text("SELECT COUNT(*) FROM form_snapshots"))).scalar()
        await pre_engine.dispose()
        print(f"  form_snapshots rows before: {pre}")

        print("  running refresh_form_snapshots()...")
        result = await _refresh_form_async()
        print(f"    {result}")

        # Post-count + freshness
        post_engine = create_async_engine(
            get_settings().database_url, pool_pre_ping=True,
        )
        post_factory = async_sessionmaker(bind=post_engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with post_factory() as s:
                post = (await s.execute(text("SELECT COUNT(*) FROM form_snapshots"))).scalar()
                latest = (await s.execute(text("""
                    SELECT MAX(computed_at) FROM form_snapshots
                """))).scalar()
                rows = (await s.execute(text("""
                    SELECT team, competition, as_of_date, matches_considered, computed_at
                    FROM form_snapshots
                    ORDER BY computed_at DESC, team
                    LIMIT 5
                """))).all()

            print(f"  form_snapshots rows after: {post}")
            print(f"  latest computed_at:        {latest}")
            print()
            print(f"  sample of 5 (ordered by computed_at DESC):")
            for r in rows:
                print(f"    {r[0]:28} {r[1]:5} {r[2]} n={r[3]} @ {r[4]}")

            # Pass conditions:
            # - result reports >= 20 saved
            # - row count unchanged (upsert, not insert)
            # - latest computed_at is fresh (within last minute)
            from datetime import UTC, datetime, timedelta
            fresh = latest is not None and (datetime.now(UTC) - latest) < timedelta(minutes=2)
            ok = (
                result.get("total_saved", 0) >= 20
                and post == pre
                and fresh
            )
            return ok
        finally:
            await post_engine.dispose()

    return asyncio.run(_run())


def main() -> None:
    print("=" * 70)
    print("SignalIQ — B.1: form snapshot refresh task")
    print("=" * 70)

    print("\n[1/4] Write apps/worker/tasks/prediction_snapshots.py")
    step1_write_task()

    print("\n[2/4] Register in apps/worker/tasks/__init__.py")
    step2_register()

    print("\n[3/4] Add beat entry (01:00 UTC daily)")
    step3_beat_entry()

    print("\n[4/4] Run once + verify")
    ok = step4_run_and_verify()

    print("=" * 70)
    if ok:
        print("✅ B.1 complete — form snapshots refresh on schedule")
        print("=" * 70)
        sys.exit(0)
    print("❌ B.1 verification failed — see above")
    print("=" * 70)
    sys.exit(1)


if __name__ == "__main__":
    main()
