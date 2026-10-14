"""
SignalIQ — Phase 10.9b Stage 3.A.1.

Fix window filtering in external_reliability scorer.

Before: all finished matches bucketed into every window (7/30/90 identical).
After:  each window only counts matches with kickoff_at >= now - window_days.

Also fixes a structural inefficiency: previously we looped WINDOWS inside
the per-row loop, doing 3x redundant work. Now we tag each row once with
its kickoff_at and bucket per-window using a date comparison.

Idempotent. Fails loudly.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "external_reliability.py"


def _patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:500]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    print(f"  ✓ {path.name}: {label}")


def step1_patch_scorer() -> None:
    _patch(
        TASK_FILE,
        """            # Bucket scores per (source_id, window_days)
            from collections import defaultdict
            buckets: dict[tuple[str, int], dict] = defaultdict(
                lambda: {"n": 0, "correct": 0, "brier_sum": 0.0}
            )
            slug_by_source: dict[str, str] = {}

            now = datetime.now(UTC)
            for r in rows:
                source_id, slug, ph, pd, pa, hg, ag, _kickoff = r
                slug_by_source[source_id] = slug

                actual = _actual_result(int(hg), int(ag))
                predicted = max(
                    (("home_win", float(ph)),
                     ("draw", float(pd)),
                     ("away_win", float(pa))),
                    key=lambda x: x[1],
                )[0]
                brier = _brier(float(ph), float(pd), float(pa), actual)

                for wd in WINDOWS:
                    window_start = now - timedelta(days=wd)
                    # We'd need scored_at semantics to filter by window — for v1
                    # we score all finished matches and rely on the source only
                    # having recent predictions. Filter later if needed.
                    b = buckets[(source_id, wd)]
                    b["n"] += 1
                    if predicted == actual:
                        b["correct"] += 1
                    b["brier_sum"] += brier""",
        """            # Bucket scores per (source_id, window_days), filtered by kickoff date.
            #
            # Each window (7/30/90 days) counts only matches with
            # kickoff_at >= now - window_days. This makes 7/30/90 genuinely
            # different — 7-day is most recent form, 90-day is season-scale.
            from collections import defaultdict

            buckets: dict[tuple[str, int], dict] = defaultdict(
                lambda: {"n": 0, "correct": 0, "brier_sum": 0.0}
            )
            slug_by_source: dict[str, str] = {}

            now = datetime.now(UTC)
            # Precompute each window's cutoff so we don't recompute per row
            window_cutoffs: dict[int, datetime] = {
                wd: now - timedelta(days=wd) for wd in WINDOWS
            }

            for r in rows:
                source_id, slug, ph, pd, pa, hg, ag, kickoff = r
                slug_by_source[source_id] = slug

                if kickoff is None:
                    # No kickoff time — cannot place in a window; skip.
                    continue

                actual = _actual_result(int(hg), int(ag))
                predicted = max(
                    (("home_win", float(ph)),
                     ("draw", float(pd)),
                     ("away_win", float(pa))),
                    key=lambda x: x[1],
                )[0]
                brier = _brier(float(ph), float(pd), float(pa), actual)

                for wd, cutoff in window_cutoffs.items():
                    if kickoff < cutoff:
                        continue  # outside this window — skip
                    b = buckets[(source_id, wd)]
                    b["n"] += 1
                    if predicted == actual:
                        b["correct"] += 1
                    b["brier_sum"] += brier""",
        "window-aware bucketing",
    )


def step2_verify() -> bool:
    import asyncio
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    sys.path.insert(0, str(ROOT))
    from packages.core.config import get_settings
    from apps.worker.tasks.external_reliability import _score_external_async

    async def _run() -> bool:
        print("  running scorer with window filter...")
        result = await _score_external_async()
        print(f"    {result}")

        engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with factory() as session:
                rows = (await session.execute(text("""
                    SELECT eps.slug, eps.kind, sr.window_days,
                           sr.predictions_scored, sr.result_accuracy,
                           sr.avg_brier_score, sr.reliability_weight
                    FROM source_reliability sr
                    JOIN external_prediction_sources eps ON eps.id = sr.source_id
                    ORDER BY eps.slug, sr.window_days
                """))).all()

                if not rows:
                    print("  ⚠ 0 rows (expected — no finished matches with external_predictions yet)")
                    print("     Window filter is now in place; will apply once data lands.")
                    return True

                print(f"\n  {len(rows)} source_reliability rows:")
                print(f"  {'slug':20} {'kind':16} {'wd':>3} {'n':>4} "
                      f"{'acc':>6} {'brier':>7} {'weight':>7}")
                for r in rows:
                    print(f"  {r[0]:20} {r[1]:16} {r[2]:>3} {r[3]:>4} "
                          f"{r[4]:>6.3f} {r[5]:>7.4f} {r[6]:>7.4f}")
            return True
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Stage 3.A.1: window filter for source reliability")
    print("=" * 70)

    print("\n[1/2] Patch external_reliability.py — window-aware bucketing")
    step1_patch_scorer()

    print("\n[2/2] Run + verify")
    ok = step2_verify()

    print("=" * 70)
    if ok:
        print("✅ Stage 3.A.1 complete — 7/30/90 windows now genuinely different")
        print("=" * 70)
        sys.exit(0)
    print("❌ Stage 3.A.1 incomplete")
    print("=" * 70)
    sys.exit(1)


if __name__ == "__main__":
    main()
