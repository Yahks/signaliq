"""Onboarding API schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field


class BlockOption(BaseModel):
    """A block the picker can show. Name/description come from the
    blocks registry so the frontend never hardcodes them."""
    slug: str
    name: str
    description: str | None = None


class OnboardingStateResponse(BaseModel):
    guide_seen: bool
    completed: bool
    selected_blocks: list[str]
    available_blocks: list[BlockOption]


class AcknowledgeResponse(BaseModel):
    ok: bool
    completed: bool


class SelectBlocksRequest(BaseModel):
    blocks: list[str] = Field(..., min_length=1, max_length=2)


class SelectBlocksResponse(BaseModel):
    ok: bool
    selected_blocks: list[str]
