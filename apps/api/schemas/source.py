"""Source request/response schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=32)
    description: str | None = None
    collection_method: str | None = None
    retention_policy: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class SourceResponse(BaseModel):
    id: str
    name: str
    source_type: str
    description: str | None
    collection_method: str | None
    retention_policy: str | None
    config: dict[str, Any]
    last_ingested_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
