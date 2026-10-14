"""Unified trash list.

One endpoint that reads soft-deleted rows from all three content
tables. The per-table delete/restore/purge verbs live on their own
routers.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id
from apps.api.schemas.trash import TrashItem, TrashListResponse
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/trash", tags=["trash"])

TRASH_TTL_DAYS = 2


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.get("", response_model=TrashListResponse)
async def list_trash(
    session: AsyncSession = Depends(_scoped_session),
) -> TrashListResponse:
    """Everything in the trash, newest-deleted first."""
    items: list[TrashItem] = []

    for kind, table, title_col in (
        ("trading", "trading_posts", "title"),
        ("news", "generated_posts", "title"),
        ("brief", "daily_briefs", "title"),
    ):
        rows = (await session.execute(sql_text(f"""
            SELECT id::text, {title_col}, word_count, deleted_at
            FROM {table}
            WHERE deleted_at IS NOT NULL
            ORDER BY deleted_at DESC
            LIMIT 200
        """))).all()
        for r in rows:
            items.append(TrashItem(
                id=r[0], kind=kind, title=r[1] or "(untitled)",
                word_count=r[2], deleted_at=r[3],
                purge_at=r[3] + timedelta(days=TRASH_TTL_DAYS),
            ))

    items.sort(key=lambda i: i.deleted_at, reverse=True)
    return TrashListResponse(items=items, total=len(items))
