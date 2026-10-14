"""
SignalIQ — Phase 10.9b Stage 1.5.

Write corner confidence + extended market metadata into predictions.inputs_snapshot
so the API and UI can surface "~10.5 (low confidence)" for promoted teams.

One patch to predictor.py, then regenerate + verify.
Idempotent. Fails loudly.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

ROOT = _ROOT
PREDICTOR = ROOT / "packages" / "prediction" / "predictor.py"


def _patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    print(f"  ✓ {path.name}: {label}")


def step1_patch_predictor() -> None:
    # Extend the inputs_snapshot dict with corners + extended-market metadata.
    _patch(
        PREDICTOR,
        """        inputs_snapshot = {
            "home_team": home,
            "away_team": away,
            "home_attack": fit.attack[home],
            "home_defense": fit.defense[home],
            "away_attack": fit.attack[away],
            "away_defense": fit.defense[away],
            "home_advantage": fit.home_advantage,
            "rho": fit.rho,
            "n_training": fit.n_matches,
            "blend_mode": bp.blend_mode,
            "model_probs": {
                "home": bp.model.prob_home_win if bp.model else None,
                "draw": bp.model.prob_draw if bp.model else None,
                "away": bp.model.prob_away_win if bp.model else None,
            },
            "market_probs": {
                "home": bp.market.prob_home_win if bp.market else None,
                "draw": bp.market.prob_draw if bp.market else None,
                "away": bp.market.prob_away_win if bp.market else None,
                "bookmakers": bp.market.bookmakers_used if bp.market else None,
            },
        }""",
        """        inputs_snapshot = {
            "home_team": home,
            "away_team": away,
            "home_attack": fit.attack[home],
            "home_defense": fit.defense[home],
            "away_attack": fit.attack[away],
            "away_defense": fit.defense[away],
            "home_advantage": fit.home_advantage,
            "rho": fit.rho,
            "n_training": fit.n_matches,
            "blend_mode": bp.blend_mode,
            "model_probs": {
                "home": bp.model.prob_home_win if bp.model else None,
                "draw": bp.model.prob_draw if bp.model else None,
                "away": bp.model.prob_away_win if bp.model else None,
            },
            "market_probs": {
                "home": bp.market.prob_home_win if bp.market else None,
                "draw": bp.market.prob_draw if bp.market else None,
                "away": bp.market.prob_away_win if bp.market else None,
                "bookmakers": bp.market.bookmakers_used if bp.market else None,
            },
            # Phase 10.9a/b — extended market + corner provenance
            "extended_markets": {
                "source": "scoreline_model",
                "prob_over_1_5": bp.prob_over_1_5,
                "prob_over_3_5": bp.prob_over_3_5,
                "prob_btts_yes": bp.prob_btts_yes,
            },
            "corners": {
                "confidence": getattr(bp, "_corners_confidence", None),
                "expected_home": bp.expected_home_corners,
                "expected_away": bp.expected_away_corners,
                "expected_total": bp.expected_total_corners,
            },
        }""",
        "inputs_snapshot extended metadata",
    )


def step2_patch_blend_corner_confidence() -> None:
    """Attach corner confidence to BlendPrediction so predictor.py can read it."""
    BLEND = ROOT / "packages" / "prediction" / "blend.py"
    _patch(
        BLEND,
        """        expected_home_corners=corner_pred.expected_home_corners if corner_pred else None,
        expected_away_corners=corner_pred.expected_away_corners if corner_pred else None,
        expected_total_corners=corner_pred.expected_total_corners if corner_pred else None,""",
        """        expected_home_corners=corner_pred.expected_home_corners if corner_pred else None,
        expected_away_corners=corner_pred.expected_away_corners if corner_pred else None,
        expected_total_corners=corner_pred.expected_total_corners if corner_pred else None,
        # Store corner confidence for snapshot / UI low-confidence badge
        _corners_confidence=(corner_pred.confidence if corner_pred else None),""",
        "corner confidence on BlendPrediction",
    )

    # Declare the field on the dataclass (private, so it stays internal)
    _patch(
        BLEND,
        """    model: Prediction | None = None
    market: MarketOdds | None = None
    blend_mode: str = "model_only"
    confidence: str = "low"
    data_quality: float | None = None""",
        """    model: Prediction | None = None
    market: MarketOdds | None = None
    blend_mode: str = "model_only"
    confidence: str = "low"
    data_quality: float | None = None
    # Internal — surfaces corner prediction confidence for snapshots/UI
    _corners_confidence: str | None = None""",
        "BlendPrediction _corners_confidence field",
    )


async def step3_regenerate_and_verify() -> bool:
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
            print("  regenerating EPL predictions...")
            result = await predict_upcoming_fixtures(
                session, competition="EPL", days_ahead=30, force_refresh=True,
            )
            await session.commit()
            print(f"    {result}")

        async with factory() as session:
            rows = (await session.execute(text("""
                SELECT
                    m.home_team, m.away_team,
                    p.inputs_snapshot->'corners'->>'confidence' AS c_conf,
                    p.expected_total_corners,
                    p.inputs_snapshot->'extended_markets'->>'source' AS ext_source
                FROM predictions p
                JOIN matches m ON m.id = p.match_id
                WHERE p.is_published = true
                  AND p.model_version = 'blend_50_50_v1'
                  AND m.kickoff_at > now()
                ORDER BY m.kickoff_at ASC
                LIMIT 8
            """))).all()

            print(f"\n  snapshot corners metadata on {len(rows)} predictions:\n")
            normal_count = 0
            low_count = 0
            for r in rows:
                home, away, c_conf, etc, ext_source = r
                if c_conf == "low_insufficient_history":
                    low_count += 1
                    marker = "~"
                elif c_conf == "normal":
                    normal_count += 1
                    marker = " "
                else:
                    marker = "?"
                print(f"    {marker} {home[:22]:22} vs {away[:22]:22}  "
                      f"corners={etc}  conf={c_conf}  src={ext_source}")
            print(f"\n  normal={normal_count}  low_insufficient={low_count}")
            return normal_count > 0 and low_count > 0
    finally:
        await engine.dispose()


async def main() -> None:
    print("=" * 70)
    print("SignalIQ — Stage 1.5: snapshot corner + extended-market metadata")
    print("=" * 70)

    print("\n[1/3] Patch predictor.py — extend inputs_snapshot")
    step1_patch_predictor()

    print("\n[2/3] Patch blend.py — attach corner confidence")
    step2_patch_blend_corner_confidence()

    print("\n[3/3] Regenerate + verify")
    ok = await step3_regenerate_and_verify()

    print("=" * 70)
    if ok:
        print("✅ Stage 1.5 complete — snapshot metadata flows through")
        sys.exit(0)
    else:
        print("❌ Stage 1.5 incomplete — see failures above")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
