"""News chains: build, list, get."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, current_user_id, require_role
from apps.api.schemas.news_chain import (
    ChainBuildRequest,
    ChainBuildResponse,
    ChainBuildStatusResponse,
    ContentBuildRequest,
    ChainLinkResponse,
    ChainUpdateResponse,
    GeneratedPostResponse,
    ImpactResponse,
    NewsChainDetailResponse,
    NewsChainResponse,
    NewsEventResponse,
    PostEvidenceResponse,
    ScenarioResponse,
    WatchlistItemResponse,
)
from packages.ai.chain.event_extractor import parse_news_event_json
from packages.ai.error_text import classify as _classify_error
from packages.ai.error_text import classify as _classify_error
from packages.ai.error_text import classify as _classify_error
from packages.ai.error_text import classify as _classify_error
from packages.core.error import NotFoundError, ValidationError
from packages.db.models.news_chain import (
    ChainLink,
    GeneratedPost,
    NewsChain,
    NewsEvent,
    PostEvidence,
)
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/chains", tags=["chains"])


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


def _serialize_events(events: list) -> list[dict]:
    """Convert EventInput objects to JSON-safe dicts for the Celery task."""
    out = []
    for i, ev in enumerate(events):
        out.append({
            "_placeholder_id": f"ph_{i}",
            "external_id": ev.external_id,
            "headline": ev.headline,
            "summary": ev.summary,
            "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
            "reported_at": ev.reported_at.isoformat() if ev.reported_at else None,
            "location": ev.location,
            "actors": ev.actors,
            "event_type": ev.event_type,
            "status": ev.status,
            "confidence": ev.confidence,
            "sources": [
                {"name": s.name, "url": s.url, "type": s.type}
                for s in ev.sources
            ],
            "topics": ev.topics,
        })
    return out


@router.post("/build", response_model=ChainBuildResponse, status_code=202)
async def build_chain_endpoint(
    body: ContentBuildRequest,
    org_id: str = Depends(current_org_id),
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_scoped_session),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
) -> ChainBuildResponse:
    """Paste-anything chain build: extract → enrich → enqueue.

    1. Persist the paste to raw_records
    2. Charge the upload (1 token / 100 words)
    3. Extract events (JSON-shape or LLM)
    4. Enrich with related events from the org's pool
    5. Enqueue the chain build
    """
    from packages.ai.chain.enrich import enrich_with_related_events
    from packages.ai.chain.extract import (
        ensure_manual_paste_source,
        extract_events_from_any_input,
        persist_paste,
    )
    from packages.core.tokens import charge_for_upload
    from packages.blocks.loader import load_block_by_slug
    from apps.worker.tasks.news_chains import build_chain

    load_block_by_slug(body.block_slug, org_id=org_id)

    # Per-org rate limit
    from packages.core.rate_limit import check_org_chain_build_limit
    rl = check_org_chain_build_limit(org_id)
    if not rl.allowed:
        raise QuotaExceededError(
            f"Chain build rate limit reached ({rl.limit}/hour). "
            f"Try again in {rl.retry_after_seconds}s.",
            org_id=org_id,
            limit=rl.limit,
            retry_after_seconds=rl.retry_after_seconds,
        )

    # 1. Persist the paste
    source_id = await ensure_manual_paste_source(session, org_id)
    raw_id = await persist_paste(
        session, org_id, source_id, body.content, created_by=user_id,
    )

    # 2. Charge the upload
    word_count = len(body.content.split())
    await charge_for_upload(
        session, user_id, word_count=word_count, ref_id=raw_id, org_id=org_id,
    )

    # 3. Extract
    seeds = await extract_events_from_any_input(session, org_id, body.content)

    # 4. Enrich
    events = await enrich_with_related_events(session, org_id, seeds, max_total=20)

    # 5. Enqueue
    serialized = _serialize_events(events)
    result = build_chain.delay(
        org_id,
        serialized,
        body.block_slug,
        body.subtopic_slug,
        body.target_words,
    )

    return ChainBuildResponse(
        chain_id=result.id,
        status="processing",
        event_count=len(serialized),
    )


@router.get("/build/{task_id}", response_model=ChainBuildStatusResponse)
async def chain_build_status(
    task_id: str,
    _org_id: str = Depends(current_org_id),
) -> ChainBuildStatusResponse:
    """Poll the status of an in-flight chain build."""
    from celery.result import AsyncResult
    from apps.worker.celery_app import celery_app

    r = AsyncResult(task_id, app=celery_app)
    state = r.state  # PENDING | STARTED | SUCCESS | FAILURE | RETRY | REVOKED

    # Map Celery states to our statuses
    if state == "SUCCESS":
        payload = r.result or {}
        if isinstance(payload, dict):
            ok = payload.get("status") == "ok"
            err_text = payload.get("error") if not ok else None
            c = _classify_error(str(err_text)) if err_text else {}
            return ChainBuildStatusResponse(
                task_id=task_id,
                status="success" if ok else "failure",
                chain_id=payload.get("chain_id"),
                post_id=payload.get("post_id"),
                verification_status=payload.get("verification_status"),
                error=str(err_text)[:1000] if err_text else None,
                error_kind=c.get("kind"),
                error_message=c.get("message"),
                error_action=c.get("action"),
                error_retry_after_seconds=c.get("retry_after_seconds"),
            )
        return ChainBuildStatusResponse(task_id=task_id, status="success")

    if state == "FAILURE":
        err = r.result
        # S.6.2.3a — a Celery FAILURE result is either our own dict or
        # Celery's exception shape {exc_type, exc_module, exc_message}.
        if isinstance(err, dict) and "exc_message" in err:
            exc_type = err.get("exc_type") or "Exception"
            exc_msg = err.get("exc_message")
            if isinstance(exc_msg, (list, tuple)):
                exc_msg = exc_msg[0] if exc_msg else ""
            err_text = f"{exc_type}: {str(exc_msg)[:900]}"
        else:
            err_text = str(err)[:1000] if err else "task failed"
        c = _classify_error(err_text)
        return ChainBuildStatusResponse(
            task_id=task_id,
            status="failure",
            error=err_text,
            error_kind=c.get("kind"),
            error_message=c.get("message"),
            error_action=c.get("action"),
            error_retry_after_seconds=c.get("retry_after_seconds"),
        )

    if state in ("STARTED", "RETRY"):
        return ChainBuildStatusResponse(task_id=task_id, status="started")

    # PENDING (or unknown)
    return ChainBuildStatusResponse(task_id=task_id, status="pending")


@router.get("", response_model=list[NewsChainResponse])
async def list_chains(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(_scoped_session),
) -> list[NewsChainResponse]:
    stmt = select(NewsChain).order_by(NewsChain.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [NewsChainResponse.model_validate(c) for c in rows]


@router.get("/{chain_id}", response_model=NewsChainDetailResponse)
async def get_chain(
    chain_id: str,
    session: AsyncSession = Depends(_scoped_session),
) -> NewsChainDetailResponse:
    chain_row = (await session.execute(select(NewsChain).where(NewsChain.id == chain_id))).scalar_one_or_none()
    if not chain_row:
        raise NotFoundError("Chain not found")

    # Events in this chain = events referenced by any link
    link_stmt = select(ChainLink).where(ChainLink.chain_id == chain_id).order_by(ChainLink.sequence)
    links = (await session.execute(link_stmt)).scalars().all()

    event_ids = set()
    for link in links:
        event_ids.add(link.from_event_id)
        event_ids.add(link.to_event_id)

    events_stmt = select(NewsEvent).where(NewsEvent.id.in_(event_ids)) if event_ids else select(NewsEvent).where(False)
    events = (await session.execute(events_stmt)).scalars().all()

    post_row = (await session.execute(
        select(GeneratedPost).where(GeneratedPost.chain_id == chain_id).order_by(GeneratedPost.created_at.desc()).limit(1)
    )).scalar_one_or_none()

    evidence: list[PostEvidence] = []
    if post_row:
        ev_stmt = select(PostEvidence).where(PostEvidence.post_id == post_row.id)
        evidence = (await session.execute(ev_stmt)).scalars().all()

    # Wave 2: load impacts, scenarios, watchlist, updates
    impacts_rows = (await session.execute(sql_text("""
        SELECT id::text, label, category, direction, strength, reasoning, position
        FROM chain_impacts
        WHERE chain_id = :cid
        ORDER BY position
    """), {"cid": chain_id})).all()

    scenarios_rows = (await session.execute(sql_text("""
        SELECT id::text, name, condition, reasoning, position,
               candidate_symbols, quantified
        FROM chain_scenarios
        WHERE chain_id = :cid
        ORDER BY position
    """), {"cid": chain_id})).all()

    watchlist_rows = (await session.execute(sql_text("""
        SELECT id::text, label, symbol, reasoning, position, live_quote
        FROM chain_watchlist
        WHERE chain_id = :cid
        ORDER BY position
    """), {"cid": chain_id})).all()

    updates_rows = (await session.execute(sql_text("""
        SELECT cu.id::text, cu.event_id::text, e.headline, e.summary,
               cu.similarity, cu.reasoning, cu.attached_by, cu.created_at
        FROM chain_updates cu
        JOIN news_events e ON e.id = cu.event_id
        WHERE cu.chain_id = :cid
        ORDER BY cu.created_at DESC
        LIMIT 50
    """), {"cid": chain_id})).all()

    return NewsChainDetailResponse(
        chain=NewsChainResponse.model_validate(chain_row),
        events=[NewsEventResponse.model_validate(e) for e in events],
        links=[ChainLinkResponse.model_validate(l) for l in links],
        post=GeneratedPostResponse.model_validate(post_row) if post_row else None,
        evidence=[PostEvidenceResponse(
            claim_text=e.claim_text, claim_type=e.claim_type,
            source_kind=e.source_kind, source_id=e.source_id,
            confidence=e.confidence,
        ) for e in evidence],
        impacts=[ImpactResponse(
            id=r[0], label=r[1], category=r[2], direction=r[3],
            strength=r[4], reasoning=r[5], position=r[6],
        ) for r in impacts_rows],
        scenarios=[ScenarioResponse(
            id=r[0], name=r[1], condition=r[2], reasoning=r[3], position=r[4],
            candidate_symbols=list(r[5] or []),
            quantified=dict(r[6] or {}),
        ) for r in scenarios_rows],
        watchlist=[WatchlistItemResponse(
            id=r[0], label=r[1], symbol=r[2], reasoning=r[3], position=r[4],
            live_quote=dict(r[5] or {}),
        ) for r in watchlist_rows],
        updates=[ChainUpdateResponse(
            id=r[0], event_id=r[1], event_headline=r[2], event_summary=r[3],
            similarity=r[4], reasoning=r[5], attached_by=r[6], created_at=r[7],
        ) for r in updates_rows],
    )


# Separate endpoint: get a post by id
@router.get("/posts/{post_id}", response_model=GeneratedPostResponse)
async def get_post(
    post_id: str,
    session: AsyncSession = Depends(_scoped_session),
) -> GeneratedPostResponse:
    post = (await session.execute(select(GeneratedPost).where(GeneratedPost.id == post_id))).scalar_one_or_none()
    if not post:
        raise NotFoundError("Post not found")
    return GeneratedPostResponse.model_validate(post)

# ─────────────────────────────────────────────────────────────────────
# Trash (S.7.6d.1)
# ─────────────────────────────────────────────────────────────────────

from datetime import UTC, datetime, timedelta
from apps.api.deps import require_role
from apps.api.schemas.trash import TrashActionResult

TRASH_TTL_DAYS = 2


@router.delete("/posts/{post_id}", response_model=TrashActionResult)
async def soft_delete_generated_post(
    post_id: str,
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped_session),
) -> TrashActionResult:
    """Move a generated post to the trash. Restorable for 2 days."""
    row = (await session.execute(_t("""
        SELECT id FROM generated_posts
        WHERE id = :pid AND deleted_at IS NULL
    """), {"pid": post_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Post not found or already deleted")

    await session.execute(_t("""
        UPDATE generated_posts SET deleted_at = now() WHERE id = :pid
    """), {"pid": post_id})
    await session.commit()

    return TrashActionResult(
        id=post_id, kind="news", action="deleted",
        purge_at=datetime.now(UTC) + timedelta(days=TRASH_TTL_DAYS),
    )


@router.post("/posts/{post_id}/restore", response_model=TrashActionResult)
async def restore_generated_post(
    post_id: str,
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped_session),
) -> TrashActionResult:
    row = (await session.execute(_t("""
        SELECT id FROM generated_posts
        WHERE id = :pid AND deleted_at IS NOT NULL
    """), {"pid": post_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Post not in trash")

    await session.execute(_t("""
        UPDATE generated_posts SET deleted_at = NULL WHERE id = :pid
    """), {"pid": post_id})
    await session.commit()
    return TrashActionResult(id=post_id, kind="news", action="restored")


@router.delete("/posts/{post_id}/purge", response_model=TrashActionResult)
async def purge_generated_post(
    post_id: str,
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped_session),
) -> TrashActionResult:
    row = (await session.execute(_t("""
        SELECT id FROM generated_posts
        WHERE id = :pid AND deleted_at IS NOT NULL
    """), {"pid": post_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Post not in trash")

    await session.execute(_t("""
        DELETE FROM generated_posts WHERE id = :pid
    """), {"pid": post_id})
    await session.commit()
    return TrashActionResult(id=post_id, kind="news", action="purged")
