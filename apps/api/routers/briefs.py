"""Daily brief endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, require_role
from apps.api.schemas.brief import DailyBriefListResponse, DailyBriefResponse
from packages.core.error import NotFoundError
from packages.db.models.daily_brief import DailyBrief
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/briefs", tags=["briefs"])


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.get("", response_model=DailyBriefListResponse)
async def list_briefs(
    limit: int = Query(default=30, ge=1, le=100),
    session: AsyncSession = Depends(_scoped_session),
) -> DailyBriefListResponse:
    stmt = select(DailyBrief).order_by(DailyBrief.brief_date.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(select(func.count()).select_from(DailyBrief))).scalar_one()
    return DailyBriefListResponse(
        items=[DailyBriefResponse.model_validate(b) for b in rows],
        total=total,
    )


@router.get("/{brief_id}", response_model=DailyBriefResponse)
async def get_brief(
    brief_id: str,
    session: AsyncSession = Depends(_scoped_session),
) -> DailyBriefResponse:
    row = (await session.execute(select(DailyBrief).where(DailyBrief.id == brief_id))).scalar_one_or_none()
    if not row:
        raise NotFoundError("Brief not found")
    return DailyBriefResponse.model_validate(row)


@router.post("/generate", status_code=202)
async def trigger_generation(
    window_hours: int = Query(default=24, ge=1, le=720),
    force: bool = Query(default=True),
    org_id: str = Depends(current_org_id),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
) -> dict:
    """Trigger on-demand brief generation for the current org."""
    from apps.worker.tasks.briefs import generate_daily_brief_task

    # Per-org rate limit (5 briefs per hour)
    from packages.core.rate_limit import check_rate_limit
    rl = check_rate_limit(
        namespace="org", identifier=org_id, route="brief-generate",
        limit=5, window_seconds=3600,
    )
    if not rl.allowed:
        from packages.core.error import QuotaExceededError
        raise QuotaExceededError(
            f"Brief generation rate limit reached ({rl.limit}/hour). "
            f"Try again in {rl.retry_after_seconds}s.",
        )

    task = generate_daily_brief_task.delay(org_id, window_hours, force)
    return {"task_id": task.id, "status": "queued"}

# ─────────────────────────────────────────────────────────────────────
# Trash (S.7.6d.1)
# ─────────────────────────────────────────────────────────────────────

from datetime import UTC, datetime, timedelta
from apps.api.deps import require_role
from apps.api.schemas.trash import TrashActionResult

TRASH_TTL_DAYS = 2


@router.delete("/{brief_id}", response_model=TrashActionResult)
async def soft_delete_brief(
    brief_id: str,
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped_session),
) -> TrashActionResult:
    row = (await session.execute(sql_text("""
        SELECT id FROM daily_briefs
        WHERE id = :bid AND deleted_at IS NULL
    """), {"bid": brief_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Brief not found or already deleted")

    await session.execute(sql_text("""
        UPDATE daily_briefs SET deleted_at = now() WHERE id = :bid
    """), {"bid": brief_id})
    await session.commit()

    return TrashActionResult(
        id=brief_id, kind="brief", action="deleted",
        purge_at=datetime.now(UTC) + timedelta(days=TRASH_TTL_DAYS),
    )


@router.post("/{brief_id}/restore", response_model=TrashActionResult)
async def restore_brief(
    brief_id: str,
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped_session),
) -> TrashActionResult:
    row = (await session.execute(sql_text("""
        SELECT id FROM daily_briefs
        WHERE id = :bid AND deleted_at IS NOT NULL
    """), {"bid": brief_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Brief not in trash")

    await session.execute(sql_text("""
        UPDATE daily_briefs SET deleted_at = NULL WHERE id = :bid
    """), {"bid": brief_id})
    await session.commit()
    return TrashActionResult(id=brief_id, kind="brief", action="restored")


@router.delete("/{brief_id}/purge", response_model=TrashActionResult)
async def purge_brief(
    brief_id: str,
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped_session),
) -> TrashActionResult:
    row = (await session.execute(sql_text("""
        SELECT id FROM daily_briefs
        WHERE id = :bid AND deleted_at IS NOT NULL
    """), {"bid": brief_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Brief not in trash")

    await session.execute(sql_text("""
        DELETE FROM daily_briefs WHERE id = :bid
    """), {"bid": brief_id})
    await session.commit()
    return TrashActionResult(id=brief_id, kind="brief", action="purged")
