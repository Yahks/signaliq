"""Records: list normalized records, get detail with raw + normalized."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id
from apps.api.schemas.record import (
    NormalizedRecordResponse,
    RawRecordResponse,
    RecordDetailResponse,
    RecordListResponse,
)
from packages.core.error import NotFoundError
from packages.db.models.normalized_record import NormalizedRecord
from packages.db.models.raw_record import RawRecord
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/records", tags=["records"])


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.get("", response_model=RecordListResponse)
async def list_records(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    language: str | None = Query(default=None),
    source_id: str | None = Query(default=None),
    session: AsyncSession = Depends(_scoped_session),
) -> RecordListResponse:
    base = select(NormalizedRecord)
    if language:
        base = base.where(NormalizedRecord.detected_language == language)
    if source_id:
        # join through raw_record
        base = base.join(
            RawRecord, RawRecord.id == NormalizedRecord.raw_record_id
        ).where(RawRecord.source_id == source_id)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    rows_stmt = base.order_by(NormalizedRecord.created_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(rows_stmt)).scalars().all()

    return RecordListResponse(
        items=[NormalizedRecordResponse.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{record_id}", response_model=RecordDetailResponse)
async def get_record(
    record_id: str,
    session: AsyncSession = Depends(_scoped_session),
) -> RecordDetailResponse:
    nr_result = await session.execute(
        select(NormalizedRecord).where(NormalizedRecord.id == record_id)
    )
    nr = nr_result.scalar_one_or_none()
    if not nr:
        raise NotFoundError("Record not found")

    rr_result = await session.execute(
        select(RawRecord).where(RawRecord.id == nr.raw_record_id)
    )
    rr = rr_result.scalar_one_or_none()
    if not rr:
        raise NotFoundError("Underlying raw record missing")

    return RecordDetailResponse(
        raw=RawRecordResponse(
            id=rr.id,
            source_id=rr.source_id,
            ingestion_job_id=rr.ingestion_job_id,
            source_record_id=rr.source_record_id,
            author_identifier=rr.author_identifier,
            original_timestamp=rr.original_timestamp,
            text=rr.text,
            language=rr.language,
            metadata=rr.metadata_,
            content_hash=rr.content_hash,
            created_at=rr.created_at,
        ),
        normalized=NormalizedRecordResponse.model_validate(nr),
    )
