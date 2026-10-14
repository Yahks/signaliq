"""CSV parser. Streams rows so large files don't blow memory."""
from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from typing import Any

from packages.ingestion.parsers.base import (
    ParseError,
    ParsedRecord,
    RecordError,
)
from packages.ingestion.parsers.field_mapper import apply_mapping, coerce_timestamp


def _detect_encoding(raw: bytes) -> str:
    """Best-effort encoding detection. Falls back to utf-8."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8"


def parse_csv(
    raw: bytes,
    *,
    mapping: dict[str, str] | None = None,
) -> Iterator[ParsedRecord]:
    """Parse CSV bytes into ParsedRecord items.

    Args:
        raw: file contents.
        mapping: column name → canonical field. If None, expects the file to
                 already use canonical names.

    Yields:
        ParsedRecord for each valid row.

    Raises:
        ParseError: if the file can't be read as CSV at all.
    """
    encoding = _detect_encoding(raw)
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ParseError(f"Cannot decode CSV: {exc}") from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ParseError("CSV has no header row")

    mapping = mapping or {}

    for lineno, row in enumerate(reader, start=2):
        try:
            yield _row_to_record(row, mapping, lineno)
        except RecordError:
            # Bad row — skip silently. Caller counts failures separately.
            continue


def _row_to_record(
    row: dict[str, Any],
    mapping: dict[str, str],
    lineno: int,
) -> ParsedRecord:
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
