"""Daily brief schemas."""
from datetime import date, datetime

from pydantic import BaseModel


class DailyBriefResponse(BaseModel):
    id: str
    brief_date: date
    title: str
    body: str
    word_count: int | None
    status: str
    block_slug: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DailyBriefListResponse(BaseModel):
    items: list[DailyBriefResponse]
    total: int
