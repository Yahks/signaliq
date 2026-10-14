"""Trading block API schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TradingSubblockResponse(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None
    category: str | None
    cadence: str
    source_kind: str | None
    enabled: bool
    sort_order: int
    theme: dict | None = None
    # Aggregates
    post_count: int = 0
    latest_post_at: datetime | None = None

    model_config = {"from_attributes": True}


class TradingSubblockListResponse(BaseModel):
    items: list[TradingSubblockResponse]
    total: int


class TradingPostResponse(BaseModel):
    id: str
    subblock_id: str
    subblock_slug: str | None = None
    title: str
    body: str
    format: str
    source_kind: str
    source_urls: list[str] = []
    symbols: list[str] = []
    status: str
    word_count: int | None
    published_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TradingPostListResponse(BaseModel):
    items: list[TradingPostResponse]
    total: int
    limit: int
    offset: int


class TradingGenerateResponse(BaseModel):
    # S.9.18 — synchronous generation. There is no task_id; the
    # endpoint runs the subblock and returns the result.
    status: str
    slug: str
    post_id: str | None = None
    word_count: int = 0
    # When status is a skip, this carries the reason a human can
    # read (e.g. "no trade_log rows yet"). Optional.
    message: str | None = None


class TradingSubblockHealth(BaseModel):
    slug: str
    name: str
    cadence: str
    source_kind: str | None
    # 24h stats
    posts_24h: int
    drafts_24h: int
    last_published_at: datetime | None
    last_created_at: datetime | None
    avg_word_count_24h: float | None
    # Health verdict
    minutes_since_last: int | None
    expected_cadence_minutes: int
    status: str   # "healthy" | "delayed" | "stale" | "never_run"


class TradingHealthResponse(BaseModel):
    items: list[TradingSubblockHealth]
    summary: dict
    generated_at: datetime


class SymbolPostMention(BaseModel):
    post_id: str
    subblock_slug: str
    subblock_name: str
    title: str
    published_at: datetime | None
    word_count: int | None


class SymbolTimelineResponse(BaseModel):
    ticker: str
    total_mentions: int
    subblocks: list[str]
    latest_mention: datetime | None
    mentions: list[SymbolPostMention]
    related_tickers: list[dict]


class SymbolIndexItem(BaseModel):
    ticker: str
    mentions: int


class SymbolIndexResponse(BaseModel):
    items: list[SymbolIndexItem]
    total: int


class DraftPostResponse(BaseModel):
    id: str
    subblock_slug: str
    subblock_name: str
    title: str
    body: str
    source_kind: str
    word_count: int | None
    created_at: datetime
    unsupported_claims: list[str]
    regenerated: bool


class DraftListResponse(BaseModel):
    items: list[DraftPostResponse]
    total: int


class DraftActionResult(BaseModel):
    post_id: str
    new_status: str

# S.9.21 — per-subblock schedule
class SubblockSchedule(BaseModel):
    """One subblock's schedule config."""
    enabled: bool = False
    interval_hours: int = 6


class ScheduleRequest(BaseModel):
    """Full per-subblock schedule map."""
    subblocks: dict[str, SubblockSchedule]


class ScheduleResponse(BaseModel):
    """Current per-subblock schedule + next-fire hints."""
    subblocks: dict[str, SubblockSchedule]
    # Per-subblock next fire timestamp (ISO). None if disabled or
    # never fired.
    next_fire: dict[str, str | None] = {}


class SubblockInfo(BaseModel):
    """Available subblock for the settings picker."""
    slug: str
    name: str
    description: str | None
    cadence: str
    source_kind: str | None
    # True if the subblock can run for this org right now (its
    # preconditions are met). Not a hard gate — the scheduler will
    # try anyway and skip on precondition misses — but a useful hint
    # for the UI.
    ready: bool = True

