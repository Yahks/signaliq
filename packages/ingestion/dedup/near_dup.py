"""Near-duplicate detection using MinHash.

Two texts are near-duplicates if their Jaccard similarity over token
shingles exceeds a threshold (default 0.85).
"""
from __future__ import annotations

from datasketch import MinHash


def _minhash(text: str, num_perm: int = 128) -> MinHash:
    m = MinHash(num_perm=num_perm)
    tokens = text.lower().split()
    # 3-gram shingles
    for i in range(max(1, len(tokens) - 2)):
        shingle = " ".join(tokens[i:i + 3])
        m.update(shingle.encode("utf-8"))
    return m


def jaccard(a: str, b: str, *, num_perm: int = 128) -> float:
    """Approximate Jaccard similarity between two texts."""
    ma = _minhash(a, num_perm)
    mb = _minhash(b, num_perm)
    return ma.jaccard(mb)


def is_near_duplicate(a: str, b: str, *, threshold: float = 0.85) -> bool:
    return jaccard(a, b) >= threshold
