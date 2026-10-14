"""Notification and follow schemas."""
from datetime import datetime

from pydantic import BaseModel


class FollowStatusResponse(BaseModel):
    chain_id: str
    following: bool


class NotificationResponse(BaseModel):
    id: str
    kind: str
    chain_id: str | None
    event_id: str | None
    title: str
    body: str | None
    read_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationsListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    unread: int
