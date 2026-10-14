"""Search request/response schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=20, ge=1, le=100)
    min_similarity: float = Field(default=0.0, ge=0.0, le=1.0)


class SearchHit(BaseModel):
    record_id: str
    normalized_text: str
    detected_language: str
    similarity: float
    created_at: datetime


class SemanticSearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    total: int
