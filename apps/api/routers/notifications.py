"""Follow chains, list notifications, mark as read."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, current_user_id
from apps.api.schemas.notification import (
    FollowStatusResponse,
    NotificationResponse,
    NotificationsListResponse,
)
from packages.core.error import NotFoundError
from packages.db.models.news_chain import NewsChain
from packages.db.models.notification import ChainFollow, Notification
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api", tags=["notifications"])


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


# ---------- Follows ----------

@router.post("/chains/{chain_id}/follow", response_model=FollowStatusResponse)
async def follow_chain(
    chain_id: str,
    org_id: str = Depends(current_org_id),
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_scoped_session),
) -> FollowStatusResponse:
    # Ensure chain exists and belongs to this org (RLS enforced)
    chain = (await session.execute(select(NewsChain).where(NewsChain.id == chain_id))).scalar_one_or_none()
    if not chain:
        raise NotFoundError("Chain not found")

    # Insert if not already following
    existing = (await session.execute(
        select(ChainFollow).where(
            ChainFollow.user_id == user_id,
            ChainFollow.chain_id == chain_id,
        )
    )).scalar_one_or_none()

    if not existing:
        session.add(ChainFollow(org_id=org_id, user_id=user_id, chain_id=chain_id))
        await session.flush()

    return FollowStatusResponse(chain_id=chain_id, following=True)


@router.delete("/chains/{chain_id}/follow", response_model=FollowStatusResponse)
async def unfollow_chain(
    chain_id: str,
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_scoped_session),
) -> FollowStatusResponse:
    existing = (await session.execute(
        select(ChainFollow).where(
            ChainFollow.user_id == user_id,
            ChainFollow.chain_id == chain_id,
        )
    )).scalar_one_or_none()
    if existing:
        await session.delete(existing)
    return FollowStatusResponse(chain_id=chain_id, following=False)


@router.get("/chains/{chain_id}/follow", response_model=FollowStatusResponse)
async def get_follow_status(
    chain_id: str,
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_scoped_session),
) -> FollowStatusResponse:
    existing = (await session.execute(
        select(ChainFollow).where(
            ChainFollow.user_id == user_id,
            ChainFollow.chain_id == chain_id,
        )
    )).scalar_one_or_none()
    return FollowStatusResponse(chain_id=chain_id, following=existing is not None)


# ---------- Notifications ----------

@router.get("/notifications", response_model=NotificationsListResponse)
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    unread_only: bool = Query(default=False),
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_scoped_session),
) -> NotificationsListResponse:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    total = (await session.execute(
        select(func.count()).select_from(Notification).where(Notification.user_id == user_id)
    )).scalar_one()

    unread = (await session.execute(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
    )).scalar_one()

    return NotificationsListResponse(
        items=[NotificationResponse.model_validate(n) for n in rows],
        total=total,
        unread=unread,
    )


@router.post("/notifications/{notification_id}/read", response_model=NotificationResponse)
async def mark_read(
    notification_id: str,
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_scoped_session),
) -> NotificationResponse:
    from datetime import UTC, datetime

    row = (await session.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )).scalar_one_or_none()
    if not row:
        raise NotFoundError("Notification not found")
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
        await session.flush()
    return NotificationResponse.model_validate(row)


@router.post("/notifications/read-all", response_model=dict)
async def mark_all_read(
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_scoped_session),
) -> dict:
    from datetime import UTC, datetime

    result = await session.execute(
        sql_text("""
            UPDATE notifications
            SET read_at = now()
            WHERE user_id = :uid
              AND read_at IS NULL
              AND org_id = current_setting('app.current_org_id', true)::uuid
        """),
        {"uid": user_id},
    )
    return {"marked_read": result.rowcount or 0}
