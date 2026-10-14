"""Record request/response schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RawRecordResponse(BaseModel):
    id: str
    source_id: str
    ingestion_job_id: str | None
    source_record_id: str | None
    author_identifier: str | None
    original_timestamp: datetime | None
    text: str
    language: str | None
    metadata: dict[str, Any]
    content_hash: str
    created_at: datetime


class NormalizedRecordResponse(BaseModel):
    id: str
    raw_record_id: str
    normalized_text: str
    detected_language: str
    language_confidence: float | None
    is_duplicate: bool
    is_spam: bool
    is_bot_suspected: bool
    urls: list[str]
    hashtags: list[str]
    mentions: list[str]
    tokens_count: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RecordDetailResponse(BaseModel):
    raw: RawRecordResponse
    normalized: NormalizedRecordResponse | None


class RecordListResponse(BaseModel):
    items: list[NormalizedRecordResponse]
    total: int
    limit: int
    offset: int
