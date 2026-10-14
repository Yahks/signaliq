"""The enrichment pipeline.

Takes a text, calls the fast LLM with the enrich.combined prompt,
parses the JSON, and returns a structured EnrichmentResult.

Adds robust JSON parsing: handles markdown fences, leading text, trailing text.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from packages.ai.prompts.loader import render, render_with_version
from packages.ai.router import complete
from packages.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class EnrichmentResult:
    sentiment_label: str | None = None
    sentiment_score: float | None = None
    sentiment_confidence: float | None = None
    emotions: dict[str, float] = field(default_factory=dict)
    intent: str | None = None
    intent_confidence: float | None = None
    raw_topics: list[str] = field(default_factory=list)
    raw_entities: list[dict[str, Any]] = field(default_factory=list)
    pain_point_signals: list[str] = field(default_factory=list)
    feature_request_signals: list[str] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)
    parse_error: str | None = None


def _strip_json_fences(text: str) -> str:
    """Remove markdown ```json fences if present."""
    text = text.strip()
    # Match ```json ... ``` or ``` ... ```
    m = re.match(r"^```(?:json)?\s*(.+?)\s*```$", text, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _extract_first_json_object(text: str) -> str | None:
    """Find the first balanced { ... } in text. Handles leading/trailing prose."""
    text = _strip_json_fences(text)
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def _safe_float(v: Any, default: float | None = None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_str(v: Any) -> str | None:
    if v is None:
        return None
    return str(v).strip() or None


def parse_response(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Extract a JSON object from the LLM response. Returns (dict, error)."""
    cleaned = _strip_json_fences(text)
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError:
        pass
    extracted = _extract_first_json_object(text)
    if extracted:
        try:
            return json.loads(extracted), None
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON after extraction: {e}"
    return None, "No JSON object found in response"


async def enrich_text(text: str, *, org_id: str | None = None, use_cache: bool = True) -> EnrichmentResult:
    """Run the enrichment pipeline on a single text."""
    prompt, _pv = render_with_version("enrich.combined", text=text)

    try:
        raw = await complete(
            role="fast",
            system=None,
            user=prompt,
            org_id=org_id,
            prompt_version_id=_pv,
            purpose="enrich.combined",
            temperature=0.1,
            max_tokens=2000,
            json_mode=True,
            use_cache=use_cache,
        )
    except Exception as exc:
        log.warning("enrich.call_failed", error=str(exc)[:200])
        return EnrichmentResult(parse_error=f"LLM call failed: {exc}")

    parsed, err = parse_response(raw)
    if parsed is None:
        log.warning("enrich.parse_failed", error=err, snippet=raw[:200])
        return EnrichmentResult(parse_error=err, raw_response={"raw_text": raw[:1000]})

    # Sentiment
    sent = parsed.get("sentiment") or {}
    # Emotions
    emotions_raw = parsed.get("emotions") or {}
    emotions: dict[str, float] = {}
    if isinstance(emotions_raw, dict):
        for k, v in emotions_raw.items():
            f = _safe_float(v)
            if f is not None:
                emotions[str(k)] = max(0.0, min(1.0, f))
    # Intent
    intent_raw = parsed.get("intent") or {}
    # Entities
    entities = parsed.get("entities") or []
    if not isinstance(entities, list):
        entities = []
    clean_entities = []
    for e in entities:
        if isinstance(e, dict) and e.get("text"):
            clean_entities.append({
                "text": str(e["text"])[:255],
                "type": str(e.get("type", "other"))[:32],
                "confidence": _safe_float(e.get("confidence"), 0.5) or 0.5,
            })
    # Topics
    topics = parsed.get("topics") or []
    if not isinstance(topics, list):
        topics = []
    clean_topics = [str(t)[:255] for t in topics if t]

    return EnrichmentResult(
        sentiment_label=_safe_str(sent.get("label")),
        sentiment_score=_safe_float(sent.get("score")),
        sentiment_confidence=_safe_float(sent.get("confidence")),
        emotions=emotions,
        intent=_safe_str(intent_raw.get("label")) if isinstance(intent_raw, dict) else None,
        intent_confidence=_safe_float(intent_raw.get("confidence")) if isinstance(intent_raw, dict) else None,
        raw_topics=clean_topics,
        raw_entities=clean_entities,
        pain_point_signals=[str(p)[:255] for p in (parsed.get("pain_point_signals") or []) if p],
        feature_request_signals=[str(f)[:255] for f in (parsed.get("feature_request_signals") or []) if f],
        raw_response=parsed,
    )
