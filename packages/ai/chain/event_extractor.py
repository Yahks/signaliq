"""Convert raw text records into news_events.

Two modes:
1. **Structured mode**: input already has {headline, summary, occurred_at, actors, status}
   — used when uploading curated news event JSON.
2. **Extraction mode**: input is raw text records — used when we have to derive events
   from unstructured posts.

This module provides parsing/validation helpers. Actual DB inserts happen in the task.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dateutil import parser as dateparser

from packages.core.error import ValidationError

VALID_EVENT_TYPES = {
    "statement", "military", "diplomatic", "economic",
    "protest", "report", "legal", "other",
}
VALID_STATUSES = {"fact", "claim", "rumor", "disputed"}
VALID_SOURCE_TYPES = {"primary", "corroborating", "contradicting"}


@dataclass
class SourceInput:
    name: str
    url: str | None = None
    type: str = "primary"
    published_at: datetime | None = None


@dataclass
class EventInput:
    external_id: str | None
    headline: str
    summary: str
    occurred_at: datetime | None
    reported_at: datetime | None
    location: str | None
    actors: list[str]
    event_type: str
    status: str
    confidence: float
    sources: list[SourceInput] = field(default_factory=list)
    quotes: list[dict[str, Any]] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_dt(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    try:
        return dateparser.parse(str(v))
    except (ValueError, TypeError):
        return None


def validate_and_parse_event(raw: dict[str, Any], index: int) -> EventInput:
    """Validate one event dict from a News Event JSON upload."""
    if not isinstance(raw, dict):
        raise ValidationError(f"Event {index}: must be an object")

    headline = str(raw.get("headline", "")).strip()
    if not headline or len(headline) > 500:
        raise ValidationError(f"Event {index}: headline required (1-500 chars)")

    summary = str(raw.get("summary", "")).strip()
    if not summary or len(summary) > 4000:
        raise ValidationError(f"Event {index}: summary required (1-4000 chars)")

    actors = raw.get("actors") or []
    if not isinstance(actors, list) or len(actors) < 1:
        raise ValidationError(f"Event {index}: actors must be a non-empty array")
    actors = [str(a).strip() for a in actors if str(a).strip()]

    event_type = str(raw.get("event_type", "other")).strip().lower()
    if event_type not in VALID_EVENT_TYPES:
        event_type = "other"

    status = str(raw.get("status", "raw")).strip().lower()
    if status not in VALID_STATUSES:
        status = "raw"

    confidence = raw.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else 0.5
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    sources: list[SourceInput] = []
    for s in raw.get("sources") or []:
        if isinstance(s, dict) and s.get("name"):
            stype = str(s.get("type", "primary")).lower()
            if stype not in VALID_SOURCE_TYPES:
                stype = "primary"
            sources.append(SourceInput(
                name=str(s["name"])[:255],
                url=str(s["url"])[:1000] if s.get("url") else None,
                type=stype,
                published_at=_parse_dt(s.get("published_at")),
            ))

    return EventInput(
        external_id=str(raw["external_id"])[:255] if raw.get("external_id") else None,
        headline=headline,
        summary=summary,
        occurred_at=_parse_dt(raw.get("occurred_at")),
        reported_at=_parse_dt(raw.get("reported_at")),
        location=str(raw["location"])[:255] if raw.get("location") else None,
        actors=actors,
        event_type=event_type,
        status=status,
        confidence=confidence,
        sources=sources,
        quotes=list(raw.get("quotes") or []),
        topics=[str(t) for t in (raw.get("topics") or [])],
        metadata=dict(raw.get("metadata") or {}),
    )


def parse_news_event_json(payload: dict[str, Any]) -> tuple[list[EventInput], dict[str, Any]]:
    """Parse a full News Event JSON upload.

    Returns (events, chain_hint). chain_hint is {} if not provided.
    """
    if not isinstance(payload, dict) or "events" not in payload:
        raise ValidationError("Payload must be an object with an 'events' array")

    events_raw = payload["events"]
    if not isinstance(events_raw, list):
        raise ValidationError("'events' must be an array")
    if len(events_raw) < 2:
        raise ValidationError("At least 2 events are required")
    if len(events_raw) > 50:
        raise ValidationError("Maximum 50 events per upload")

    events = [validate_and_parse_event(e, i) for i, e in enumerate(events_raw)]
    chain_hint = payload.get("chain_hint") or {}
    return events, chain_hint
