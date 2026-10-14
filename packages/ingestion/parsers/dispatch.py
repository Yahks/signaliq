"""Pick the right parser based on file extension."""
from __future__ import annotations

from collections.abc import Iterator, Callable
from typing import Any

from packages.ingestion.parsers.base import ParseError, ParsedRecord
from packages.ingestion.parsers.csv_parser import parse_csv
from packages.ingestion.parsers.json_parser import parse_json
from packages.ingestion.parsers.jsonl_parser import parse_jsonl


PARSERS: dict[str, Callable[..., Iterator[ParsedRecord]]] = {
    "csv": parse_csv,
    "json": parse_json,
    "jsonl": parse_jsonl,
}


def detect_format(filename: str) -> str:
    """Return canonical format name from filename extension."""
    name = filename.lower().rsplit(".", 1)
    if len(name) != 2:
        raise ParseError(f"Cannot detect format from filename: {filename}")
    ext = name[1]
    if ext not in PARSERS:
        raise ParseError(f"Unsupported file format: .{ext}. Supported: {', '.join(PARSERS)}")
    return ext


def parse_file(
    raw: bytes,
    *,
    filename: str,
    mapping: dict[str, str] | None = None,
) -> Iterator[ParsedRecord]:
    fmt = detect_format(filename)
    return PARSERS[fmt](raw, mapping=mapping)
