"""
SignalIQ — Phase T.4.1: Trading block API endpoints.

Exposes:
  GET  /api/trading/subblocks
  GET  /api/trading/subblocks/{slug}
  GET  /api/trading/subblocks/{slug}/posts
  GET  /api/trading/posts/{post_id}
  POST /api/trading/subblocks/{slug}/generate

Read-only except the generate trigger, which is rate-limited and
dispatches to the existing Celery tasks.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "apps" / "api" / "schemas" / "trading.py"
ROUTER_FILE = ROOT / "apps" / "api" / "routers" / "trading.py"
MAIN_FILE = ROOT / "apps" / "api" / "main.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(f"\n❌ {path.name}: pattern not found for '{label}'")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. Schemas
# ─────────────────────────────────────────────────────────────────────

SCHEMAS_SRC = '''"""Trading block API schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TradingSubblockResponse(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None
    category: str | None
    cadence: str
    source_kind: str | None
    enabled: bool
    sort_order: int
    theme: dict | None = None
    # Aggregates
    post_count: int = 0
    latest_post_at: datetime | None = None

    model_config = {"from_attributes": True}


class TradingSubblockListResponse(BaseModel):
    items: list[TradingSubblockResponse]
    total: int


class TradingPostResponse(BaseModel):
    id: str
    subblock_id: str
    subblock_slug: str | None = None
    title: str
    body: str
    format: str
    source_kind: str
    source_urls: list[str] = []
    symbols: list[str] = []
    status: str
    word_count: int | None
    published_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TradingPostListResponse(BaseModel):
    items: list[TradingPostResponse]
    total: int
    limit: int
    offset: int


class TradingGenerateResponse(BaseModel):
    task_id: str
    status: str
    slug: str
'''


def step1_write_schemas() -> None:
    print("\n[1/4] Write API schemas")
    if SCHEMA_FILE.exists():
        print(f"  ↷ {SCHEMA_FILE.name} exists — overwriting")
    SCHEMA_FILE.write_text(SCHEMAS_SRC)
    print(f"  ✓ wrote {SCHEMA_FILE.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 2. Router
# ─────────────────────────────────────────────────────────────────────

ROUTER_SRC = '''"""Trading block endpoints.

Lists subblocks, their posts, and triggers on-demand generation.

Subblocks are org-overridable: global seeds have org_id IS NULL,
org-specific rows override per-org. The RLS policy on trading_subblocks
allows org_id IS NULL OR org_id = current_org. Posts are strictly
org-scoped via RLS on trading_posts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, current_user_id, require_role
from apps.api.schemas.trading import (
    TradingGenerateResponse,
    TradingPostListResponse,
    TradingPostResponse,
    TradingSubblockListResponse,
    TradingSubblockResponse,
)
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/trading", tags=["trading"])


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


# ─────────────────────────────────────────────────────────────────────
# Subblocks
# ─────────────────────────────────────────────────────────────────────

@router.get("/subblocks", response_model=TradingSubblockListResponse)
async def list_subblocks(
    session: AsyncSession = Depends(_scoped_session),
) -> TradingSubblockListResponse:
    """All 16 trading subblocks with post counts + latest post time.

    RLS on trading_subblocks allows global seeds (org_id IS NULL) plus
    any org-specific overrides. We filter to enabled=true for the UI.
    """
    rows = (await session.execute(sql_text("""
        SELECT
            ts.id::text, ts.slug, ts.name, ts.description, ts.category,
            ts.cadence, ts.source_kind, ts.enabled, ts.sort_order, ts.theme,
            COALESCE(pc.n, 0) AS post_count,
            pl.latest AS latest_post_at
        FROM trading_subblocks ts
        LEFT JOIN (
            SELECT subblock_id, COUNT(*) AS n
            FROM trading_posts
            WHERE status = 'published'
            GROUP BY subblock_id
        ) pc ON pc.subblock_id = ts.id
        LEFT JOIN (
            SELECT subblock_id, MAX(published_at) AS latest
            FROM trading_posts
            WHERE status = 'published'
            GROUP BY subblock_id
        ) pl ON pl.subblock_id = ts.id
        WHERE ts.enabled = true
        ORDER BY ts.sort_order, ts.name
    """))).all()

    items = [
        TradingSubblockResponse(
            id=r[0], slug=r[1], name=r[2], description=r[3], category=r[4],
            cadence=r[5], source_kind=r[6], enabled=bool(r[7]),
            sort_order=r[8], theme=r[9],
            post_count=int(r[10] or 0),
            latest_post_at=r[11],
        )
        for r in rows
    ]
    return TradingSubblockListResponse(items=items, total=len(items))


@router.get("/subblocks/{slug}", response_model=TradingSubblockResponse)
async def get_subblock(
    slug: str,
    session: AsyncSession = Depends(_scoped_session),
) -> TradingSubblockResponse:
    row = (await session.execute(sql_text("""
        SELECT ts.id::text, ts.slug, ts.name, ts.description, ts.category,
               ts.cadence, ts.source_kind, ts.enabled, ts.sort_order, ts.theme,
               COALESCE(pc.n, 0) AS post_count,
               pl.latest AS latest_post_at
        FROM trading_subblocks ts
        LEFT JOIN (
            SELECT subblock_id, COUNT(*) AS n FROM trading_posts
            WHERE status = 'published' GROUP BY subblock_id
        ) pc ON pc.subblock_id = ts.id
        LEFT JOIN (
            SELECT subblock_id, MAX(published_at) AS latest FROM trading_posts
            WHERE status = 'published' GROUP BY subblock_id
        ) pl ON pl.subblock_id = ts.id
        WHERE ts.slug = :slug
        ORDER BY ts.org_id NULLS LAST
        LIMIT 1
    """), {"slug": slug})).first()

    if not row:
        raise HTTPException(status_code=404, detail="Subblock not found")

    return TradingSubblockResponse(
        id=row[0], slug=row[1], name=row[2], description=row[3], category=row[4],
        cadence=row[5], source_kind=row[6], enabled=bool(row[7]),
        sort_order=row[8], theme=row[9],
        post_count=int(row[10] or 0),
        latest_post_at=row[11],
    )


# ─────────────────────────────────────────────────────────────────────
# Posts
# ─────────────────────────────────────────────────────────────────────

@router.get("/subblocks/{slug}/posts", response_model=TradingPostListResponse)
async def list_posts(
    slug: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default="published"),
    session: AsyncSession = Depends(_scoped_session),
) -> TradingPostListResponse:
    # Resolve subblock id
    subblock_id = (await session.execute(sql_text("""
        SELECT id::text FROM trading_subblocks
        WHERE slug = :slug ORDER BY org_id NULLS LAST LIMIT 1
    """), {"slug": slug})).scalar()
    if not subblock_id:
        raise HTTPException(status_code=404, detail="Subblock not found")

    where = "WHERE tp.subblock_id = :sid"
    params: dict = {"sid": subblock_id, "lim": limit, "off": offset}
    if status:
        where += " AND tp.status = :status"
        params["status"] = status

    rows = (await session.execute(sql_text(f"""
        SELECT tp.id::text, tp.subblock_id::text, ts.slug,
               tp.title, tp.body, tp.format, tp.source_kind,
               tp.source_urls, tp.symbols, tp.status, tp.word_count,
               tp.published_at, tp.created_at
        FROM trading_posts tp
        LEFT JOIN trading_subblocks ts ON ts.id = tp.subblock_id
        {where}
        ORDER BY COALESCE(tp.published_at, tp.created_at) DESC
        LIMIT :lim OFFSET :off
    """), params)).all()

    total = (await session.execute(sql_text(f"""
        SELECT count(*) FROM trading_posts tp {where}
    """), {k: v for k, v in params.items() if k in ("sid", "status")})).scalar() or 0

    items = [
        TradingPostResponse(
            id=r[0], subblock_id=r[1], subblock_slug=r[2],
            title=r[3], body=r[4], format=r[5], source_kind=r[6],
            source_urls=list(r[7] or []), symbols=list(r[8] or []),
            status=r[9], word_count=r[10],
            published_at=r[11], created_at=r[12],
        )
        for r in rows
    ]
    return TradingPostListResponse(
        items=items, total=int(total), limit=limit, offset=offset,
    )


@router.get("/posts/{post_id}", response_model=TradingPostResponse)
async def get_post(
    post_id: str,
    session: AsyncSession = Depends(_scoped_session),
) -> TradingPostResponse:
    row = (await session.execute(sql_text("""
        SELECT tp.id::text, tp.subblock_id::text, ts.slug,
               tp.title, tp.body, tp.format, tp.source_kind,
               tp.source_urls, tp.symbols, tp.status, tp.word_count,
               tp.published_at, tp.created_at
        FROM trading_posts tp
        LEFT JOIN trading_subblocks ts ON ts.id = tp.subblock_id
        WHERE tp.id = :pid
    """), {"pid": post_id})).first()

    if not row:
        raise HTTPException(status_code=404, detail="Post not found")

    return TradingPostResponse(
        id=row[0], subblock_id=row[1], subblock_slug=row[2],
        title=row[3], body=row[4], format=row[5], source_kind=row[6],
        source_urls=list(row[7] or []), symbols=list(row[8] or []),
        status=row[9], word_count=row[10],
        published_at=row[11], created_at=row[12],
    )


# ─────────────────────────────────────────────────────────────────────
# On-demand generation
# ─────────────────────────────────────────────────────────────────────

_TASK_BY_SLUG = {
    "stock_lists": "tasks.trading_generate_stock_lists",
    "market_signals": "tasks.trading_generate_market_signals",
    "screener_picks": "tasks.trading_generate_screener_picks",
}


@router.post("/subblocks/{slug}/generate", response_model=TradingGenerateResponse,
             status_code=202)
async def trigger_generation(
    slug: str,
    org_id: str = Depends(current_org_id),
    _user: str = Depends(current_user_id),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
) -> TradingGenerateResponse:
    """Queue an on-demand generation for one subblock.

    Rate-limited to 5 requests per hour per org.
    Only the 3 scraper subblocks are implemented so far; other slugs
    return 501 until their generators ship.
    """
    task_name = _TASK_BY_SLUG.get(slug)
    if not task_name:
        raise HTTPException(
            status_code=501,
            detail=f"Subblock '{slug}' has no generator yet",
        )

    # Rate limit
    from packages.core.rate_limit import check_rate_limit
    from packages.core.error import QuotaExceededError
    rl = check_rate_limit(
        namespace="org", identifier=org_id, route=f"trading-generate-{slug}",
        limit=5, window_seconds=3600,
    )
    if not rl.allowed:
        raise QuotaExceededError(
            f"Rate limit reached ({rl.limit}/hour). "
            f"Retry in {rl.retry_after_seconds}s.",
        )

    # Dispatch
    from apps.worker.celery_app import celery_app
    task = celery_app.send_task(task_name, args=[org_id])
    return TradingGenerateResponse(
        task_id=task.id, status="queued", slug=slug,
    )
'''


def step2_write_router() -> None:
    print("\n[2/4] Write router")
    ROUTER_FILE.write_text(ROUTER_SRC)
    print(f"  ✓ wrote {ROUTER_FILE.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 3. Register in main.py
# ─────────────────────────────────────────────────────────────────────

def step3_register_router() -> None:
    print("\n[3/4] Register router in main.py")
    src = MAIN_FILE.read_text()
    if "trading_router" in src:
        print("  ↷ trading_router already registered")
        return

    # Find existing router registration for anchoring
    if "from apps.api.routers import notifications as notifications_router" in src:
        _patch_and_verify(
            MAIN_FILE,
            "from apps.api.routers import notifications as notifications_router",
            "from apps.api.routers import notifications as notifications_router\n"
            "from apps.api.routers import trading as trading_router",
            "add import",
        )
        _patch_and_verify(
            MAIN_FILE,
            "app.include_router(notifications_router.router)",
            "app.include_router(notifications_router.router)\n"
            "app.include_router(trading_router.router)",
            "add include_router",
        )
    else:
        raise SystemExit("❌ main.py structure differs — paste first 60 lines")


# ─────────────────────────────────────────────────────────────────────
# 4. Restart API + smoke test
# ─────────────────────────────────────────────────────────────────────

def step4_restart_and_verify() -> bool:
    print("\n[4/4] Restart API + smoke test")

    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)

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

    # Verify trading routes in OpenAPI
    r = subprocess.run(
        ["curl", "-s", "http://localhost:8000/openapi.json"],
        capture_output=True, text=True,
    )
    expected_paths = [
        "/api/trading/subblocks",
        "/api/trading/subblocks/{slug}",
        "/api/trading/subblocks/{slug}/posts",
        "/api/trading/posts/{post_id}",
        "/api/trading/subblocks/{slug}/generate",
    ]
    missing = [p for p in expected_paths if p not in r.stdout]
    if missing:
        print(f"  ❌ missing endpoints in OpenAPI: {missing}")
        return False
    print(f"  ✓ all {len(expected_paths)} trading endpoints registered")

    # Try one endpoint (401 without auth expected)
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/api/trading/subblocks"],
        capture_output=True, text=True,
    )
    code = r.stdout.strip()
    print(f"  ✓ GET /api/trading/subblocks → {code} (401 = auth required)")

    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.4.1: Trading API endpoints")
    print("=" * 70)

    step1_write_schemas()
    step2_write_router()
    step3_register_router()
    ok = step4_restart_and_verify()

    print("=" * 70)
    if ok:
        print("✅ T.4.1 complete — API endpoints live")
        print()
        print("Next: T.4.2 — UI (/trading hub, /trading/[slug], /trading/[slug]/[postId])")
        sys.exit(0)
    print("❌ T.4.1 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
