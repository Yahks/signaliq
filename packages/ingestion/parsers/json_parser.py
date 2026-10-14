"""JSON parser.

Accepts several top-level shapes:
- Array of objects: [{...}, {...}]
- Wrapped array:    {"records": [...]} or {"posts": [...]} or {"items": [...]} or {"data": [...]}
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from packages.ingestion.parsers.base import (
    ParseError,
    ParsedRecord,
    RecordError,
)
from packages.ingestion.parsers.field_mapper import apply_mapping, coerce_timestamp

# Top-level object keys we recognize as "the array of records"
WRAPPER_KEYS = ("records", "posts", "items", "data", "results")


def parse_json(
    raw: bytes,
    *,
    mapping: dict[str, str] | None = None,
) -> Iterator[ParsedRecord]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(f"Invalid JSON: {exc}") from exc

    rows = _extract_rows(data)

    mapping = mapping or {}
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        try:
            yield _row_to_record(row, mapping, idx)
        except RecordError:
            continue


def _extract_rows(data: Any) -> list[Any]:
    """Find the array of record objects inside whatever top-level shape we got."""
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        # Try well-known wrapper keys first
        for key in WRAPPER_KEYS:
            if key in data and isinstance(data[key], list):
                return data[key]

        # Fallback: find the FIRST list-valued key and use it
        for key, value in data.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value

        # Single record: object with a `text` field
        if "text" in data:
            return [data]

    raise ParseError(
        "JSON must be an array of objects, or an object with one of: "
        + ", ".join(f"'{k}'" for k in WRAPPER_KEYS)
        + " (an array of objects), or a single object with a 'text' field."
    )


def _row_to_record(row: dict[str, Any], mapping: dict[str, str], idx: int) -> ParsedRecord:
    mapped = apply_mapping(row, mapping)

    text = mapped.get("text")
    if not text or not isinstance(text, str) or not text.strip():
        raise RecordError(f"index {idx}: missing text")

    return ParsedRecord(
        text=text.strip(),
        source_record_id=_as_str(mapped.get("source_record_id")),
        author_identifier=_as_str(mapped.get("author_identifier")),
        original_timestamp=coerce_timestamp(mapped.get("original_timestamp")),
        language=_as_str(mapped.get("language")),
        metadata=mapped.get("metadata") or {},
    )


def _as_str(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)
