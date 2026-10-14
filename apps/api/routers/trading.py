"""Trading block endpoints.

Lists subblocks, their posts, and triggers on-demand generation.

Subblocks are org-overridable: global seeds have org_id IS NULL,
org-specific rows override per-org. The RLS policy on trading_subblocks
allows org_id IS NULL OR org_id = current_org. Posts are strictly
org-scoped via RLS on trading_posts.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, current_user_id, require_role
from apps.api.schemas.trading import (
    DraftActionResult,
    DraftListResponse,
    DraftPostResponse,
    SymbolIndexItem,
    SymbolIndexResponse,
    SymbolPostMention,
    SymbolTimelineResponse,
    TradingGenerateResponse,
    TradingHealthResponse,
    TradingPostListResponse,
    TradingPostResponse,
    TradingSubblockHealth,
    TradingSubblockListResponse,
    TradingSubblockResponse,
    ScheduleRequest,
    ScheduleResponse,
    SubblockInfo,
    SubblockSchedule,
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
            WHERE status = 'published' AND deleted_at IS NULL
            GROUP BY subblock_id
        ) pc ON pc.subblock_id = ts.id
        LEFT JOIN (
            SELECT subblock_id, MAX(published_at) AS latest
            FROM trading_posts
            WHERE status = 'published' AND deleted_at IS NULL
            GROUP BY subblock_id
        ) pl ON pl.subblock_id = ts.id
        WHERE ts.enabled = true
          AND NOT COALESCE(
                (ts.theme->>'hidden_in_grid')::boolean,
                false
              )
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
            WHERE status = 'published' AND deleted_at IS NULL GROUP BY subblock_id
        ) pc ON pc.subblock_id = ts.id
        LEFT JOIN (
            SELECT subblock_id, MAX(published_at) AS latest FROM trading_posts
            WHERE status = 'published' AND deleted_at IS NULL GROUP BY subblock_id
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

    where = "WHERE tp.subblock_id = :sid AND tp.deleted_at IS NULL"
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
        WHERE tp.id = :pid AND tp.deleted_at IS NULL
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
    "trading_psychology": "tasks.trading_generate_trading_psychology",
    "investment_strategies": "tasks.trading_generate_investment_strategies",
    "trading_styles": "tasks.trading_generate_trading_styles",
    "class_room": "tasks.trading_generate_class_room",
    "trading_tools": "tasks.trading_generate_trading_tools",
    "asset_classes": "tasks.trading_generate_asset_classes",
    "technical_indicators": "tasks.trading_generate_technical_indicators",
    "economic_events": "tasks.trading_generate_economic_events",
    "risk_management": "tasks.trading_generate_risk_management",
    "fundamental_metrics": "tasks.trading_generate_fundamental_metrics",
    "market_cap": "tasks.trading_generate_market_cap",
    "market_condition": "tasks.trading_generate_market_condition",
    "market_analysis": "tasks.trading_generate_market_analysis",
}


@router.post("/performance/chart")
async def generate_performance_chart(
    org_id: str = Depends(current_org_id),
    _user: str = Depends(current_user_id),
    session: AsyncSession = Depends(get_session),
):
    """Render the equity curve as a PNG and return its URL.

    Overwrites the previous chart for this org. Next.js serves
    the file from /charts/<org_id>.png.
    """
    from packages.trading.render_chart import (
        render_equity_chart, chart_url,
    )
    await set_current_org(session, org_id)
    try:
        await render_equity_chart(session, org_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"url": chart_url(org_id, cache_bust=True)}


@router.get("/runs")
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    slug: str | None = Query(None),
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
):
    """Recent generation attempts for this org.

    Pass ?slug=risk_management to filter to one subblock.
    """
    await set_current_org(session, org_id)
    if slug:
        rows = (await session.execute(sql_text("""
            SELECT id::text, subblock_slug, status, error,
                   post_id::text, started_at, finished_at
            FROM generation_runs
            WHERE org_id = :oid AND subblock_slug = :slug
            ORDER BY started_at DESC
            LIMIT :lim
        """), {"oid": org_id, "slug": slug, "lim": limit})).all()
    else:
        rows = (await session.execute(sql_text("""
            SELECT id::text, subblock_slug, status, error,
                   post_id::text, started_at, finished_at
            FROM generation_runs
            WHERE org_id = :oid
            ORDER BY started_at DESC
            LIMIT :lim
        """), {"oid": org_id, "lim": limit})).all()

    return {
        "items": [
            {
                "id": r[0],
                "subblock_slug": r[1],
                "status": r[2],
                "error": r[3],
                "post_id": r[4],
                "started_at": r[5].isoformat() if r[5] else None,
                "finished_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ],
    }


@router.post("/subblocks/{slug}/generate", response_model=TradingGenerateResponse,
             status_code=200)
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

    # S.7.2 — block gate. Missing row means disabled.
    from packages.db.session import get_owner_session_factory as _osf
    from packages.core.blocks import is_block_enabled
    _block_factory = _osf()
    async with _block_factory() as _bs:
        if not await is_block_enabled(_bs, org_id, "trading"):
            raise HTTPException(
                status_code=403,
                detail="Trading block is not enabled for your organisation. "
                       "Add it in settings to generate content.",
            )

    # Ensure a batch exists before queuing the article. The beat
    # produces batches every 2 minutes, but a manual click should
    # work immediately. force=False respects the population gate.
    if slug == "risk_management":
        try:
            from packages.trading.synthetic_trades import (
                generate_synthetic_trades,
            )
            from packages.db.session import get_owner_session_factory as _gen_osf
            _gen_factory = _gen_osf()
            async with _gen_factory() as _gen_sess:
                await _gen_sess.execute(
                    sql_text(
                        "SELECT set_config('app.current_org_id', :oid, false)"
                    ),
                    {"oid": org_id},
                )
                await generate_synthetic_trades(
                    _gen_sess, org_id=org_id, force=False,
                )
        except Exception as exc:
            from packages.core.logging import get_logger
            get_logger(__name__).warning(
                "trading.manual_batch_failed", error=str(exc)[:120],
            )

    # Exhaustion gate — return 402 if the user is out of tokens.
    # No session dependency on this endpoint, so open an owner session
    # inline (same pattern as /api/auth/tokens/balance).
    from packages.core.context import current_user_id as _uid_ctx
    uid = _uid_ctx.get()
    if uid:
        from packages.core.tokens import can_generate
        from packages.db.session import get_owner_session_factory
        factory = get_owner_session_factory()
        async with factory() as check_session:
            if not await can_generate(check_session, uid):
                raise HTTPException(
                    status_code=402,
                    detail="Insufficient tokens — top up to continue",
                )

    # Rate limit — per (org, subblock) per hour. The cap depends on
    # the org's plan: free tier gets the conservative default, paid
    # plans get a much higher one. Configurable via settings so ops
    # can tune without a code change.
    #
    # This endpoint does not take a session dependency (see the note
    # above the exhaustion gate), so open an owner session inline to
    # read the org's plan.
    from packages.core.config import get_settings as _gs
    from packages.core.rate_limit import check_rate_limit
    from packages.core.error import QuotaExceededError
    from packages.db.session import get_owner_session_factory as _get_owner

    _s = _gs()
    _owner = _get_owner()
    async with _owner() as _plan_session:
        _plan_row = (await _plan_session.execute(sql_text(
            "SELECT plan FROM orgs WHERE id = :oid"
        ), {"oid": org_id})).first()
    _plan = (_plan_row[0] if _plan_row else "free")
    _limit = (
        _s.trading_generate_limit_per_hour_paid
        if _plan in ("pro", "business", "enterprise")
        else _s.trading_generate_limit_per_hour
    )

    rl = check_rate_limit(
        namespace="org", identifier=org_id, route=f"trading-generate-{slug}",
        limit=_limit, window_seconds=30,
    )
    if not rl.allowed:
        raise QuotaExceededError(
            f"Rate limit reached ({rl.limit}/hour). "
            f"Retry in {rl.retry_after_seconds}s.",
        )

    # S.9.18 — synchronous generation.
    #
    # The old path queued a Celery task and returned 202. The
    # 10-key gate lives in the LLM router, which runs inside the
    # worker. So the user saw "queued" for a request that would
    # fail seconds later. Now the subblock runs inline and the
    # gate surfaces as the HTTP response.
    from packages.trading.subblocks.base import generate_subblock
    from packages.db.session import get_session_factory
    from packages.db.tenant import set_current_org
    from packages.core.error import (
        NoCredentialsError,
        CredentialsExhaustedError,
        BlockDisabledError,
    )

    factory = get_session_factory()
    async with factory() as session:
        await set_current_org(session, org_id)
        try:
            result = await generate_subblock(
                session, org_id=org_id, slug=slug,
            )
        except NoCredentialsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except BlockDisabledError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except CredentialsExhaustedError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    status = result.get("status", "unknown") if isinstance(result, dict) else "unknown"
    post_id = result.get("post_id") if isinstance(result, dict) else None
    word_count = int(result.get("word_count", 0) or 0) if isinstance(result, dict) else 0

    # Human-readable reason when the subblock skipped because a
    # precondition is missing (no trade_log, no market_conditions,
    # etc.). The frontend can show this instead of a silent 200.
    SKIP_EXPLANATIONS = {
        "no_event": "No market event available yet. Run a market fetch first.",
        "no_market_conditions": "Market conditions have not been populated yet.",
        "no_movers": "No movers detected today.",
        "no_process": "Your trader process document is empty or incomplete.",
        "no_section": "A required section of your trader process is missing.",
        "no_trades": "No trades logged yet. Add trades to unlock this subblock.",
        "no_data": "The data this subblock needs is not available.",
        "exhausted": "Every coordinate for this subblock has been written. "
                     "Edit your process document to open a new cycle.",
        "all_covered": "All candidates have already been written about.",
        "disabled": "This subblock is not enabled for your org.",
        "skipped": "The subblock decided not to run this time.",
    }
    message = SKIP_EXPLANATIONS.get(status)

    return TradingGenerateResponse(
        status=status,
        slug=slug,
        post_id=post_id,
        word_count=word_count,
        message=message,
    )


# ─────────────────────────────────────────────────────────────────────
# Observability — health of the automated generation
# ─────────────────────────────────────────────────────────────────────

# Expected cadence in minutes, per cadence string
_CADENCE_MINUTES = {
    "hourly_market": 60,
    "daily": 24 * 60,
    "event_driven": 24 * 60,   # treat event-driven as daily minimum
    "manual": 24 * 60 * 7,     # weekly, low bar
}


@router.get("/health", response_model=TradingHealthResponse)
async def trading_health(
    session: AsyncSession = Depends(_scoped_session),
) -> TradingHealthResponse:
    """Health snapshot of the automated trading generation pipeline.

    Per subblock: how many posts in the last 24h, when the last one
    landed, and a health verdict comparing observed vs expected cadence.
    """
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    since = now - timedelta(hours=24)

    rows = (await session.execute(sql_text("""
        SELECT
            ts.slug, ts.name, ts.cadence, ts.source_kind,
            COALESCE(tp_pub.n, 0) AS posts_24h,
            COALESCE(tp_dft.n, 0) AS drafts_24h,
            tp_pub.last_pub AS last_published_at,
            GREATEST(tp_pub.last_pub, tp_dft.last_any) AS last_created_at,
            tp_pub.avg_words AS avg_word_count_24h
        FROM trading_subblocks ts
        LEFT JOIN (
            SELECT subblock_id,
                   COUNT(*) AS n,
                   MAX(published_at) AS last_pub,
                   AVG(word_count)::float AS avg_words
            FROM trading_posts
            WHERE status = 'published' AND deleted_at IS NULL AND created_at >= :since
            GROUP BY subblock_id
        ) tp_pub ON tp_pub.subblock_id = ts.id
        LEFT JOIN (
            SELECT subblock_id, COUNT(*) AS n, MAX(created_at) AS last_any
            FROM trading_posts
            WHERE status = 'draft' AND deleted_at IS NULL AND created_at >= :since
            GROUP BY subblock_id
        ) tp_dft ON tp_dft.subblock_id = ts.id
        WHERE ts.enabled = true
        ORDER BY ts.sort_order
    """), {"since": since})).all()

    items: list[TradingSubblockHealth] = []
    healthy = delayed = stale = never = 0

    for r in rows:
        slug, name, cadence, source_kind = r[0], r[1], r[2], r[3]
        posts_24h = int(r[4] or 0)
        drafts_24h = int(r[5] or 0)
        last_pub = r[6]
        last_created = r[7]
        avg_words = float(r[8]) if r[8] is not None else None

        expected = _CADENCE_MINUTES.get(cadence, 24 * 60)

        # Market-hours awareness: hourly_market subblocks only run
        # 13:00-20:00 UTC Mon-Fri. Outside that window, "delayed" or
        # "stale" is expected — do not flag as a problem.
        outside_hours = False
        if cadence == "hourly_market":
            weekday = now.weekday()  # 0=Mon, 6=Sun
            if weekday >= 5 or now.hour < 13 or now.hour >= 20:
                outside_hours = True

        if last_created is None:
            mins_since = None
            status = "never_run"
            never += 1
        else:
            delta = now - last_created
            mins_since = int(delta.total_seconds() / 60)
            if outside_hours:
                status = "outside_hours"
                healthy += 1  # not a problem
            elif mins_since <= expected * 1.5:
                status = "healthy"
                healthy += 1
            elif mins_since <= expected * 3:
                status = "delayed"
                delayed += 1
            else:
                status = "stale"
                stale += 1

        items.append(TradingSubblockHealth(
            slug=slug, name=name, cadence=cadence,
            source_kind=source_kind,
            posts_24h=posts_24h, drafts_24h=drafts_24h,
            last_published_at=last_pub, last_created_at=last_created,
            avg_word_count_24h=avg_words,
            minutes_since_last=mins_since,
            expected_cadence_minutes=expected,
            status=status,
        ))

    return TradingHealthResponse(
        items=items,
        summary={
            "total": len(items),
            "healthy": healthy,
            "delayed": delayed,
            "stale": stale,
            "never_run": never,
        },
        generated_at=now,
    )


# ─────────────────────────────────────────────────────────────────────
# Cross-subblock: symbols appear across many subblocks
# ─────────────────────────────────────────────────────────────────────

@router.get("/performance")
async def trading_performance(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
):
    """The demo account's equity curve, monthly breakdown, and
    full trade log. Used by the /trading/performance page."""
    await set_current_org(session, org_id)

    acc = (await session.execute(sql_text("""
        SELECT account_name FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL LIMIT 1
    """), {"oid": org_id})).first()
    account_name = (acc[0] if acc and acc[0] else "Demo Account")

    rows = (await session.execute(sql_text("""
        SELECT traded_at, direction, contracts, entry, stop, exit, result
        FROM trade_log
        WHERE org_id = :oid
          AND source = 'synthetic'
          AND deleted_at IS NULL
        ORDER BY traded_at ASC, id ASC
    """), {"oid": org_id})).all()

    starting = 200_000.0
    equity = starting
    trades: list[dict] = []
    for r in rows:
        equity += float(r[6] or 0)
        trades.append({
            "traded_at": r[0].isoformat(),
            "direction": r[1],
            "contracts": int(r[2] or 0),
            "entry": float(r[3]) if r[3] is not None else None,
            "stop": float(r[4]) if r[4] is not None else None,
            "exit": float(r[5]) if r[5] is not None else None,
            "result": float(r[6] or 0),
            "equity": equity,
        })

    winners = sum(1 for t in trades if t["result"] > 0)
    losers = sum(1 for t in trades if t["result"] < 0)
    scratches = sum(1 for t in trades if t["result"] == 0)
    net = sum(t["result"] for t in trades)
    ending = starting + net

    peak = starting
    trough = starting
    max_dd = 0.0
    max_dd_pct = 0.0
    for t in trades:
        eq = t["equity"]
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak * 100 if peak else 0
        if eq < trough:
            trough = eq

    monthly_map: dict[tuple[int, int], dict] = {}
    for t in trades:
        d = t["traded_at"][:10]
        yr, mo = int(d[:4]), int(d[5:7])
        key = (yr, mo)
        b = monthly_map.setdefault(key, {
            "trades": 0, "winners": 0, "losers": 0, "net": 0.0,
            "ending_equity": starting,
        })
        b["trades"] += 1
        if t["result"] > 0:
            b["winners"] += 1
        elif t["result"] < 0:
            b["losers"] += 1
        b["net"] += t["result"]
        b["ending_equity"] = t["equity"]

    from datetime import date as _date
    monthly = []
    for (yr, mo) in sorted(monthly_map):
        month_name = _date(yr, mo, 1).strftime("%B %Y")
        b = monthly_map[(yr, mo)]
        monthly.append({"month": month_name, **b})

    return {
        "summary": {
            "trades": len(trades),
            "winners": winners,
            "losers": losers,
            "scratches": scratches,
            "net": net,
            "ending": ending,
            "peak": peak,
            "trough": trough,
            "max_dd": max_dd,
            "max_dd_pct": max_dd_pct,
            "account_name": account_name,
            "starting_balance": starting,
        },
        "trades": trades,
        "monthly": monthly,
    }


@router.get("/account-name")
async def get_account_name(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
):
    """Return the current account name (empty string if unset)."""
    await set_current_org(session, org_id)
    row = (await session.execute(sql_text("""
        SELECT account_name FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL LIMIT 1
    """), {"oid": org_id})).first()
    return {"account_name": row[0] if row and row[0] else ""}


class AccountNameRequest(BaseModel):
    account_name: str


@router.post("/account-name")
async def set_account_name(
    body: AccountNameRequest,
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
):
    """Set the account name shown on the performance page and in
    every article's performance sentence."""
    await set_current_org(session, org_id)
    name = body.account_name.strip()[:120]
    if not name:
        raise HTTPException(
            status_code=400,
            detail="account_name cannot be empty",
        )
    await session.execute(sql_text("""
        UPDATE trader_process
        SET account_name = :n
        WHERE org_id = :oid AND deleted_at IS NULL
    """), {"n": name, "oid": org_id})
    await session.commit()
    return {"ok": True, "account_name": name}




@router.get("/schedule", response_model=ScheduleResponse)
async def get_schedule(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> ScheduleResponse:
    """Return the org's per-subblock schedule settings.

    Each auto-generating subblock has its own enabled flag and
    interval. The next_fire map shows when the scheduler will next
    try each one.
    """
    from datetime import UTC, datetime, timedelta

    await set_current_org(session, org_id)

    # Load the schedule dict.
    row = (await session.execute(sql_text("""
        SELECT settings->'schedule'->'subblocks' AS subs
        FROM orgs WHERE id = :oid
    """), {"oid": org_id})).first()
    raw = row[0] if row and row[0] else {}

    # Normalize to SubblockSchedule.
    parsed: dict[str, SubblockSchedule] = {}
    if isinstance(raw, dict):
        for slug, cfg in raw.items():
            if isinstance(cfg, dict):
                parsed[slug] = SubblockSchedule(
                    enabled=bool(cfg.get("enabled", False)),
                    interval_hours=max(2, min(int(cfg.get("interval_hours", 6)), 168)),
                )
            elif isinstance(cfg, bool):
                # Legacy: subblocks used to be a list of strings.
                parsed[slug] = SubblockSchedule(enabled=cfg, interval_hours=6)

    # Compute next_fire per subblock from the last post.
    next_fire: dict[str, str | None] = {}
    for slug, cfg in parsed.items():
        if not cfg.enabled:
            next_fire[slug] = None
            continue
        last_row = (await session.execute(sql_text("""
            SELECT MAX(tp.created_at) AS last
            FROM trading_posts tp
            JOIN trading_subblocks ts ON ts.id = tp.subblock_id
            WHERE tp.org_id = :oid
              AND ts.slug = :slug
              AND tp.deleted_at IS NULL
        """), {"oid": org_id, "slug": slug})).first()
        last = last_row[0] if last_row and last_row[0] else None
        if last is None:
            next_fire[slug] = datetime.now(UTC).isoformat()
        else:
            next_fire[slug] = (last + timedelta(hours=cfg.interval_hours)).isoformat()

    return ScheduleResponse(subblocks=parsed, next_fire=next_fire)


@router.post("/schedule")
async def set_schedule(
    body: ScheduleRequest,
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Replace the org's per-subblock schedule.

    Every subblock in the payload is written. Subblocks not in the
    payload keep their previous config (or default to disabled).
    Interval is clamped to [2, 168] hours.
    """
    await set_current_org(session, org_id)

    # Validate and normalize.
    normalized: dict[str, dict] = {}
    for slug, cfg in body.subblocks.items():
        s = slug.strip()
        if not s:
            continue
        normalized[s] = {
            "enabled": bool(cfg.enabled),
            "interval_hours": max(2, min(int(cfg.interval_hours), 168)),
        }

    await session.execute(sql_text("""
        UPDATE orgs
        SET settings = jsonb_set(
            settings,
            '{schedule,subblocks}',
            CAST(:subs AS jsonb)
        )
        WHERE id = :oid
    """), {"oid": org_id, "subs": json.dumps(normalized)})
    await session.commit()

    return {"ok": True, "subblocks": normalized}


@router.get("/subblocks-available", response_model=list[SubblockInfo])
async def list_available_subblocks(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> list[SubblockInfo]:
    """List every auto-generating subblock the org can configure.

    Only llm-source subblocks are listed — scraper and studio
    subblocks run on their own fixed cadence and are not user-scheduled.
    """
    await set_current_org(session, org_id)
    rows = (await session.execute(sql_text("""
        SELECT slug, name, description, cadence, source_kind
        FROM trading_subblocks
        WHERE enabled = true
          AND (org_id IS NULL OR org_id = :oid)
          AND source_kind = 'llm'
          AND cadence IN ('daily', 'event_driven', 'hourly_market')
        ORDER BY sort_order, name
    """), {"oid": org_id})).mappings().all()

    return [
        SubblockInfo(
            slug=r["slug"],
            name=r["name"],
            description=r["description"],
            cadence=r["cadence"],
            source_kind=r["source_kind"],
            ready=True,
        )
        for r in rows
    ]



# ─────────────────────────────────────────────────────────────────────
# Ticker index — top tickers by mention count across all subblocks
# ─────────────────────────────────────────────────────────────────────

@router.get("/symbols", response_model=SymbolIndexResponse)
async def symbols_index(
    limit: int = Query(default=60, ge=1, le=200),
    session: AsyncSession = Depends(_scoped_session),
) -> SymbolIndexResponse:
    """Ranked list of every ticker mentioned, by number of posts.

    Powered by the GIN index on trading_posts.symbols. Used by the
    /trading/symbols index page and the hub's 'Browse by symbol' card.
    """
    rows = (await session.execute(sql_text("""
        SELECT sym, COUNT(*)::int AS mentions
        FROM trading_posts tp,
             LATERAL unnest(tp.symbols) AS sym
        WHERE tp.status = 'published' AND tp.deleted_at IS NULL
        GROUP BY sym
        ORDER BY mentions DESC, sym ASC
        LIMIT :lim
    """), {"lim": limit})).all()

    items = [
        {"ticker": r[0], "mentions": int(r[1])}
        for r in rows
    ]
    return SymbolIndexResponse(items=items, total=len(items))


# ─────────────────────────────────────────────────────────────────────
# Draft review workflow (T.11)
# ─────────────────────────────────────────────────────────────────────

@router.get("/drafts", response_model=DraftListResponse)
async def list_drafts(
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(_scoped_session),
) -> DraftListResponse:
    """All draft posts across all subblocks, newest first.

    Each draft carries its rejection reason (unsupported claims from
    the verify layer, if the generator recorded them).
    """
    rows = (await session.execute(sql_text("""
        SELECT tp.id::text, ts.slug, ts.name,
               tp.title, tp.body, tp.source_kind, tp.word_count,
               tp.created_at,
               tp.source_payload->'_rejection'->'unsupported_claims' AS claims,
               COALESCE((tp.source_payload->'_rejection'->>'regenerated')::boolean, false) AS regen
        FROM trading_posts tp
        JOIN trading_subblocks ts ON ts.id = tp.subblock_id
        WHERE tp.status = 'draft' AND tp.deleted_at IS NULL
        ORDER BY tp.created_at DESC
        LIMIT :lim
    """), {"lim": limit})).all()

    items = []
    for r in rows:
        claims_raw = r[8]
        claims: list[str] = []
        if isinstance(claims_raw, list):
            claims = [str(c) for c in claims_raw]
        items.append(DraftPostResponse(
            id=r[0], subblock_slug=r[1], subblock_name=r[2],
            title=r[3], body=r[4], source_kind=r[5],
            word_count=r[6], created_at=r[7],
            unsupported_claims=claims, regenerated=bool(r[9]),
        ))

    return DraftListResponse(items=items, total=len(items))


@router.post("/drafts/{post_id}/approve", response_model=DraftActionResult)
async def approve_draft(
    post_id: str,
    org_id: str = Depends(current_org_id),
    user_id: str = Depends(current_user_id),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
    session: AsyncSession = Depends(_scoped_session),
) -> DraftActionResult:
    """Publish a draft post and record human approval."""
    row = (await session.execute(sql_text("""
        SELECT id FROM trading_posts WHERE id = :pid AND status = 'draft'
    """), {"pid": post_id})).first()

    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")

    # S.8.9.5 — clear the rejection metadata on approve. The
    # `_rejection` field is written by persist_post when a draft is
    # saved. A human reviewer has just decided the flagged claims
    # were acceptable, so the metadata should not linger — otherwise
    # the UI keeps showing the post as "flagged" after approval.
    await session.execute(sql_text("""
        UPDATE trading_posts
        SET status = 'published',
            published_at = now(),
            updated_at = now(),
            source_payload = source_payload - '_rejection'
        WHERE id = :pid
    """), {"pid": post_id})

    await session.execute(sql_text("""
        INSERT INTO post_feedback (org_id, post_id, user_id, verdict, comments)
        VALUES (:oid, :pid, :uid, 'approved', 'human-approved from draft queue')
    """), {"oid": org_id, "pid": post_id, "uid": user_id})

    await session.commit()
    return DraftActionResult(post_id=post_id, new_status="published")


@router.post("/drafts/{post_id}/reject", response_model=DraftActionResult)
async def reject_draft(
    post_id: str,
    reason: str = Query(default="", max_length=500),
    org_id: str = Depends(current_org_id),
    user_id: str = Depends(current_user_id),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
    session: AsyncSession = Depends(_scoped_session),
) -> DraftActionResult:
    """Reject a draft — mark as failed and record why."""
    row = (await session.execute(sql_text("""
        SELECT id FROM trading_posts WHERE id = :pid AND status = 'draft'
    """), {"pid": post_id})).first()

    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")

    await session.execute(sql_text("""
        UPDATE trading_posts
        SET status = 'failed',
            updated_at = now()
        WHERE id = :pid
    """), {"pid": post_id})

    await session.execute(sql_text("""
        INSERT INTO post_feedback (org_id, post_id, user_id, verdict, comments)
        VALUES (:oid, :pid, :uid, 'rejected', :reason)
    """), {"oid": org_id, "pid": post_id, "uid": user_id,
            "reason": reason or "rejected from draft queue"})

    await session.commit()
    return DraftActionResult(post_id=post_id, new_status="failed")

# ─────────────────────────────────────────────────────────────────────
# Trash (S.7.6d.1)
# ─────────────────────────────────────────────────────────────────────

from datetime import UTC, datetime, timedelta
from apps.api.schemas.trash import TrashActionResult

TRASH_TTL_DAYS = 2


@router.delete("/posts/{post_id}", response_model=TrashActionResult)
async def soft_delete_post(
    post_id: str,
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped_session),
) -> TrashActionResult:
    """Move a trading post to the trash. Restorable for 2 days."""
    row = (await session.execute(sql_text("""
        SELECT id FROM trading_posts
        WHERE id = :pid AND deleted_at IS NULL
    """), {"pid": post_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Post not found or already deleted")

    await session.execute(sql_text("""
        UPDATE trading_posts SET deleted_at = now() WHERE id = :pid
    """), {"pid": post_id})
    await session.commit()

    return TrashActionResult(
        id=post_id, kind="trading", action="deleted",
        purge_at=datetime.now(UTC) + timedelta(days=TRASH_TTL_DAYS),
    )


@router.post("/posts/{post_id}/restore", response_model=TrashActionResult)
async def restore_post(
    post_id: str,
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped_session),
) -> TrashActionResult:
    """Bring a trashed post back into normal view."""
    row = (await session.execute(sql_text("""
        SELECT id FROM trading_posts
        WHERE id = :pid AND deleted_at IS NOT NULL
    """), {"pid": post_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Post not in trash")

    await session.execute(sql_text("""
        UPDATE trading_posts SET deleted_at = NULL WHERE id = :pid
    """), {"pid": post_id})
    await session.commit()

    return TrashActionResult(id=post_id, kind="trading", action="restored")


@router.delete("/posts/{post_id}/purge", response_model=TrashActionResult)
async def purge_post(
    post_id: str,
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped_session),
) -> TrashActionResult:
    """Permanently delete a trashed post. Cannot be undone."""
    row = (await session.execute(sql_text("""
        SELECT id FROM trading_posts
        WHERE id = :pid AND deleted_at IS NOT NULL
    """), {"pid": post_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Post not in trash")

    await session.execute(sql_text("""
        DELETE FROM trading_posts WHERE id = :pid
    """), {"pid": post_id})
    await session.commit()

    return TrashActionResult(id=post_id, kind="trading", action="purged")
