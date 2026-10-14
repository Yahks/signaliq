"""
SignalIQ — Phase B.3: dixon_coles_v2 prediction writer.

For every scheduled EPL match, compute a v2 prediction (base DC + form +
H2H) and upsert into external_predictions under source_id=dixon_coles_v2.

  1. Writes apps/worker/tasks/prediction_v2.py
  2. Registers in tasks/__init__.py
  3. Beat entry at 02:00 UTC
  4. Runs once against the live DB and verifies rows appeared

Idempotent. Fails loudly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "prediction_v2.py"
TASKS_INIT = ROOT / "apps" / "worker" / "tasks" / "__init__.py"
BEAT = ROOT / "apps" / "worker" / "beat_schedule.py"

COMPETITIONS = ["EPL"]
SOURCE_SLUG = "dixon_coles_v2"


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

TASK_SRC = '''"""Dixon-Coles v2 prediction task.

Writes dixon_coles_v2 predictions to external_predictions for every
scheduled match, so the model participates in the ensemble + reliability
system alongside the_odds_api.

Uses:
  - fit_and_cache() from predictor.py (same fit as the blend)
  - form_snapshots (team-level, from tasks.prediction_snapshots)
  - h2h_snapshots (pair-level, from tasks.prediction_snapshots)
  - predict_match_v2() from dixon_coles_v2.py

Idempotent: upserts per (source_id, match_id) via 0018's unique constraint.
"""
from __future__ import annotations

import asyncio
import json

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

COMPETITIONS = ["EPL"]
SOURCE_SLUG = "dixon_coles_v2"


async def _run_v2_async() -> dict:
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from packages.core.config import get_settings
    from packages.prediction.dixon_coles_v2 import (
        FormSignal,
        H2HSignal,
        predict_match_v2,
    )
    from packages.prediction.predictor import fit_and_cache

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_pre_ping=True, pool_recycle=180,
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    total_upserts = 0
    by_comp: dict[str, int] = {}

    try:
        async with factory() as session:
            # Resolve source_id once
            source_id = (await session.execute(sql_text("""
                SELECT id::text FROM external_prediction_sources WHERE slug = :slug
            """), {"slug": SOURCE_SLUG})).scalar()

            if not source_id:
                log.error("v2.source_not_registered", slug=SOURCE_SLUG)
                return {"total_upserts": 0, "by_competition": {}, "error": "source not registered"}

            for comp in COMPETITIONS:
                # Fit base model (cached by config_hash)
                try:
                    fit, config_hash = await fit_and_cache(session, competition=comp)
                except Exception as exc:
                    log.warning("v2.fit_failed", competition=comp, error=str(exc)[:200])
                    by_comp[comp] = -1
                    continue

                # Load all form snapshots for this competition (today's rows)
                form_rows = (await session.execute(sql_text("""
                    SELECT DISTINCT ON (team)
                        team, attack_residual, defense_residual, matches_considered
                    FROM form_snapshots
                    WHERE competition = :comp
                    ORDER BY team, as_of_date DESC
                """), {"comp": comp})).all()
                form_by_team = {
                    r[0]: FormSignal(
                        team=r[0],
                        attack_residual=float(r[1]),
                        defense_residual=float(r[2]),
                        matches_considered=int(r[3]),
                    )
                    for r in form_rows
                }

                # Load all h2h snapshots for this competition (latest per pair)
                h2h_rows = (await session.execute(sql_text("""
                    SELECT DISTINCT ON (home_team, away_team)
                        home_team, away_team, h2h_multiplier, matches_considered
                    FROM h2h_snapshots
                    WHERE competition = :comp
                    ORDER BY home_team, away_team, as_of_date DESC
                """), {"comp": comp})).all()
                h2h_by_pair: dict[tuple[str, str], H2HSignal] = {
                    (r[0], r[1]): H2HSignal(
                        home_team=r[0],
                        away_team=r[1],
                        multiplier=float(r[2]),
                        matches_considered=int(r[3]),
                    )
                    for r in h2h_rows
                }

                # Upcoming matches
                fixtures = (await session.execute(sql_text("""
                    SELECT id::text, home_team, away_team, kickoff_at
                    FROM matches
                    WHERE status = 'scheduled'
                      AND competition = :comp
                    ORDER BY kickoff_at ASC
                """), {"comp": comp})).all()

                log.info(
                    "v2.fixtures_found",
                    competition=comp, count=len(fixtures),
                    form_teams=len(form_by_team),
                    h2h_pairs=len(h2h_by_pair),
                )

                saved = 0
                failed = 0
                for match_id, home, away, kickoff in fixtures:
                    try:
                        pred = predict_match_v2(
                            fit,
                            home_team=home,
                            away_team=away,
                            home_form=form_by_team.get(home),
                            away_form=form_by_team.get(away),
                            h2h=h2h_by_pair.get((home, away)),
                        )
                    except Exception as exc:
                        failed += 1
                        log.warning(
                            "v2.predict_failed",
                            match_id=match_id[:8], home=home, away=away,
                            error=str(exc)[:200],
                        )
                        continue

                    raw_payload = {
                        "config_hash": config_hash,
                        "form_home_matches": (
                            pred.home_form.matches_considered if pred.home_form else None
                        ),
                        "form_away_matches": (
                            pred.away_form.matches_considered if pred.away_form else None
                        ),
                        "h2h_matches": pred.h2h.matches_considered if pred.h2h else None,
                        "form_shift_home": pred.form_shift_home,
                        "form_shift_away": pred.form_shift_away,
                        "h2h_multiplier": pred.h2h_multiplier,
                        "base_home_goals": pred.base.expected_home_goals,
                        "base_away_goals": pred.base.expected_away_goals,
                        "adjusted_home_goals": pred.adjusted_expected_home_goals,
                        "adjusted_away_goals": pred.adjusted_expected_away_goals,
                        "most_likely_score": pred.most_likely_score,
                    }

                    await session.execute(sql_text("""
                        INSERT INTO external_predictions (
                            source_id, match_id,
                            prob_home_win, prob_draw, prob_away_win,
                            prob_over_2_5, raw_payload, fetched_at
                        ) VALUES (
                            :sid, :mid,
                            :ph, :pd, :pa,
                            :pou, CAST(:raw AS jsonb), now()
                        )
                        ON CONFLICT (source_id, match_id) DO UPDATE
                        SET prob_home_win = EXCLUDED.prob_home_win,
                            prob_draw = EXCLUDED.prob_draw,
                            prob_away_win = EXCLUDED.prob_away_win,
                            prob_over_2_5 = EXCLUDED.prob_over_2_5,
                            raw_payload = EXCLUDED.raw_payload,
                            fetched_at = now()
                    """), {
                        "sid": source_id, "mid": match_id,
                        "ph": pred.prob_home_win,
                        "pd": pred.prob_draw,
                        "pa": pred.prob_away_win,
                        "pou": pred.prob_over_2_5,
                        "raw": json.dumps(raw_payload),
                    })
                    saved += 1

                await session.commit()
                by_comp[comp] = saved
                total_upserts += saved
                log.info(
                    "v2.predictions_saved",
                    competition=comp, saved=saved, failed=failed,
                )
    finally:
        await engine.dispose()

    return {"total_upserts": total_upserts, "by_competition": by_comp}


@celery_app.task(name="tasks.run_v2_predictions", bind=True)
def run_v2_predictions(self) -> dict:  # type: ignore[no-untyped-def]
    """Daily: write dixon_coles_v2 predictions to external_predictions."""
    return asyncio.run(_run_v2_async())
'''


def step1_write_task() -> None:
    if TASK_FILE.exists():
        print(f"  ↷ {TASK_FILE.name} already exists")
        return
    TASK_FILE.write_text(TASK_SRC)
    print(f"  ✓ wrote {TASK_FILE.relative_to(ROOT)}")


def step2_register() -> None:
    src = TASKS_INIT.read_text()
    if "prediction_v2" in src:
        print(f"  ↷ {TASKS_INIT.name}: already registered")
        return
    src = src.rstrip() + "\nfrom apps.worker.tasks import prediction_v2  # noqa: F401\n"
    TASKS_INIT.write_text(src)
    print(f"  ✓ {TASKS_INIT.name}: registered")


def step3_beat_entry() -> None:
    _patch(
        BEAT,
        """        # Refresh per-pair H2H snapshots 15 min later (01:15 UTC)
        "refresh-h2h-snapshots": {
            "task": "tasks.refresh_h2h_snapshots",
            "schedule": crontab(hour="1", minute="15"),
        },""",
        """        # Refresh per-pair H2H snapshots 15 min later (01:15 UTC)
        "refresh-h2h-snapshots": {
            "task": "tasks.refresh_h2h_snapshots",
            "schedule": crontab(hour="1", minute="15"),
        },
        # Run dixon_coles_v2 predictions for upcoming fixtures at 02:00 UTC
        # (after form + H2H snapshots have refreshed)
        "run-v2-predictions": {
            "task": "tasks.run_v2_predictions",
            "schedule": crontab(hour="2", minute="0"),
        },""",
        "run-v2-predictions beat entry",
    )


def step4_run_and_verify() -> bool:
    import asyncio

    async def _run() -> bool:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
        sys.path.insert(0, str(ROOT))
        from packages.core.config import get_settings
        from apps.worker.tasks.prediction_v2 import _run_v2_async

        engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        try:
            async with factory() as s:
                pre = (await s.execute(text("""
                    SELECT COUNT(*) FROM external_predictions ep
                    JOIN external_prediction_sources eps ON eps.id = ep.source_id
                    WHERE eps.slug = :slug
                """), {"slug": SOURCE_SLUG})).scalar()
            print(f"  dixon_coles_v2 rows before: {pre}")

            print("  running run_v2_predictions()...")
            result = await _run_v2_async()
            print(f"    {result}")

            async with factory() as s:
                post = (await s.execute(text("""
                    SELECT COUNT(*) FROM external_predictions ep
                    JOIN external_prediction_sources eps ON eps.id = ep.source_id
                    WHERE eps.slug = :slug
                """), {"slug": SOURCE_SLUG})).scalar()
                sample = (await s.execute(text("""
                    SELECT m.home_team, m.away_team,
                           ep.prob_home_win, ep.prob_draw, ep.prob_away_win,
                           ep.prob_over_2_5,
                           ep.raw_payload->>'h2h_multiplier' AS h2h_mult,
                           ep.raw_payload->>'form_shift_home' AS form_h,
                           ep.fetched_at
                    FROM external_predictions ep
                    JOIN external_prediction_sources eps ON eps.id = ep.source_id
                    JOIN matches m ON m.id = ep.match_id
                    WHERE eps.slug = :slug
                    ORDER BY ep.fetched_at DESC
                    LIMIT 5
                """), {"slug": SOURCE_SLUG})).all()

            print(f"  dixon_coles_v2 rows after:  {post}")
            print()
            print(f"  sample of 5:")
            for r in sample:
                print(f"    {r[0][:20]:20} vs {r[1][:20]:20} "
                      f"H/D/A={r[2]:.3f}/{r[3]:.3f}/{r[4]:.3f} "
                      f"O2.5={r[5]:.3f} h2h={r[6]} formH={r[7]}")

            ok = result.get("total_upserts", 0) > 0 and post > 0
            return ok
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def main() -> None:
    print("=" * 70)
    print("SignalIQ — B.3: dixon_coles_v2 prediction writer")
    print("=" * 70)

    print("\n[1/4] Write apps/worker/tasks/prediction_v2.py")
    step1_write_task()

    print("\n[2/4] Register in apps/worker/tasks/__init__.py")
    step2_register()

    print("\n[3/4] Add beat entry (02:00 UTC daily)")
    step3_beat_entry()

    print("\n[4/4] Run once + verify")
    ok = step4_run_and_verify()

    print("=" * 70)
    if ok:
        print("✅ B.3 complete — dixon_coles_v2 produces predictions")
        print("=" * 70)
        sys.exit(0)
    print("❌ B.3 verification failed — see above")
    print("=" * 70)
    sys.exit(1)


if __name__ == "__main__":
    main()
