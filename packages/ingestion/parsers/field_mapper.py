"""Maps user-supplied column names to SignalIQ's canonical fields.

Three modes (in order of precedence):
1. Explicit mapping: {"tweet_text": "text", "user": "author_identifier"}
2. Common aliases (built in): "author"/"user"/"username"/"handle" → author_identifier, etc.
3. Canonical names: if the column is already named text, source_record_id, etc.

Anything unmapped goes to metadata.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


CANONICAL_FIELDS = {
    "text",
    "source_record_id",
    "author_identifier",
    "original_timestamp",
    "language",
}


# Common column names → canonical field names. Case-insensitive.
ALIASES: dict[str, str] = {
    # text
    "text": "text",
    "content": "text",
    "body": "text",
    "message": "text",
    "post": "text",
    "tweet": "text",
    "tweet_text": "text",
    "comment": "text",
    "review": "text",
    # source_record_id
    "id": "source_record_id",
    "record_id": "source_record_id",
    "post_id": "source_record_id",
    "tweet_id": "source_record_id",
    "message_id": "source_record_id",
    # author_identifier
    "author": "author_identifier",
    "author_id": "author_identifier",
    "user": "author_identifier",
    "user_id": "author_identifier",
    "username": "author_identifier",
    "handle": "author_identifier",
    "screen_name": "author_identifier",
    # original_timestamp
    "timestamp": "original_timestamp",
    "date": "original_timestamp",
    "created": "original_timestamp",
    "created_at": "original_timestamp",
    "posted_at": "original_timestamp",
    "datetime": "original_timestamp",
    "time": "original_timestamp",
    # language
    "language": "language",
    "lang": "language",
    "locale": "language",
}


def apply_mapping(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Apply user mapping + alias fallback + canonical passthrough."""
    out: dict[str, Any] = {"metadata": {}}
    claimed: set[str] = set()

    # 1. Explicit mapping wins
    for source_col, target in mapping.items():
        if source_col is None:
            continue
        if source_col in row and target in CANONICAL_FIELDS:
            out[target] = row[source_col]
            claimed.add(source_col)

    # 2 & 3. Aliases / canonical names for unclaimed columns
    for col, value in row.items():
        if col is None or col in claimed:
            continue
        if col in CANONICAL_FIELDS:
            out[col] = value
        elif isinstance(col, str) and col.lower() in ALIASES:
            out[ALIASES[col.lower()]] = value
        else:
            out["metadata"][col] = value

    return out


def coerce_timestamp(value: Any) -> datetime | None:
    """Best-effort timestamp coercion. Returns None if unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e11:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=UTC)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None
