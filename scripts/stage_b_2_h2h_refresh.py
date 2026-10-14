"""
SignalIQ — Phase B.2: H2H snapshot refresh task.

Extends prediction_snapshots.py to also refresh h2h_snapshots.

For every ordered pair of teams that has met at least once in finished
matches (in either orientation), compute the H2H signal and upsert.

  1. Adds refresh_h2h_snapshots task to prediction_snapshots.py
  2. Registers it in tasks/__init__.py (same module — already imported)
  3. Beat entry at 01:15 UTC (15 min after form)
  4. Runs once against the live DB and verifies row count

Idempotent. Fails loudly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "prediction_snapshots.py"
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
# 1. Append H2H task to prediction_snapshots.py
# ─────────────────────────────────────────────────────────────────────

H2H_BLOCK = '''

async def _refresh_h2h_async() -> dict:
    """Refresh h2h_snapshots for every ordered team pair with any finished meeting."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from packages.core.config import get_settings
    from packages.prediction.form.h2h import compute_h2h, save_h2h_snapshot

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_pre_ping=True, pool_recycle=180,
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    total_saved = 0
    by_comp: dict[str, int] = {}

    try:
        async with factory() as session:
            # compute_h2h expects a datetime, not a date (mirrors form).
            as_of = datetime.now(UTC)

            for comp in COMPETITIONS:
                # Every pair that has met at least once, in either orientation.
                # We deduplicate the unordered set first, then feed both
                # orientations so whichever way an upcoming fixture is
                # scheduled, a row exists.
                pairs_raw = (await session.execute(sql_text("""
                    SELECT DISTINCT
                        LEAST(home_team, away_team)    AS a,
                        GREATEST(home_team, away_team) AS b
                    FROM matches
                    WHERE status = 'finished'
                      AND competition = :comp
                      AND home_goals IS NOT NULL
                """), {"comp": comp})).all()

                saved_for_comp = 0
                for (a, b) in pairs_raw:
                    for (home, away) in ((a, b), (b, a)):
                        try:
                            snap = await compute_h2h(
                                session,
                                home_team=home,
                                away_team=away,
                                competition=comp,
                                as_of=as_of,
                            )
                            if snap is None:
                                continue
                            await save_h2h_snapshot(session, snapshot=snap)
                            saved_for_comp += 1
                        except Exception as exc:
                            log.warning(
                                "h2h_snapshots.pair_failed",
                                home=home, away=away,
                                error=str(exc)[:200],
                            )

                by_comp[comp] = saved_for_comp
                total_saved += saved_for_comp
                log.info(
                    "h2h_snapshots.saved",
                    competition=comp, as_of=str(as_of), count=saved_for_comp,
                )
    finally:
        await engine.dispose()

    return {"total_saved": total_saved, "by_competition": by_comp}


@celery_app.task(name="tasks.refresh_h2h_snapshots", bind=True)
def refresh_h2h_snapshots(self) -> dict:  # type: ignore[no-untyped-def]
    """Daily: recompute per-pair H2H snapshots."""
    return asyncio.run(_refresh_h2h_async())
'''


def step1_append_h2h_task() -> None:
    if "refresh_h2h_snapshots" in TASK_FILE.read_text():
        print(f"  ↷ {TASK_FILE.name}: H2H task already present")
        return
    src = TASK_FILE.read_text().rstrip() + "\n" + H2H_BLOCK
    TASK_FILE.write_text(src)
    print(f"  ✓ {TASK_FILE.name}: H2H task appended")


# ─────────────────────────────────────────────────────────────────────
# 2. Beat entry
# ─────────────────────────────────────────────────────────────────────

def step2_beat_entry() -> None:
    _patch(
        BEAT,
        """        # Refresh per-team form snapshots daily at 01:00 UTC
        # (feeds dixon_coles_v2's recent-form signal)
        "refresh-form-snapshots": {
            "task": "tasks.refresh_form_snapshots",
            "schedule": crontab(hour="1", minute="0"),
        },""",
        """        # Refresh per-team form snapshots daily at 01:00 UTC
        # (feeds dixon_coles_v2's recent-form signal)
        "refresh-form-snapshots": {
            "task": "tasks.refresh_form_snapshots",
            "schedule": crontab(hour="1", minute="0"),
        },
        # Refresh per-pair H2H snapshots 15 min later (01:15 UTC)
        "refresh-h2h-snapshots": {
            "task": "tasks.refresh_h2h_snapshots",
            "schedule": crontab(hour="1", minute="15"),
        },""",
        "refresh-h2h-snapshots beat entry",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Run once + verify
# ─────────────────────────────────────────────────────────────────────

def step3_run_and_verify() -> bool:
    import asyncio

    async def _run() -> bool:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
        sys.path.insert(0, str(ROOT))
        from packages.core.config import get_settings
        from apps.worker.tasks.prediction_snapshots import _refresh_h2h_async

        engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        try:
            async with factory() as s:
                pre = (await s.execute(text("SELECT COUNT(*) FROM h2h_snapshots"))).scalar()
            print(f"  h2h_snapshots rows before: {pre}")

            print("  running refresh_h2h_snapshots()...")
            result = await _refresh_h2h_async()
            print(f"    {result}")

            async with factory() as s:
                post = (await s.execute(text("SELECT COUNT(*) FROM h2h_snapshots"))).scalar()
                latest = (await s.execute(text("SELECT MAX(computed_at) FROM h2h_snapshots"))).scalar()
                rows = (await s.execute(text("""
                    SELECT home_team, away_team, matches_considered, h2h_multiplier
                    FROM h2h_snapshots
                    ORDER BY computed_at DESC, home_team, away_team
                    LIMIT 6
                """))).all()

            print(f"  h2h_snapshots rows after:  {post}")
            print(f"  latest computed_at:        {latest}")
            print()
            print(f"  sample of 6 most-recent rows:")
            for r in rows:
                print(f"    {r[0]:24} vs {r[1]:24} n={r[2]} mult={r[3]}")

            from datetime import UTC, datetime, timedelta
            fresh = latest is not None and (datetime.now(UTC) - latest) < timedelta(minutes=2)
            ok = (
                result.get("total_saved", 0) > 0
                and post >= pre
                and fresh
            )
            return ok
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def main() -> None:
    print("=" * 70)
    print("SignalIQ — B.2: H2H snapshot refresh task")
    print("=" * 70)

    print("\n[1/3] Append refresh_h2h_snapshots to prediction_snapshots.py")
    step1_append_h2h_task()

    print("\n[2/3] Add beat entry (01:15 UTC daily)")
    step2_beat_entry()

    print("\n[3/3] Run once + verify")
    ok = step3_run_and_verify()

    print("=" * 70)
    if ok:
        print("✅ B.2 complete — H2H snapshots refresh on schedule")
        print("=" * 70)
        sys.exit(0)
    print("❌ B.2 verification failed — see above")
    print("=" * 70)
    sys.exit(1)


if __name__ == "__main__":
    main()
