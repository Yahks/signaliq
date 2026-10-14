"""Cluster response schemas."""
from datetime import datetime

from pydantic import BaseModel


class ClusterResponse(BaseModel):
    id: str
    title: str
    summary: str | None
    volume: int
    sentiment_avg: float | None
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ClusterMemberSample(BaseModel):
    record_id: str
    normalized_text: str


class ClusterDetailResponse(BaseModel):
    id: str
    title: str
    summary: str | None
    volume: int
    sentiment_avg: float | None
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    sample_records: list[ClusterMemberSample]
    created_at: datetime
