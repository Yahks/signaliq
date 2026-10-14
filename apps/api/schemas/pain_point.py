"""Pain point response schemas."""
from datetime import datetime

from pydantic import BaseModel


class PainPointResponse(BaseModel):
    id: str
    title: str
    description: str | None
    severity: float | None
    first_seen_at: datetime
    last_seen_at: datetime

    model_config = {"from_attributes": True}


class PainPointDetail(PainPointResponse):
    sample_records: list[dict]
