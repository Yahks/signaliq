"""Studio API schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StudioMessageRequest(BaseModel):
    """Request body for POST /api/studio/message."""
    prompt: str = Field(min_length=1, max_length=8000)
    # Optional per-message overrides. Both default to the form/tone
    # tables' defaults if omitted.
    form: str | None = None
    tone: str | None = None
    # S.8.4 — audience track. 'retail' or 'quant'. NULL means no
    # track directive is applied to the generation prompt.
    track: str | None = None
    # S.8.11.8 — URL of an uploaded chart image. The pipeline reads
    # the image, runs vision analysis, and injects the analysis
    # into the article prompt.
    image_url: str | None = None


class StudioMessageResponse(BaseModel):
    """Response for POST /api/studio/message."""
    message_id: str
    kind: str                    # "article" | "quick_answer"
    content: str
    trading_post_id: str | None = None
    track: str | None = None


class StudioMessageItem(BaseModel):
    """One row in a chat scrollback."""
    id: str
    role: str                    # "user" | "assistant"
    content: str
    prompt_kind: str | None = None
    trading_post_id: str | None = None
    track: str | None = None
    image_url: str | None = None
    created_at: datetime
    # S.8.9.7.1 — true for messages with user_id=NULL (currently:
    # movers auto-generated articles). The UI shows an "auto" badge.
    is_system: bool = False

    model_config = {"from_attributes": True}


class StudioMessageListResponse(BaseModel):
    items: list[StudioMessageItem]


class StudioDeleteResponse(BaseModel):
    id: str
    action: str                  # "deleted"
