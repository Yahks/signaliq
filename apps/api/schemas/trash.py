"""Trash API schemas.

Three operations on a soft-deleted row: soft delete (set deleted_at),
restore (clear deleted_at), purge (hard delete). Plus a unified list
that reads across trading_posts, generated_posts, and daily_briefs.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TrashActionResult(BaseModel):
    id: str
    kind: str              # "trading" | "news" | "brief"
    action: str            # "deleted" | "restored" | "purged"
    purge_at: datetime | None = None  # only set on soft-delete


class TrashItem(BaseModel):
    id: str
    kind: str
    title: str
    word_count: int | None
    deleted_at: datetime
    purge_at: datetime     # deleted_at + 2 days


class TrashListResponse(BaseModel):
    items: list[TrashItem]
    total: int
