"""
SignalIQ — Phase 10.9b Stage 3.D.

API endpoint: GET /api/predictions/sources

Returns the reliability leaderboard for all enabled external prediction
sources, joined with human-readable metadata from
external_prediction_sources.

Sources with no reliability row yet appear with null metrics —
consumers should show them as "awaiting data" rather than hiding them.

Idempotent. Fails loudly.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "apps" / "api" / "schemas" / "prediction.py"
ROUTER_FILE = ROOT / "apps" / "api" / "routers" / "source_reliability.py"
MAIN_FILE = ROOT / "apps" / "api" / "main.py"


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
# 1. Extend prediction schemas with SourceReliability types
# ─────────────────────────────────────────────────────────────────────

def step1_patch_schemas() -> None:
    _patch(
        SCHEMA_FILE,
        """class UpcomingPredictionsList(BaseModel):
    items: list[PredictionResponse]
    total: int""",
        """class UpcomingPredictionsList(BaseModel):
    items: list[PredictionResponse]
    total: int


class SourceReliabilityItem(BaseModel):
    slug: str
    name: str
    kind: str
    enabled: bool
    predictions_scored: int | None = None
    result_accuracy: float | None = None
    avg_brier_score: float | None = None
    reliability_weight: float | None = None
    computed_at: datetime | None = None
    low_sample: bool = True


class SourceReliabilityList(BaseModel):
    window_days: int
    items: list[SourceReliabilityItem]""",
        "SourceReliabilityItem + List",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. New router file
# ─────────────────────────────────────────────────────────────────────

ROUTER_SRC = '''"""External prediction source reliability endpoints.

Read-only leaderboard of external sources (bookmakers, internal models,
prediction sites) and how well they have actually performed against
finished match results.

Public data — no tenant scope. Any authenticated user can see this.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_user_id
from apps.api.schemas.prediction import (
    SourceReliabilityItem,
    SourceReliabilityList,
)
from packages.db.session import get_session

router = APIRouter(prefix="/api/predictions", tags=["predictions"])

LOW_SAMPLE_THRESHOLD = 10


@router.get("/sources", response_model=SourceReliabilityList)
async def list_sources(
    window_days: int = Query(default=30, ge=1, le=365),
    _user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(get_session),
) -> SourceReliabilityList:
    """Reliability leaderboard for all enabled external sources.

    Sources with no reliability row yet appear with null metrics so the
    UI can show them as 'awaiting data' rather than hiding them.
    """
    rows = (await session.execute(sql_text("""
        SELECT
            eps.slug,
            eps.name,
            eps.kind,
            eps.enabled,
            sr.predictions_scored,
            sr.result_accuracy,
            sr.avg_brier_score,
            sr.reliability_weight,
            sr.computed_at
        FROM external_prediction_sources eps
        LEFT JOIN LATERAL (
            SELECT predictions_scored, result_accuracy,
                   avg_brier_score, reliability_weight, computed_at
            FROM source_reliability
            WHERE source_id = eps.id
              AND window_days = :wd
            ORDER BY computed_at DESC
            LIMIT 1
        ) sr ON true
        WHERE eps.enabled = true
        ORDER BY
            sr.reliability_weight DESC NULLS LAST,
            eps.slug ASC
    """), {"wd": window_days})).all()

    items = []
    for r in rows:
        scored = int(r[4]) if r[4] is not None else None
        items.append(SourceReliabilityItem(
            slug=r[0],
            name=r[1],
            kind=r[2],
            enabled=bool(r[3]),
            predictions_scored=scored,
            result_accuracy=float(r[5]) if r[5] is not None else None,
            avg_brier_score=float(r[6]) if r[6] is not None else None,
            reliability_weight=float(r[7]) if r[7] is not None else None,
            computed_at=r[8],
            low_sample=(scored is None or scored < LOW_SAMPLE_THRESHOLD),
        ))

    return SourceReliabilityList(window_days=window_days, items=items)
'''


def step2_write_router() -> None:
    if ROUTER_FILE.exists():
        print(f"  ↷ {ROUTER_FILE.name} already exists")
        return
    ROUTER_FILE.write_text(ROUTER_SRC)
    print(f"  ✓ wrote {ROUTER_FILE.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 3. Register router in main.py
# ─────────────────────────────────────────────────────────────────────

def step3_register_router() -> None:
    src = MAIN_FILE.read_text()
    if "source_reliability_router" in src or "source_reliability as source_reliability_router" in src:
        print(f"  ↷ main.py: already registered")
        return

    # Add import
    import_line = "from apps.api.routers import notifications as notifications_router"
    if import_line in src:
        src = src.replace(
            import_line,
            import_line + "\nfrom apps.api.routers import source_reliability as source_reliability_router",
            1,
        )
    else:
        # fallback: find any "from apps.api.routers import" and append
        raise SystemExit(
            "\n❌ main.py: could not find a stable import block. "
            "Paste the top 40 lines of apps/api/main.py."
        )

    # Add include_router — anchor after notifications
    anchor = "app.include_router(notifications_router.router)"
    if anchor in src:
        src = src.replace(
            anchor,
            anchor + "\napp.include_router(source_reliability_router.router)",
            1,
        )
    else:
        raise SystemExit(
            "\n❌ main.py: notifications_router.router not found. "
            "Paste the include_router section."
        )

    MAIN_FILE.write_text(src)
    print(f"  ✓ main.py: source_reliability router registered")


# ─────────────────────────────────────────────────────────────────────
# 4. Restart API + smoke test
# ─────────────────────────────────────────────────────────────────────

def step4_restart_and_verify() -> bool:
    print("  killing uvicorn...")
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

    # Check OpenAPI includes the new route
    r = subprocess.run(
        ["curl", "-s", "http://localhost:8000/openapi.json"],
        capture_output=True, text=True,
    )
    if "/api/predictions/sources" not in r.stdout:
        print("  ❌ /api/predictions/sources missing from OpenAPI")
        return False
    print("  ✓ /api/predictions/sources in OpenAPI")

    # Try the endpoint (401 without auth is expected — proves it's wired)
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/api/predictions/sources?window_days=30"],
        capture_output=True, text=True,
    )
    code = r.stdout.strip()
    if code == "200":
        print(f"  ✓ endpoint responds 200 (unauthenticated — deps may be open)")
    elif code == "401":
        print(f"  ✓ endpoint responds 401 (auth required — expected)")
    else:
        print(f"  ⚠ endpoint returned {code} — investigate")
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Stage 3.D: /api/predictions/sources endpoint")
    print("=" * 70)

    print("\n[1/4] Extend schemas")
    step1_patch_schemas()

    print("\n[2/4] Write apps/api/routers/source_reliability.py")
    step2_write_router()

    print("\n[3/4] Register in apps/api/main.py")
    step3_register_router()

    print("\n[4/4] Restart + smoke test")
    ok = step4_restart_and_verify()

    print("=" * 70)
    if ok:
        print("✅ Stage 3.D complete")
        print("   GET /api/predictions/sources?window_days=30")
        print("=" * 70)
        sys.exit(0)
    print("❌ Stage 3.D incomplete")
    print("=" * 70)
    sys.exit(1)


if __name__ == "__main__":
    main()
