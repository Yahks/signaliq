"""Credential API schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CredentialResponse(BaseModel):
    id: str
    provider: str
    model_name: str
    roles: list[str]
    label: str
    key_preview: str
    is_active: bool
    priority: int
    exhausted_until: datetime | None = None
    last_success_at: datetime | None = None
    created_at: datetime
    # S.6.4.3 — per-key Groq token visibility
    tokens_used: int = 0
    calls_made: int = 0
    last_remaining_tokens: int | None = None
    last_rate_at: datetime | None = None


class CredentialListResponse(BaseModel):
    items: list[CredentialResponse]


class CreateCredentialRequest(BaseModel):
    provider: str = Field(..., min_length=2, max_length=32)
    model_name: str = Field(..., min_length=2, max_length=128)
    role: str = Field(..., min_length=4, max_length=16)
    label: str = Field(..., min_length=2, max_length=64)
    api_key: str = Field(..., min_length=8, max_length=512)
    priority: int = Field(default=100, ge=1, le=1000)


class ToggleCredentialRequest(BaseModel):
    is_active: bool


class CreateCredentialSimpleRequest(BaseModel):
    """The user-facing form: pick a provider, paste a key.

    An optional label lets the user name the key meaningfully when they
    have several for the same provider. Falls back to
    `{provider}-{key_hash4}` when not supplied.
    """
    provider: str = Field(..., min_length=2, max_length=32)
    api_key: str = Field(..., min_length=8, max_length=512)
    label: str | None = Field(default=None, min_length=2, max_length=64)


class SupportRequest(BaseModel):
    message: str = Field(..., min_length=10, max_length=2000)
