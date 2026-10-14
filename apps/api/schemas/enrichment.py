"""Enrichment, entity, topic response schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EnrichmentResponse(BaseModel):
    id: str
    normalized_record_id: str
    sentiment_label: str | None
    sentiment_score: float | None
    sentiment_confidence: float | None
    emotions: dict[str, float]
    intent: str | None
    intent_confidence: float | None
    raw_topics: list[str]
    raw_entities: list[dict[str, Any]]
    pain_point_signals: list[str]
    feature_request_signals: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class EntityResponse(BaseModel):
    id: str
    canonical_name: str
    type: str
    mention_count: int
    created_at: datetime


class EntityDetailResponse(BaseModel):
    id: str
    canonical_name: str
    type: str
    mention_count: int
    avg_sentiment: float | None
    sample_mentions: list[dict[str, Any]]
    created_at: datetime


class TopicResponse(BaseModel):
    id: str
    name: str
    record_count: int
    created_at: datetime
