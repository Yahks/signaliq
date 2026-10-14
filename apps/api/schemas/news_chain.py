"""News chain request/response schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChainBuildRequest(BaseModel):
    events: list[dict[str, Any]] = Field(min_length=2, max_length=500)
    block_slug: str = Field(default="news", min_length=1, max_length=64)
    subtopic_slug: str | None = Field(default=None, max_length=64)
    chain_hint: dict[str, Any] | None = None
    target_words: int | None = Field(default=None, ge=200, le=5000)


class ContentBuildRequest(BaseModel):
    """Paste-anything chain build. The user provides arbitrary text."""
    content: str = Field(min_length=20, max_length=50000)
    block_slug: str = Field(default="news", min_length=1, max_length=64)
    subtopic_slug: str | None = Field(default=None, max_length=64)
    target_words: int | None = Field(default=None, ge=200, le=5000)


class ChainBuildStatusResponse(BaseModel):
    """Poll response for an in-flight chain build."""
    task_id: str
    status: str   # 'pending' | 'started' | 'success' | 'failure'
    chain_id: str | None = None
    post_id: str | None = None
    verification_status: str | None = None
    error: str | None = None            # raw, for logs / admin
    reason: str | None = None
    # S.6.2.3 — user-facing, structured
    error_kind: str | None = None       # rate_limit | invalid_key | ...
    error_message: str | None = None    # 'Your Groq rate limit is reached...'
    error_action: str | None = None     # 'Add another key at /settings/...'
    error_retry_after_seconds: int | None = None


class ChainBuildResponse(BaseModel):
    chain_id: str
    status: str
    event_count: int


class NewsEventResponse(BaseModel):
    id: str
    headline: str
    summary: str
    occurred_at: datetime | None
    reported_at: datetime | None
    location: str | None
    actors: list[str]
    event_type: str | None
    status: str
    confidence: float
    created_at: datetime

    model_config = {"from_attributes": True}


class ChainLinkResponse(BaseModel):
    from_event_id: str
    to_event_id: str
    link_type: str
    rationale: str
    confidence: float
    sequence: int

    model_config = {"from_attributes": True}


class PostEvidenceResponse(BaseModel):
    claim_text: str
    claim_type: str
    source_kind: str
    source_id: str | None
    confidence: float | None


class GeneratedPostResponse(BaseModel):
    id: str
    chain_id: str
    title: str
    body: str
    format: str
    tone: str
    word_count: int | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class NewsChainResponse(BaseModel):
    id: str
    title: str
    thesis: str | None
    status: str
    primary_entities: list[str]
    primary_topics: list[str]
    confidence: float | None
    block_id: str | None = None
    subtopic_slug: str | None = None
    update_count: int = 0
    last_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Wave 2: living chain sections ---

class ImpactResponse(BaseModel):
    id: str
    label: str
    category: str
    direction: str | None
    strength: str | None
    reasoning: str | None
    position: int

    model_config = {"from_attributes": True}


class ScenarioResponse(BaseModel):
    id: str
    name: str
    condition: str
    reasoning: str
    position: int
    candidate_symbols: list[str] = []
    quantified: dict = {}

    model_config = {"from_attributes": True}


class WatchlistItemResponse(BaseModel):
    id: str
    label: str
    symbol: str | None
    reasoning: str | None
    position: int
    live_quote: dict = {}

    model_config = {"from_attributes": True}


class ChainUpdateResponse(BaseModel):
    id: str
    event_id: str
    event_headline: str
    event_summary: str
    similarity: float
    reasoning: str | None
    attached_by: str
    created_at: datetime


class NewsChainDetailResponse(BaseModel):
    chain: NewsChainResponse
    events: list[NewsEventResponse]
    links: list[ChainLinkResponse]
    post: GeneratedPostResponse | None
    evidence: list[PostEvidenceResponse]
    # Wave 2
    impacts: list[ImpactResponse] = []
    scenarios: list[ScenarioResponse] = []
    watchlist: list[WatchlistItemResponse] = []
    updates: list[ChainUpdateResponse] = []
