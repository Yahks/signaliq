"""Base types and interface for parsers."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ParsedRecord:
    """One record parsed from an uploaded file.

    `text` is required. All other fields are optional.
    """
    text: str
    source_record_id: str | None = None
    author_identifier: str | None = None
    original_timestamp: datetime | None = None
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ParseError(Exception):
    """Raised when a file cannot be parsed at all (bad encoding, bad structure)."""


class RecordError(Exception):
    """Raised when one row is malformed. The parser skips the row and records the error."""
