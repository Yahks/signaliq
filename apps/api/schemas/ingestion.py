"""Ingestion request/response schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class JobResponse(BaseModel):
    id: str
    source_id: str
    status: str
    file_format: str | None
    total_records: int | None
    processed_records: int
    failed_records: int
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    job_id: str
    status: str
    message: str
