"""Studio endpoints — on-demand chat that produces trading articles.

Studio lives inside the trading block. The block gate is enforced
at request time; if trading is disabled for the org, every Studio
endpoint returns 403.

S.8.2 wires the endpoints. The generation path is a stub in
packages/trading/studio.py; S.8.3 replaces it with the real
pipeline.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, current_user_id, require_role
from apps.api.schemas.studio import (
    StudioDeleteResponse,
    StudioMessageItem,
    StudioMessageListResponse,
    StudioMessageRequest,
    StudioMessageResponse,
)
from packages.core.blocks import is_block_enabled
from packages.core.logging import get_logger
from packages.db.models.studio_message import StudioMessage
from packages.db.session import get_session
from packages.db.tenant import set_current_org
from packages.trading.studio import generate_studio_response

log = get_logger(__name__)

router = APIRouter(prefix="/api/studio", tags=["studio"])


async def _scoped(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


async def _require_trading_block(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped),
) -> None:
    """Studio is gated by the trading block being enabled."""
    if not await is_block_enabled(session, org_id, "trading"):
        raise HTTPException(
            status_code=403,
            detail="Trading block is not enabled for your organisation.",
        )


@router.post(
    "/message",
    response_model=StudioMessageResponse,
    status_code=201,
    dependencies=[Depends(_require_trading_block)],
)
async def post_message(
    body: StudioMessageRequest,
    org_id: str = Depends(current_org_id),
    user_id: str = Depends(current_user_id),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
    session: AsyncSession = Depends(_scoped),
) -> StudioMessageResponse:
    """Accept a prompt, generate a response, persist both as messages."""
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    # 1. Persist the user's prompt.
    user_msg = StudioMessage(
        org_id=org_id,
        user_id=user_id,
        role="user",
        content=prompt,
    )
    session.add(user_msg)
    await session.flush()   # assign the id

    # 2. Generate the assistant's response.
    kind, content, trading_post_id = await generate_studio_response(
        session,
        org_id=org_id,
        user_id=user_id,
        prompt=prompt,
        form=body.form,
        tone=body.tone,
        track=body.track,
        image_url=body.image_url,
    )

    # 3. Persist the assistant's response.
    assistant_msg = StudioMessage(
        org_id=org_id,
        user_id=user_id,
        role="assistant",
        content=content,
        prompt_kind=kind,
        trading_post_id=trading_post_id,
        track=body.track,
        image_url=body.image_url,
    )
    session.add(assistant_msg)
    await session.commit()

    log.info(
        "studio.message_posted",
        org_id=org_id[:8],
        user_id=user_id[:8],
        kind=kind,
        has_post=trading_post_id is not None,
    )

    return StudioMessageResponse(
        message_id=assistant_msg.id,
        kind=kind,
        content=content,
        trading_post_id=trading_post_id,
        track=body.track,
    )


@router.get(
    "/messages/counts",
    dependencies=[Depends(_require_trading_block)],
)
async def message_counts(
    session: AsyncSession = Depends(_scoped),
) -> dict:
    """Return counts for the two chat surfaces.

      user   = manual chat messages (user_id NOT NULL)
      system = auto-generated movers articles (user_id IS NULL)
    """
    from sqlalchemy import text as _sql_text
    row = (await session.execute(_sql_text("""
        SELECT
          COUNT(*) FILTER (WHERE user_id IS NOT NULL) AS user_count,
          COUNT(*) FILTER (WHERE user_id IS NULL) AS system_count
        FROM studio_messages
        WHERE deleted_at IS NULL
    """))).first()
    return {
        "user": int(row[0] or 0),
        "system": int(row[1] or 0),
    }


@router.get(
    "/messages",
    response_model=StudioMessageListResponse,
    dependencies=[Depends(_require_trading_block)],
)
async def list_messages(
    limit: int = Query(default=50, ge=1, le=200),
    filter: str | None = Query(
        default=None,
        description=(
            "'user' = manual Studio chat (user_id IS NOT NULL); "
            "'system' = auto-generated (user_id IS NULL); "
            "omit for all."
        ),
    ),
    session: AsyncSession = Depends(_scoped),
) -> StudioMessageListResponse:
    """Return the org's recent Studio messages, oldest first.

    The `filter` parameter splits the two surfaces on the chat page:

      filter=user    the manual chat — user prompts and responses
      filter=system  the auto feed — movers-generated articles
      (omitted)      both, interleaved by time
    """
    # Latest `limit` rows, then reverse so the caller sees them in
    # chronological order (the scrollback view).
    stmt = (
        select(StudioMessage)
        .where(StudioMessage.deleted_at.is_(None))
        .order_by(StudioMessage.created_at.desc())
        .limit(limit)
    )
    if filter == "user":
        stmt = stmt.where(StudioMessage.user_id.is_not(None))
    elif filter == "system":
        stmt = stmt.where(StudioMessage.user_id.is_(None))
    # Any other value (or None) returns both.
    rows = (await session.execute(stmt)).scalars().all()
    rows = list(reversed(rows))
    # S.8.9.7.1 — compute is_system from user_id NULL. The ORM
    # doesn't have that flag; it's derived here.
    items = []
    for r in rows:
        item = StudioMessageItem.model_validate(r)
        item.is_system = r.user_id is None
        items.append(item)
    return StudioMessageListResponse(items=items)


@router.delete(
    "/messages/{message_id}",
    response_model=StudioDeleteResponse,
    dependencies=[Depends(_require_trading_block)],
)
async def delete_message(
    message_id: str,
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped),
) -> StudioDeleteResponse:
    """Soft-delete a message. If it produced a post, soft-delete that too."""
    row = (await session.execute(
        select(StudioMessage)
        .where(StudioMessage.id == message_id)
        .where(StudioMessage.deleted_at.is_(None))
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    row.deleted_at = datetime.now(UTC)

    # If the message produced an article, soft-delete the trading_post.
    if row.trading_post_id:
        from sqlalchemy import text as sql_text
        await session.execute(sql_text("""
            UPDATE trading_posts SET deleted_at = now()
            WHERE id = :pid AND deleted_at IS NULL
        """), {"pid": row.trading_post_id})

    await session.commit()
    log.info("studio.message_deleted", message_id=message_id[:8])
    return StudioDeleteResponse(id=message_id, action="deleted")


# ─────────────────────────────────────────────────────────────────────
# S.8.11.6 — chart image upload
# ─────────────────────────────────────────────────────────────────────


@router.post(
    "/upload",
    dependencies=[Depends(_require_trading_block)],
)
async def upload_chart_image(
    file: UploadFile = File(...),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
) -> dict:
    """Accept a chart image, save it, return the URL.

    The URL is what gets attached to a studio_messages row via
    StudioMessageRequest.image_url (S.8.11.8). Files land under
    /static/uploads/ and are served by the /static mount from
    S.8.10.

    Guards: 8 MB cap, .png/.jpg/.jpeg/.gif/.webp allowlist. See
    packages/trading/upload.py.
    """
    from packages.trading.upload import save_upload

    data = await file.read()
    try:
        url = save_upload(data, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log.info(
        "studio.upload_ok",
        filename=file.filename,
        bytes=len(data),
        url=url,
    )
    return {"url": url}
