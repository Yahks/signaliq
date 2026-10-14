"""Settings API schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BlockSettingsItem(BaseModel):
    slug: str
    name: str
    auto_enabled: bool
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class BlockSettingsListResponse(BaseModel):
    items: list[BlockSettingsItem]


class UpdateBlockSettingsRequest(BaseModel):
    auto_enabled: bool


class EditorialUpdateRequest(BaseModel):
    """Request body for PUT /api/settings/editorial."""
    voice: str
