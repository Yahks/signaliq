"""
SignalIQ — Fix prediction extended fields (Phase 10.9a, complete).

What this does, in order:
  1. Writes alembic/versions/0022_prediction_under_and_btts_no.py
  2. Patches packages/prediction/predictor.py:
       - INSERT column list  (add 3 _no columns)
       - VALUES placeholders (add 3)
       - params dict         (add 3)
       - ON CONFLICT SET     (add 12 extended columns)
       - blend_prediction()  (pass competition=competition)
  3. Patches packages/prediction/blend.py:
       - competition becomes required (no = "EPL" default)
  4. Runs alembic upgrade head
  5. Regenerates predictions for UPCOMING fixtures only
  6. Verifies extended fields populated + over+under ≈ 1.0

Idempotent: safe to re-run. Fails loudly on any pattern mismatch.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

# Ensure project root is importable regardless of where the script is run from
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

ROOT = Path.cwd()
MIGRATION_PATH = ROOT / "alembic" / "versions" / "0022_prediction_under_and_btts_no.py"
PREDICTOR = ROOT / "packages" / "prediction" / "predictor.py"
BLEND = ROOT / "packages" / "prediction" / "blend.py"


# ─────────────────────────────────────────────────────────────────────
# Step 1 — write migration 0022
# ─────────────────────────────────────────────────────────────────────

MIGRATION_SRC = '''"""Add prob_under_1_5, prob_under_3_5, prob_btts_no to predictions.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("predictions", sa.Column("prob_under_1_5", sa.Float, nullable=True))
    op.add_column("predictions", sa.Column("prob_under_3_5", sa.Float, nullable=True))
    op.add_column("predictions", sa.Column("prob_btts_no", sa.Float, nullable=True))


def downgrade() -> None:
    for col in ["prob_btts_no", "prob_under_3_5", "prob_under_1_5"]:
        op.drop_column("predictions", col)
'''


def step1_write_migration() -> None:
    if MIGRATION_PATH.exists():
        print(f"  ↷ {MIGRATION_PATH.relative_to(ROOT)} already exists — skipping")
        return
    MIGRATION_PATH.write_text(MIGRATION_SRC)
    print(f"  ✓ wrote {MIGRATION_PATH.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# Step 2 — patch predictor.py
# ─────────────────────────────────────────────────────────────────────

def _patch(src: str, old: str, new: str, label: str) -> str:
    if new.strip() in src and old not in src:
        print(f"  ↷ predictor.py: {label} already patched")
        return src
    if old not in src:
        raise SystemExit(
            f"\n❌ predictor.py: pattern not found for '{label}'\n"
            f"   Expected to find:\n---\n{old}\n---\n"
            f"   File may have changed. Paste this message back."
        )
    return src.replace(old, new, 1)


def step2_patch_predictor() -> None:
    src = PREDICTOR.read_text()
    orig = src

    # --- 2a. INSERT column list ---
    src = _patch(
        src,
        "                prob_over_1_5, prob_over_3_5, prob_btts_yes,\n"
        "                home_goals_dist, away_goals_dist, top_scorelines,\n"
        "                expected_home_corners, expected_away_corners, expected_total_corners,\n",
        "                prob_over_1_5, prob_under_1_5, prob_over_3_5, prob_under_3_5,\n"
        "                prob_btts_yes, prob_btts_no,\n"
        "                home_goals_dist, away_goals_dist, top_scorelines,\n"
        "                expected_home_corners, expected_away_corners, expected_total_corners,\n",
        "INSERT columns",
    )

    # --- 2b. VALUES placeholders ---
    src = _patch(
        src,
        "                :pov15, :pov35, :pbtts,\n",
        "                :pov15, :pun15, :pov35, :pun35, :pbtts, :pbttsno,\n",
        "VALUES placeholders",
    )

    # --- 2c. params dict ---
    src = _patch(
        src,
        '            "pov15": bp.prob_over_1_5,\n'
        '            "pov35": bp.prob_over_3_5,\n'
        '            "pbtts": bp.prob_btts_yes,\n',
        '            "pov15": bp.prob_over_1_5,\n'
        '            "pun15": bp.prob_under_1_5,\n'
        '            "pov35": bp.prob_over_3_5,\n'
        '            "pun35": bp.prob_under_3_5,\n'
        '            "pbtts": bp.prob_btts_yes,\n'
        '            "pbttsno": bp.prob_btts_no,\n',
        "params dict",
    )

    # --- 2d. ON CONFLICT SET — the actual bug ---
    src = _patch(
        src,
        "                prob_over_2_5 = EXCLUDED.prob_over_2_5,\n"
        "                prob_under_2_5 = EXCLUDED.prob_under_2_5,\n"
        "                confidence = EXCLUDED.confidence,\n",
        "                prob_over_2_5 = EXCLUDED.prob_over_2_5,\n"
        "                prob_under_2_5 = EXCLUDED.prob_under_2_5,\n"
        "                prob_over_1_5 = EXCLUDED.prob_over_1_5,\n"
        "                prob_under_1_5 = EXCLUDED.prob_under_1_5,\n"
        "                prob_over_3_5 = EXCLUDED.prob_over_3_5,\n"
        "                prob_under_3_5 = EXCLUDED.prob_under_3_5,\n"
        "                prob_btts_yes = EXCLUDED.prob_btts_yes,\n"
        "                prob_btts_no = EXCLUDED.prob_btts_no,\n"
        "                home_goals_dist = EXCLUDED.home_goals_dist,\n"
        "                away_goals_dist = EXCLUDED.away_goals_dist,\n"
        "                top_scorelines = EXCLUDED.top_scorelines,\n"
        "                expected_home_corners = EXCLUDED.expected_home_corners,\n"
        "                expected_away_corners = EXCLUDED.expected_away_corners,\n"
        "                expected_total_corners = EXCLUDED.expected_total_corners,\n"
        "                confidence = EXCLUDED.confidence,\n",
        "ON CONFLICT SET",
    )

    # --- 2e. pass competition=competition to blend_prediction ---
    src = _patch(
        src,
        "            bp = await blend_prediction(\n"
        "                session, fit,\n"
        "                match_id=match_id,\n"
        "                home_team=home,\n"
        "                away_team=away,\n"
        "            )",
        "            bp = await blend_prediction(\n"
        "                session, fit,\n"
        "                match_id=match_id,\n"
        "                home_team=home,\n"
        "                away_team=away,\n"
        "                competition=competition,\n"
        "            )",
        "blend_prediction competition",
    )

    if src != orig:
        PREDICTOR.write_text(src)
        print("  ✓ predictor.py patched")
    else:
        print("  ↷ predictor.py unchanged (already correct)")


# ─────────────────────────────────────────────────────────────────────
# Step 3 — patch blend.py (competition required, no default)
# ─────────────────────────────────────────────────────────────────────

def step3_patch_blend() -> None:
    src = BLEND.read_text()

    if "    competition: str = \"EPL\",\n" not in src and "    competition: str,\n" in src:
        print("  ↷ blend.py: competition already required")
        return

    old = "    competition: str = \"EPL\",\n"
    new = "    competition: str,\n"

    if old not in src:
        raise SystemExit(
            "\n❌ blend.py: 'competition: str = \"EPL\",' not found.\n"
            "   Paste the signature of blend_prediction back."
        )
    BLEND.write_text(src.replace(old, new, 1))
    print("  ✓ blend.py: competition is now required")


# ─────────────────────────────────────────────────────────────────────
# Step 4 — alembic upgrade head
# ─────────────────────────────────────────────────────────────────────

def step4_migrate() -> None:
    r = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if r.returncode != 0:
        raise SystemExit(f"\n❌ alembic upgrade head failed:\n{r.stderr}")
    print("  ✓ alembic upgrade head")
    for line in r.stderr.splitlines():
        if "Running upgrade" in line:
            print(f"      {line}")


# ─────────────────────────────────────────────────────────────────────
# Step 5 — regenerate upcoming fixtures
# ─────────────────────────────────────────────────────────────────────

async def step5_regenerate() -> dict:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    from packages.core.config import get_settings
    from packages.prediction.predictor import predict_upcoming_fixtures

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as session:
            result = await predict_upcoming_fixtures(
                session, competition="EPL", days_ahead=30, force_refresh=True,
            )
            await session.commit()
            return result if isinstance(result, dict) else {"result": result}
    finally:
        await engine.dispose()


# ─────────────────────────────────────────────────────────────────────
# Step 6 — verify extended fields landed
# ─────────────────────────────────────────────────────────────────────

def _model_version() -> str:
    """Read MODEL_VERSION from predictor.py so verify stays in sync."""
    import re
    m = re.search(r'MODEL_VERSION\s*=\s*["\']([^"\']+)["\']', PREDICTOR.read_text())
    return m.group(1) if m else "blend_50_50_v1"


async def step6_verify() -> bool:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    ok = True
    try:
        async with factory() as session:
            rows = (await session.execute(text("""
                SELECT
                    m.home_team, m.away_team,
                    p.prob_over_1_5,  p.prob_under_1_5,
                    p.prob_over_3_5,  p.prob_under_3_5,
                    p.prob_btts_yes,  p.prob_btts_no,
                    p.home_goals_dist, p.away_goals_dist, p.top_scorelines,
                    p.expected_home_corners, p.expected_away_corners, p.expected_total_corners
                FROM predictions p
                JOIN matches m ON m.id = p.match_id
                WHERE p.is_published = true
                  AND p.model_version = :mv
                  AND m.kickoff_at > now()
                ORDER BY m.kickoff_at ASC
                LIMIT 3
            """), {"mv": _model_version()})).all()

            if not rows:
                print("  ❌ no upcoming published predictions found")
                return False

            print(f"  Sample of {len(rows)} upcoming predictions (model_version={_model_version()}):\n")
            for r in rows:
                (home, away,
                 o15, u15, o35, u35, btts_y, btts_n,
                 hgd, agd, tops,
                 ehc, eac, etc) = r

                print(f"    {home[:22]:22} vs {away[:22]:22}")
                print(f"      O1.5={o15}  U1.5={u15}  O3.5={o35}  U3.5={u35}")
                print(f"      BTTS_y={btts_y}  BTTS_n={btts_n}")
                print(f"      goals_dist: home={hgd}  away={agd}")
                print(f"      top_scorelines: {tops}")
                print(f"      corners: home={ehc}  away={eac}  total={etc}")

                # Data-quality gates
                if o15 is None or u15 is None:
                    print("      ❌ O/U1.5 missing"); ok = False
                elif abs((o15 + u15) - 1.0) > 0.01:
                    print(f"      ❌ O1.5+U1.5 = {o15 + u15:.4f} ≠ 1.0"); ok = False

                if o35 is None or u35 is None:
                    print("      ❌ O/U3.5 missing"); ok = False
                elif abs((o35 + u35) - 1.0) > 0.01:
                    print(f"      ❌ O3.5+U3.5 = {o35 + u35:.4f} ≠ 1.0"); ok = False

                if btts_y is None or btts_n is None:
                    print("      ❌ BTTS y/n missing"); ok = False
                elif abs((btts_y + btts_n) - 1.0) > 0.01:
                    print(f"      ❌ BTTS_y+BTTS_n = {btts_y + btts_n:.4f} ≠ 1.0"); ok = False

                if not hgd or not agd:
                    print("      ❌ goals distributions empty"); ok = False
                if not tops:
                    print("      ❌ top_scorelines empty"); ok = False
                if etc is None:
                    print("      ❌ total corners missing"); ok = False

                print()
    finally:
        await engine.dispose()

    return ok


# ─────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("=" * 70)
    print("SignalIQ — fix prediction extended fields (Phase 10.9a)")
    print("=" * 70)

    print("\n[1/6] Write migration 0022")
    step1_write_migration()

    print("\n[2/6] Patch predictor.py")
    step2_patch_predictor()

    print("\n[3/6] Patch blend.py (competition required)")
    step3_patch_blend()

    print("\n[4/6] Apply migrations")
    step4_migrate()

    print("\n[5/6] Regenerate upcoming predictions")
    result = await step5_regenerate()
    for k, v in result.items():
        print(f"    {k}: {v}")

    print("\n[6/6] Verify extended fields")
    ok = await step6_verify()

    print("=" * 70)
    if ok:
        print("✅ 10.9a complete — all extended fields populated, gates passed")
        print("=" * 70)
        sys.exit(0)
    else:
        print("❌ 10.9a INCOMPLETE — see failures above")
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
