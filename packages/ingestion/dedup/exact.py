"""Exact duplicate detection via SHA-256 of normalized text."""
from __future__ import annotations

import hashlib


def content_hash(text: str) -> str:
    """SHA-256 hex digest of text.

    Text is lowercased and whitespace-collapsed before hashing so that
    trivial differences (trailing spaces, different casing) hash equally.
    """
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
