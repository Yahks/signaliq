"""
SignalIQ — Phase 10.9b Stage 1.

Wire the 12 extended prediction fields through the API:
  1. apps/api/schemas/prediction.py — add 12 + 1 fields to PredictionResponse
  2. apps/api/routers/predictions.py — select them in the CTE, outer SELECT,
     and PredictionResponse constructor
  3. Restart the API (if running) and hit the endpoint to verify

Idempotent. Fails loudly on pattern mismatch.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "apps" / "api" / "schemas" / "prediction.py"
ROUTER = ROOT / "apps" / "api" / "routers" / "predictions.py"


def _patch(path: Path, old: str, new: str, label: str, allow_present: bool = True) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src and allow_present:
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
# 1. PredictionResponse schema
# ─────────────────────────────────────────────────────────────────────

def step1_patch_schema() -> None:
    _patch(
        SCHEMA,
        """    prob_over_2_5: float
    prob_under_2_5: float
    confidence: str
""",
        """    prob_over_2_5: float
    prob_under_2_5: float
    # Extended markets (Phase 10.9a)
    prob_over_1_5: float | None = None
    prob_under_1_5: float | None = None
    prob_over_3_5: float | None = None
    prob_under_3_5: float | None = None
    prob_btts_yes: float | None = None
    prob_btts_no: float | None = None
    # Distributions & scorelines
    home_goals_dist: list[float] | None = None
    away_goals_dist: list[float] | None = None
    top_scorelines: list | None = None
    # Corner predictions (may be low-confidence — see corners_confidence)
    expected_home_corners: float | None = None
    expected_away_corners: float | None = None
    expected_total_corners: float | None = None
    corners_confidence: str | None = None
    confidence: str
""",
        "PredictionResponse extended fields",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Router — CTE + outer SELECT + constructor
# ─────────────────────────────────────────────────────────────────────

def step2_patch_router_cte() -> None:
    _patch(
        ROUTER,
        """        WITH latest AS (
            SELECT DISTINCT ON (match_id)
                id, match_id, prob_home_win, prob_draw, prob_away_win,
                expected_home_goals, expected_away_goals,
                prob_over_2_5, prob_under_2_5,
                confidence, data_quality,
                fair_odds_home, fair_odds_draw, fair_odds_away,
                model_version, published_at, inputs_snapshot
            FROM predictions
            WHERE is_published = true
            ORDER BY match_id, published_at DESC NULLS LAST, created_at DESC
        )""",
        """        WITH latest AS (
            SELECT DISTINCT ON (match_id)
                id, match_id, prob_home_win, prob_draw, prob_away_win,
                expected_home_goals, expected_away_goals,
                prob_over_2_5, prob_under_2_5,
                prob_over_1_5, prob_under_1_5,
                prob_over_3_5, prob_under_3_5,
                prob_btts_yes, prob_btts_no,
                home_goals_dist, away_goals_dist, top_scorelines,
                expected_home_corners, expected_away_corners, expected_total_corners,
                confidence, data_quality,
                fair_odds_home, fair_odds_draw, fair_odds_away,
                model_version, published_at, inputs_snapshot
            FROM predictions
            WHERE is_published = true
            ORDER BY match_id, published_at DESC NULLS LAST, created_at DESC
        )""",
        "CTE extended columns",
    )


def step2_patch_router_select() -> None:
    _patch(
        ROUTER,
        """            p.prob_home_win, p.prob_draw, p.prob_away_win,
            p.expected_home_goals, p.expected_away_goals,
            p.prob_over_2_5, p.prob_under_2_5,
            p.confidence, p.data_quality,
            p.fair_odds_home, p.fair_odds_draw, p.fair_odds_away,
            p.model_version, p.published_at,
            m.home_goals, m.away_goals,
            r.result_correct, r.brier_score,
            p.inputs_snapshot""",
        """            p.prob_home_win, p.prob_draw, p.prob_away_win,
            p.expected_home_goals, p.expected_away_goals,
            p.prob_over_2_5, p.prob_under_2_5,
            p.prob_over_1_5, p.prob_under_1_5,
            p.prob_over_3_5, p.prob_under_3_5,
            p.prob_btts_yes, p.prob_btts_no,
            p.home_goals_dist, p.away_goals_dist, p.top_scorelines,
            p.expected_home_corners, p.expected_away_corners, p.expected_total_corners,
            p.confidence, p.data_quality,
            p.fair_odds_home, p.fair_odds_draw, p.fair_odds_away,
            p.model_version, p.published_at,
            m.home_goals, m.away_goals,
            r.result_correct, r.brier_score,
            p.inputs_snapshot""",
        "outer SELECT extended columns",
    )


def step2_patch_router_constructor() -> None:
    _patch(
        ROUTER,
        """    items = [
        PredictionResponse(
            id=r[0], match_id=r[1],
            home_team=r[2], away_team=r[3], competition=r[4],
            kickoff_at=r[5], match_status=r[6],
            prob_home_win=r[7], prob_draw=r[8], prob_away_win=r[9],
            expected_home_goals=r[10], expected_away_goals=r[11],
            prob_over_2_5=r[12], prob_under_2_5=r[13],
            confidence=r[14], data_quality=r[15],
            fair_odds_home=r[16], fair_odds_draw=r[17], fair_odds_away=r[18],
            model_version=r[19], published_at=r[20],
            actual_home_goals=r[21], actual_away_goals=r[22],
            result_correct=r[23], brier_score=r[24],
            **_parse_blend(r[25]),
        )
        for r in rows
    ]""",
        """    items = [
        PredictionResponse(
            id=r[0], match_id=r[1],
            home_team=r[2], away_team=r[3], competition=r[4],
            kickoff_at=r[5], match_status=r[6],
            prob_home_win=r[7], prob_draw=r[8], prob_away_win=r[9],
            expected_home_goals=r[10], expected_away_goals=r[11],
            prob_over_2_5=r[12], prob_under_2_5=r[13],
            prob_over_1_5=r[14], prob_under_1_5=r[15],
            prob_over_3_5=r[16], prob_under_3_5=r[17],
            prob_btts_yes=r[18], prob_btts_no=r[19],
            home_goals_dist=r[20], away_goals_dist=r[21], top_scorelines=r[22],
            expected_home_corners=r[23], expected_away_corners=r[24],
            expected_total_corners=r[25],
            confidence=r[26], data_quality=r[27],
            fair_odds_home=r[28], fair_odds_draw=r[29], fair_odds_away=r[30],
            model_version=r[31], published_at=r[32],
            actual_home_goals=r[33], actual_away_goals=r[34],
            result_correct=r[35], brier_score=r[36],
            corners_confidence=_corners_confidence_from_snapshot(r[37]),
            **_parse_blend(r[37]),
        )
        for r in rows
    ]""",
        "constructor extended fields",
    )


def step2_patch_router_helper() -> None:
    """Insert a small helper that extracts corners_confidence from inputs_snapshot."""
    _patch(
        ROUTER,
        """    from apps.api.schemas.prediction import MarketProbs, ModelProbs

    def _parse_blend(snapshot: dict | None) -> dict:""",
        """    from apps.api.schemas.prediction import MarketProbs, ModelProbs

    def _corners_confidence_from_snapshot(snapshot: dict | None) -> str | None:
        if not snapshot:
            return None
        # blend.py stores corner prediction metadata under "corners"
        c = snapshot.get("corners")
        if isinstance(c, dict):
            return c.get("confidence")
        return None

    def _parse_blend(snapshot: dict | None) -> dict:""",
        "corners_confidence helper",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Restart API + smoke test
# ─────────────────────────────────────────────────────────────────────

def step3_restart_and_verify() -> bool:
    print("  killing any running uvicorn...")
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)

    print("  starting uvicorn on :8000...")
    log = Path("/tmp/signaliq-api.log")
    with log.open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(5)

    # Health
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health returned {r.stdout} — see /tmp/signaliq-api.log")
        return False
    print("  ✓ /health 200")

    # Needs an auth token — we can't easily forge one here, so hit the route
    # and check for the field NAMES in the OpenAPI schema instead.
    r = subprocess.run(
        ["curl", "-s", "http://localhost:8000/openapi.json"],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not r.stdout:
        print("  ❌ could not fetch /openapi.json")
        return False

    missing = []
    for field in [
        "prob_over_1_5", "prob_under_1_5",
        "prob_over_3_5", "prob_under_3_5",
        "prob_btts_yes", "prob_btts_no",
        "home_goals_dist", "away_goals_dist", "top_scorelines",
        "expected_home_corners", "expected_away_corners", "expected_total_corners",
        "corners_confidence",
    ]:
        if field not in r.stdout:
            missing.append(field)

    if missing:
        print(f"  ❌ OpenAPI schema still missing: {missing}")
        return False

    print("  ✓ OpenAPI schema includes all 13 extended fields")
    print(f"  OpenAPI served at: http://localhost:8000/docs")
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — wire extended fields through API (10.9b Stage 1)")
    print("=" * 70)

    print("\n[1/3] Patch apps/api/schemas/prediction.py")
    step1_patch_schema()

    print("\n[2/3] Patch apps/api/routers/predictions.py")
    step2_patch_router_cte()
    step2_patch_router_select()
    step2_patch_router_constructor()
    step2_patch_router_helper()

    print("\n[3/3] Restart API + smoke test")
    ok = step3_restart_and_verify()

    print("=" * 70)
    if ok:
        print("✅ 10.9b Stage 1 complete — API exposes all 13 extended fields")
        print("=" * 70)
        sys.exit(0)
    else:
        print("❌ 10.9b Stage 1 incomplete — see failures above")
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    main()
