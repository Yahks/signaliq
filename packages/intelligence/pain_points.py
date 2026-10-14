"""Aggregate pain_point_signals from enrichments into ranked pain_points.

Algorithm:
  1. Collect all (record_id, signal_text, sentiment_score) tuples from enrichments
  2. Normalize signal text (lowercase, trim)
  3. Group by signal → count records + avg sentiment + distinct sources
  4. Compute severity = frequency * |negative_sentiment| * source_diversity
  5. Upsert into pain_points table
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass
class PainPointAggregate:
    title: str
    frequency: int
    severity: float
    avg_sentiment: float | None
    first_seen_at: object
    last_seen_at: object


def aggregate_signals(
    rows: list[tuple[str, list[str], float | None, object]],
) -> list[PainPointAggregate]:
    """rows: list of (record_id, signals, sentiment_score, created_at)."""
    groups: dict[str, dict] = defaultdict(lambda: {
        "records": set(),
        "sentiments": [],
        "first": None,
        "last": None,
    })

    for record_id, signals, sentiment, created_at in rows:
        if not signals:
            continue
        for raw in signals:
            if not raw:
                continue
            key = str(raw).strip().lower()
            if len(key) < 3 or len(key) > 255:
                continue
            g = groups[key]
            g["records"].add(record_id)
            if sentiment is not None:
                g["sentiments"].append(sentiment)
            if created_at is not None:
                if g["first"] is None or created_at < g["first"]:
                    g["first"] = created_at
                if g["last"] is None or created_at > g["last"]:
                    g["last"] = created_at

    results: list[PainPointAggregate] = []
    for title, g in groups.items():
        freq = len(g["records"])
        if freq < 2:  # ignore one-offs
            continue
        sentiments = g["sentiments"]
        avg_sent = sum(sentiments) / len(sentiments) if sentiments else None
        # Severity: more frequent + more negative = worse
        negativity = max(0.0, -(avg_sent or 0.0))
        severity = min(1.0, (freq / 20.0) * 0.5 + negativity * 0.5)
        results.append(PainPointAggregate(
            title=title,
            frequency=freq,
            severity=round(severity, 3),
            avg_sentiment=round(avg_sent, 3) if avg_sent is not None else None,
            first_seen_at=g["first"],
            last_seen_at=g["last"],
        ))

    results.sort(key=lambda x: x.severity, reverse=True)
    return results
