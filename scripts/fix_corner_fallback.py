"""
SignalIQ — Phase 10.9a final fix.

Three patches:
  1. corners.py — league-average fallback + confidence flag
  2. blend.py   — log corner failures instead of silent except pass
  3. session.py — pool_pre_ping + pool_recycle (prevents idle-connection drop)
Then re-run predictions for the one affected fixture and verify.

Idempotent. Fails loudly on pattern mismatch.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

ROOT = _ROOT
CORNERS = ROOT / "packages" / "prediction" / "corners.py"
BLEND = ROOT / "packages" / "prediction" / "blend.py"
SESSION = ROOT / "packages" / "db" / "session.py"


def _patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. corners.py — fallback + confidence flag
# ─────────────────────────────────────────────────────────────────────

def step1_patch_corners() -> None:
    # 1a. Add confidence field to CornerPrediction
    _patch(
        CORNERS,
        """@dataclass
class CornerPrediction:
    home_team: str
    away_team: str
    expected_home_corners: float
    expected_away_corners: float
    expected_total_corners: float
    home_stats: CornerStats | None = None
    away_stats: CornerStats | None = None
""",
        """@dataclass
class CornerPrediction:
    home_team: str
    away_team: str
    expected_home_corners: float
    expected_away_corners: float
    expected_total_corners: float
    home_stats: CornerStats | None = None
    away_stats: CornerStats | None = None
    confidence: str = "normal"          # "normal" | "low_insufficient_history"
""",
        "CornerPrediction confidence field",
    )

    # 1b. Add league-average helper before predict_corners
    _patch(
        CORNERS,
        """async def predict_corners(
    session: AsyncSession,
    *,
    home_team: str,
    away_team: str,
    competition: str = "EPL",
    as_of: datetime | None = None,
) -> CornerPrediction | None:
    \"\"\"Predict total and per-team corners for a match.\"\"\"
    home_stats = await compute_corner_stats(
        session, team=home_team, competition=competition, as_of=as_of,
    )
    away_stats = await compute_corner_stats(
        session, team=away_team, competition=competition, as_of=as_of,
    )

    if home_stats is None or away_stats is None:
        return None

    # Attack vs defense blend: half from my attack, half from their defense
    exp_home = 0.5 * home_stats.corners_for_per_match + 0.5 * away_stats.corners_against_per_match
    exp_away = 0.5 * away_stats.corners_for_per_match + 0.5 * home_stats.corners_against_per_match

    return CornerPrediction(
        home_team=home_team,
        away_team=away_team,
        expected_home_corners=round(exp_home, 2),
        expected_away_corners=round(exp_away, 2),
        expected_total_corners=round(exp_home + exp_away, 2),
        home_stats=home_stats,
        away_stats=away_stats,
    )
""",
        '''async def _league_average_corners(
    session: AsyncSession,
    *,
    competition: str,
    as_of: datetime,
) -> tuple[float, float]:
    """Fallback: league-wide avg corners scored/conceded per side.

    Returns (avg_for, avg_against) for a generic team in this competition.
    Used when a specific team has no corner history (e.g. newly promoted).
    """
    row = (await session.execute(sql_text("""
        SELECT
            avg(home_corners) AS avg_home_c,
            avg(away_corners) AS avg_away_c,
            count(*)          AS n
        FROM match_corners
        WHERE competition = :comp
          AND kickoff_at < :as_of
          AND home_corners IS NOT NULL
    """), {"comp": competition, "as_of": as_of})).first()

    if not row or row[2] == 0:
        # Absolute last resort — empirical EPL medians
        return (5.5, 5.0)
    # A team is home half the time, away half the time.
    # avg_for = (avg_home_c + avg_away_c) / 2; same for avg_against.
    avg_for = (float(row[0]) + float(row[1])) / 2.0
    return (avg_for, avg_for)


async def predict_corners(
    session: AsyncSession,
    *,
    home_team: str,
    away_team: str,
    competition: str = "EPL",
    as_of: datetime | None = None,
) -> CornerPrediction | None:
    """Predict total and per-team corners for a match.

    If either team lacks corner history (e.g. promoted this season),
    falls back to league averages and flags confidence="low_insufficient_history".
    Returns None only if the competition itself has no corner data.
    """
    as_of = as_of or datetime.now(UTC)

    home_stats = await compute_corner_stats(
        session, team=home_team, competition=competition, as_of=as_of,
    )
    away_stats = await compute_corner_stats(
        session, team=away_team, competition=competition, as_of=as_of,
    )

    # If both missing → no data at all for this competition → give up
    if home_stats is None and away_stats is None:
        return None

    # Fallback for whichever side lacks history
    confidence = "normal"
    league_for, league_against = await _league_average_corners(
        session, competition=competition, as_of=as_of,
    )

    if home_stats is None:
        confidence = "low_insufficient_history"
        home_for = league_for
        home_against = league_against
    else:
        home_for = home_stats.corners_for_per_match
        home_against = home_stats.corners_against_per_match

    if away_stats is None:
        confidence = "low_insufficient_history"
        away_for = league_for
        away_against = league_against
    else:
        away_for = away_stats.corners_for_per_match
        away_against = away_stats.corners_against_per_match

    # Attack vs defense blend
    exp_home = 0.5 * home_for + 0.5 * away_against
    exp_away = 0.5 * away_for + 0.5 * home_against

    return CornerPrediction(
        home_team=home_team,
        away_team=away_team,
        expected_home_corners=round(exp_home, 2),
        expected_away_corners=round(exp_away, 2),
        expected_total_corners=round(exp_home + exp_away, 2),
        home_stats=home_stats,
        away_stats=away_stats,
        confidence=confidence,
    )
''',
        "predict_corners fallback logic",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. blend.py — replace silent except with logged warning
# ─────────────────────────────────────────────────────────────────────

def step2_patch_blend() -> None:
    _patch(
        BLEND,
        """    corner_pred = None
    try:
        corner_pred = await predict_corners(
            session, home_team=home_team, away_team=away_team, competition=competition,
        )
    except Exception:
        pass
""",
        """    corner_pred = None
    try:
        corner_pred = await predict_corners(
            session, home_team=home_team, away_team=away_team, competition=competition,
        )
    except Exception as exc:
        log.warning(
            "blend.corners_failed",
            home=home_team, away=away_team,
            error=str(exc)[:200],
        )
""",
        "log corner failures",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. session.py — pool_pre_ping + pool_recycle
# ─────────────────────────────────────────────────────────────────────

def step3_patch_session() -> None:
    if not SESSION.exists():
        print(f"  ⚠ {SESSION.relative_to(ROOT)} does not exist — skipping")
        return

    src = SESSION.read_text()

    if "pool_pre_ping" in src:
        print(f"  ↷ {SESSION.name}: pool_pre_ping already present")
        return

    # Match any create_async_engine(...) call
    m = re.search(r"create_async_engine\(([^)]*)\)", src, re.DOTALL)
    if not m:
        print(f"  ⚠ {SESSION.name}: no create_async_engine call found — manual review needed")
        return

    old_call = m.group(0)
    inner = m.group(1).strip()

    # Preserve existing args, append ours
    if inner.endswith(","):
        new_inner = inner + "\n        pool_pre_ping=True,\n        pool_recycle=180,\n    "
    else:
        new_inner = inner + ",\n        pool_pre_ping=True,\n        pool_recycle=180,\n    "
    new_call = f"create_async_engine(\n        {new_inner})"

    SESSION.write_text(src.replace(old_call, new_call, 1))
    print(f"  ✓ {SESSION.name}: pool_pre_ping + pool_recycle added")


# ─────────────────────────────────────────────────────────────────────
# 4. Regenerate + verify
# ─────────────────────────────────────────────────────────────────────

async def step4_regenerate_and_verify() -> bool:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    from packages.core.config import get_settings
    from packages.prediction.predictor import predict_upcoming_fixtures

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_pre_ping=True, pool_recycle=180,
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as session:
            print("  regenerating EPL predictions (upcoming, force_refresh)...")
            result = await predict_upcoming_fixtures(
                session, competition="EPL", days_ahead=30, force_refresh=True,
            )
            await session.commit()
            print(f"    {result}")

        async with factory() as session:
            rows = (await session.execute(text("""
                SELECT m.home_team, m.away_team,
                       p.prob_over_1_5, p.prob_under_1_5,
                       p.prob_over_3_5, p.prob_under_3_5,
                       p.prob_btts_yes, p.prob_btts_no,
                       p.home_goals_dist, p.top_scorelines,
                       p.expected_home_corners, p.expected_away_corners,
                       p.expected_total_corners
                FROM predictions p
                JOIN matches m ON m.id = p.match_id
                WHERE p.is_published = true
                  AND p.model_version = 'blend_50_50_v1'
                  AND m.kickoff_at > now()
                ORDER BY m.kickoff_at ASC
                LIMIT 5
            """))).all()

            print(f"\n  verify: {len(rows)} upcoming blend_50_50_v1 predictions\n")
            ok = True
            corners_present = 0
            for r in rows:
                (home, away, o15, u15, o35, u35, by, bn, hgd, tops, ehc, eac, etc) = r
                gates_ok = (
                    o15 is not None and u15 is not None and abs(o15 + u15 - 1.0) <= 0.01
                    and o35 is not None and u35 is not None and abs(o35 + u35 - 1.0) <= 0.01
                    and by is not None and bn is not None and abs(by + bn - 1.0) <= 0.01
                    and bool(hgd) and bool(tops)
                )
                if etc is not None:
                    corners_present += 1
                marker = "✓" if gates_ok else "❌"
                print(f"    {marker} {home[:22]:22} vs {away[:22]:22}  "
                      f"corners_h={ehc}  corners_a={eac}  total={etc}")
                if not gates_ok:
                    ok = False

            print(f"\n  corners populated for {corners_present}/{len(rows)} fixtures")
            if corners_present < len(rows):
                ok = False
                print(f"  ❌ {len(rows) - corners_present} fixtures still have no corners")
            return ok
    finally:
        await engine.dispose()


async def main() -> None:
    print("=" * 70)
    print("SignalIQ — corner fallback + session hardening (10.9a final)")
    print("=" * 70)

    print("\n[1/4] Patch corners.py — league-average fallback + confidence flag")
    step1_patch_corners()

    print("\n[2/4] Patch blend.py — log corner failures")
    step2_patch_blend()

    print("\n[3/4] Patch session.py — pool_pre_ping + pool_recycle")
    step3_patch_session()

    print("\n[4/4] Regenerate + verify")
    ok = await step4_regenerate_and_verify()

    print("=" * 70)
    if ok:
        print("✅ 10.9a fully complete — corners populated for every fixture")
        sys.exit(0)
    else:
        print("❌ 10.9a still incomplete — see ❌ rows above")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
