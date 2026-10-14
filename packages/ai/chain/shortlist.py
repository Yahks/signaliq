"""Three-tier event shortlist selector.

Scales to tens of thousands of events per block by reducing what
we send to the LLM. The LLM only ever sees MAX_EVENTS (25) events.

Tiers:
    N <= 30:      send all (no reduction)
    30 < N <= 500: greedy diverse sampling → MAX_EVENTS
    N > 500:      cluster + sample → MAX_EVENTS

All sampling preserves recency and diversity.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

MAX_EVENTS = 25


@dataclass
class EventWithEmbedding:
    id: str
    occurred_at_iso: str | None
    embedding: list[float] | None
    raw: dict  # full event dict for the LLM


def _cosine_distance(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    na = np.linalg.norm(va) or 1.0
    nb = np.linalg.norm(vb) or 1.0
    sim = float(np.dot(va, vb) / (na * nb))
    return 1.0 - sim


def _greedy_diverse_sample(events: list[EventWithEmbedding], k: int) -> list[EventWithEmbedding]:
    """Farthest-point sampling to maximize diversity."""
    if len(events) <= k:
        return list(events)

    # Sort by recency — recent first
    events = sorted(events, key=lambda e: e.occurred_at_iso or "", reverse=True)

    # Seed: take the most recent event
    picked = [events[0]]
    pool = events[1:]

    while len(picked) < k and pool:
        # Find the event with max distance to any picked event
        best_idx = 0
        best_score = -1.0
        for i, e in enumerate(pool):
            if e.embedding is None:
                # No embedding → score by recency only
                score = 0.5
            else:
                # Min distance to any picked event
                min_d = min(
                    (_cosine_distance(e.embedding, p.embedding)
                     for p in picked if p.embedding is not None),
                    default=1.0,
                )
                score = min_d
            if score > best_score:
                best_score = score
                best_idx = i
        picked.append(pool.pop(best_idx))

    return picked


def _cluster_sample(events: list[EventWithEmbedding], k: int) -> list[EventWithEmbedding]:
    """Cluster + sample. For N > 500."""
    from sklearn.cluster import HDBSCAN

    embedded = [e for e in events if e.embedding is not None]
    no_embedding = [e for e in events if e.embedding is None]

    if len(embedded) < 10:
        # Not enough embeddings to cluster — fall back to diverse sample
        return _greedy_diverse_sample(events, k)

    X = np.asarray([e.embedding for e in embedded], dtype=np.float32)
    # Normalize for cosine
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    X = X / norms

    model = HDBSCAN(min_cluster_size=3, min_samples=2, metric="euclidean")
    labels = model.fit_predict(X)

    # Group by cluster
    clusters: dict[int, list[EventWithEmbedding]] = {}
    noise: list[EventWithEmbedding] = []
    for i, label in enumerate(labels):
        if label == -1:
            noise.append(embedded[i])
        else:
            clusters.setdefault(int(label), []).append(embedded[i])

    if not clusters:
        # No clusters found — fall back
        return _greedy_diverse_sample(events, k)

    # Sort clusters by size (biggest first)
    sorted_clusters = sorted(clusters.values(), key=len, reverse=True)

    # Distribute k picks across top clusters
    per_cluster = max(2, k // min(5, len(sorted_clusters)))
    picked: list[EventWithEmbedding] = []

    for cluster_events in sorted_clusters[:5]:
        # Take up to per_cluster from this cluster, prioritizing recency
        cluster_events = sorted(cluster_events, key=lambda e: e.occurred_at_iso or "", reverse=True)
        picked.extend(cluster_events[:per_cluster])
        if len(picked) >= k:
            break

    # Fill remaining slots from noise (recent first)
    if len(picked) < k:
        noise.sort(key=lambda e: e.occurred_at_iso or "", reverse=True)
        picked.extend(noise[: k - len(picked)])

    return picked[:k]


def select_shortlist(events: list[EventWithEmbedding]) -> list[EventWithEmbedding]:
    """Apply the right tier based on event count.

    - N <= 30:      all events
    - 30 < N <= 500: greedy diverse sample
    - N > 500:      cluster + sample
    """
    n = len(events)
    if n <= 30:
        return events
    if n <= 500:
        return _greedy_diverse_sample(events, MAX_EVENTS)
    return _cluster_sample(events, MAX_EVENTS)
