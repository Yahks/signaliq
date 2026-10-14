"""JSONL parser. One JSON object per line."""
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


def parse_jsonl(
    raw: bytes,
    *,
    mapping: dict[str, str] | None = None,
) -> Iterator[ParsedRecord]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ParseError(f"Cannot decode JSONL: {exc}") from exc

    mapping = mapping or {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # skip malformed line
        if not isinstance(row, dict):
            continue
        try:
            yield _row_to_record(row, mapping, lineno)
        except RecordError:
            continue


def _row_to_record(row: dict[str, Any], mapping: dict[str, str], lineno: int) -> ParsedRecord:
    mapped = apply_mapping(row, mapping)

    text = mapped.get("text")
    if not text or not isinstance(text, str) or not text.strip():
        raise RecordError(f"line {lineno}: missing text")

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
