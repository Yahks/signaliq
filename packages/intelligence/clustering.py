"""Semantic clustering over embeddings.

Uses HDBSCAN on cosine distance. Records not fitting any cluster become "noise"
(label -1) and are excluded — this is intentional (better to have no cluster
than a garbage cluster).
"""
from __future__ import annotations

import numpy as np
from sklearn.cluster import HDBSCAN


def cluster_vectors(
    vectors: list[list[float]],
    *,
    min_cluster_size: int = 3,
    min_samples: int = 2,
) -> list[int]:
    """Cluster N vectors. Returns list of cluster labels (-1 = noise).

    Uses cosine distance directly via sklearn's HDBSCAN metric='cosine'.
    """
    if len(vectors) < min_cluster_size:
        return [-1] * len(vectors)

    X = np.asarray(vectors, dtype=np.float64)

    # Normalize to unit length so cosine distance == euclidean on unit sphere
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    X = X / norms

    model = HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="euclidean",  # equivalent to cosine on unit-normalized vectors
        cluster_selection_method="eom",
    )
    labels = model.fit_predict(X)
    return [int(label) for label in labels]
