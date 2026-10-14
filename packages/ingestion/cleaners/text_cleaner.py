"""Normalize text without destroying meaning.

Extracts URLs, hashtags, mentions into separate lists.
Lowercases nothing — case matters for entities.
Collapses whitespace. Strips control chars.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)
MENTION_RE = re.compile(r"@(\w+)", re.UNICODE)
WHITESPACE_RE = re.compile(r"\s+")
CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


@dataclass
class CleanedText:
    text: str
    urls: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    tokens_count: int = 0


def clean(text: str) -> CleanedText:
    """Clean a raw text blob. Never returns empty text — raises if empty."""
    if text is None:
        raise ValueError("text is None")

    # Strip control chars first
    cleaned = CONTROL_RE.sub("", text)

    # Extract before removing
    urls = URL_RE.findall(cleaned)
    hashtags = [h for h in HASHTAG_RE.findall(cleaned)]
    mentions = [m for m in MENTION_RE.findall(cleaned)]

    # Remove URLs from the text (they're kept separately)
    cleaned = URL_RE.sub(" ", cleaned)
    # Collapse whitespace
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip()

    if not cleaned:
        raise ValueError("text is empty after cleaning")

    return CleanedText(
        text=cleaned,
        urls=urls,
        hashtags=hashtags,
        mentions=mentions,
        tokens_count=len(cleaned.split()),
    )
