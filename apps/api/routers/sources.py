"""Sources CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, require_role
from apps.api.schemas.source import SourceCreate, SourceResponse
from pydantic import BaseModel, Field
from packages.core.error import NotFoundError
from packages.db.models.source import Source
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/sources", tags=["sources"])


# --- Stock news fetcher schemas ---
class StockNewsFetchRequest(BaseModel):
    tickers: list[str] | None = None
    auto_build: bool = False
    subtopic_slug: str = "us_stocks"
    max_events: int = Field(default=30, ge=5, le=100)


class StockNewsFetchResponse(BaseModel):
    task_id: str
    status: str
    tickers: list[str] | None
    auto_build: bool



async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    """Session bound to the current tenant."""
    await set_current_org(session, org_id)
    return session


@router.post("", response_model=SourceResponse, status_code=201)
async def create_source(
    body: SourceCreate,
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped_session),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
) -> SourceResponse:
    source = Source(
        org_id=org_id,
        name=body.name,
        source_type=body.source_type,
        description=body.description,
        collection_method=body.collection_method,
        retention_policy=body.retention_policy,
        config=body.config,
    )
    session.add(source)
    await session.flush()
    await session.refresh(source)
    return SourceResponse.model_validate(source)


@router.get("", response_model=list[SourceResponse])
async def list_sources(
    session: AsyncSession = Depends(_scoped_session),
) -> list[SourceResponse]:
    result = await session.execute(
        select(Source).where(Source.deleted_at.is_(None)).order_by(Source.created_at.desc())
    )
    return [SourceResponse.model_validate(s) for s in result.scalars().all()]


@router.post("/fetch-stock-news", response_model=StockNewsFetchResponse, status_code=202)
async def trigger_stock_news_fetch(
    body: StockNewsFetchRequest,
    org_id: str = Depends(current_org_id),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
) -> StockNewsFetchResponse:
    """Trigger a live fetch of stock news + extraction for this org.

    Rate limited: 10 fetches per hour per org (protects LLM spend + SEC rate
    limits + CNBC politeness).
    """
    from packages.core.rate_limit import check_org_fetch_limit
    rl = check_org_fetch_limit(org_id)
    if not rl.allowed:
        raise QuotaExceededError(
            f"Fetch rate limit reached ({rl.limit}/hour). "
            f"Try again in {rl.retry_after_seconds}s.",
            org_id=org_id,
            limit=rl.limit,
            retry_after_seconds=rl.retry_after_seconds,
        )

    from apps.worker.tasks.fetch_stock_news import fetch_stock_news

    result = fetch_stock_news.delay(
        org_id,
        body.tickers,
        body.auto_build,
        body.subtopic_slug,
        body.max_events,
    )
    return StockNewsFetchResponse(
        task_id=result.id,
        status="queued",
        tickers=body.tickers,
        auto_build=body.auto_build,
    )

@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: str,
    session: AsyncSession = Depends(_scoped_session),
) -> SourceResponse:
    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source or source.deleted_at is not None:
        raise NotFoundError("Source not found")
    return SourceResponse.model_validate(source)


@router.delete("/{source_id}", status_code=204, response_class=Response)
async def delete_source(
    source_id: str,
    session: AsyncSession = Depends(_scoped_session),
    _role: str = Depends(require_role("owner", "admin")),
):
    from datetime import UTC, datetime
    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise NotFoundError("Source not found")
    source.deleted_at = datetime.now(UTC)
    return Response(status_code=204)


from pydantic import BaseModel, Field


