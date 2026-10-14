"""Convert fetched SourceRows into structured news events for the trading block.

Uses the fast LLM to distill each row into:
  - headline (concise, action-oriented)
  - summary (2-4 sentences)
  - actors (companies, people, tickers)
  - event_type (earnings, analyst_call, regulatory, price_move, etc.)
  - status (fact | forecast | opinion)
  - confidence

Falls back to the raw title/summary if the LLM call fails, so the pipeline
never loses data.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from packages.ai.prompts.loader import render, render_with_version
from packages.ai.router import complete
from packages.core.logging import get_logger
from packages.ingestion.sources.stock_news import SourceRow

log = get_logger(__name__)


def _parse_iso(v: str | None) -> str | None:
    if not v:
        return None
    try:
        # Try direct parse
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()
    except (ValueError, TypeError):
        # Try email.utils (RSS dates like "Mon, 16 Sep 2026 10:00:00 GMT")
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(v)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.isoformat()
        except Exception:
            return None


PROMPT_NAME = "extract.stock_event.v1"


async def row_to_event(row: SourceRow, *, org_id: str) -> dict:
    """Convert one SourceRow into a news-event shaped dict.

    Always returns a valid event — if LLM fails, falls back to a basic
    shape from the raw row. Never raises.
    """
    # Sanitize fetched content BEFORE it hits the prompt
    from packages.security.prompt_guard import sanitize
    clean_title = sanitize(row.title, field_name="stock_title", org_id=org_id).text
    clean_summary = sanitize(
        (row.summary or "")[:2000], field_name="stock_summary", org_id=org_id
    ).text
    clean_content = sanitize(
        (row.full_content or "")[:4000], field_name="stock_content", org_id=org_id
    ).text

    # Build the prompt
    prompt, _pv = render_with_version(
        PROMPT_NAME,
        source_name=row.source_name,
        source_url=row.source_url or "(none)",
        ticker=row.ticker or "(not specified)",
        published_at=row.published_at or "(unknown)",
        title=clean_title,
        summary=clean_summary or "(no summary)",
        content=clean_content or "(not fetched)",
        matched_keywords=", ".join(row.matched_keywords) if row.matched_keywords else "(none)",
    )

    try:
        raw = await complete(
            role="fast",
            prompt_version_id=_pv,
            system=None,
            user=prompt,
            org_id=org_id,
            purpose="extract.stock_event",
            temperature=0.1,
            max_tokens=800,
            json_mode=True,
            use_cache=True,  # cache by content hash — same row → same event
        )
        parsed = json.loads(raw)
    except Exception as exc:
        log.warning("stock_extract.llm_failed", title=row.title[:60], error=str(exc)[:200])
        parsed = None

    if not parsed or not isinstance(parsed, dict):
        # Fallback: use raw row fields
        return {
            "external_id": f"stock_{hash(row.dedup_key()) & 0xFFFFFFFF:x}",
            "headline": row.title[:500],
            "summary": (row.summary or row.title)[:2000],
            "occurred_at": _parse_iso(row.published_at),
            "reported_at": _parse_iso(row.published_at),
            "location": None,
            "actors": [row.ticker] if row.ticker else [],
            "event_type": "report",
            "status": "fact",
            "confidence": 0.6,
            "sources": [{
                "name": row.source_name,
                "url": row.source_url,
                "type": "primary",
            }],
            "metadata": {
                "ticker": row.ticker,
                "catalyst_flag": row.catalyst_flag,
                "matched_keywords": row.matched_keywords,
            },
        }

    # LLM-produced event — validate + normalize
    headline = str(parsed.get("headline", row.title))[:500]
    summary = str(parsed.get("summary", row.summary or row.title))[:2000]

    actors = parsed.get("actors") or []
    if not isinstance(actors, list):
        actors = []
    actors = [str(a)[:64] for a in actors][:10]
    if row.ticker and row.ticker not in actors:
        actors.insert(0, row.ticker)

    event_type = str(parsed.get("event_type", "report")).lower().strip()
    # Sanity map to a small vocabulary
    if event_type not in {
        "price_move", "earnings", "macro_release", "regulatory",
        "analyst_call", "sentiment_shift", "teaching_post", "geopolitical", "report",
    }:
        event_type = "report"

    status = str(parsed.get("status", "fact")).lower().strip()
    if status not in {"fact", "forecast", "rumor", "opinion"}:
        status = "fact"

    try:
        confidence = float(parsed.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7
    confidence = max(0.0, min(1.0, confidence))

    return {
        "external_id": f"stock_{hash(row.dedup_key()) & 0xFFFFFFFF:x}",
        "headline": headline,
        "summary": summary,
        "occurred_at": _parse_iso(row.published_at),
        "reported_at": _parse_iso(row.published_at),
        "location": parsed.get("location"),
        "actors": actors,
        "event_type": event_type,
        "status": status,
        "confidence": confidence,
        "sources": [{
            "name": row.source_name,
            "url": row.source_url,
            "type": "primary",
        }],
        "metadata": {
            "ticker": row.ticker,
            "catalyst_flag": row.catalyst_flag,
            "matched_keywords": row.matched_keywords,
            "origin": "stock_news_fetcher",
        },
    }


async def rows_to_events(rows: list[SourceRow], *, org_id: str) -> list[dict]:
    """Convert many rows in parallel (bounded concurrency)."""
    import asyncio

    sem = asyncio.Semaphore(4)  # 4 concurrent LLM calls

    async def one(r: SourceRow) -> dict:
        async with sem:
            return await row_to_event(r, org_id=org_id)

    return await asyncio.gather(*(one(r) for r in rows))
