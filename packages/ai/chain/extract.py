"""Extract events from arbitrary user input.

The user pastes anything — an article, a tweet, a paragraph, some JSON —
and we produce a list of EventInput ready for the chain pipeline.

Three paths, in order:
  1. Valid JSON matching our schema   → use directly, no LLM
  2. Valid JSON of another shape      → pass to the LLM as-is
  3. Not JSON                         → LLM extraction

The LLM path uses chain.extract@1 (role=reasoning) and is charged to the
user's BYOK key. The upload itself is charged separately by charge_for_upload.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.chain.event_extractor import (
    EventInput,
    validate_and_parse_event,
)
from packages.ai.prompts.loader import render_with_version
from packages.ai.router import complete
from packages.core.logging import get_logger
from packages.db.models.raw_record import RawRecord
from packages.db.models.source import Source
from packages.ingestion.dedup.exact import content_hash

log = get_logger(__name__)

MANUAL_PASTE_SOURCE_NAME = "manual_paste"


# ─────────────────────────────────────────────────────────────────────
# Source + persistence
# ─────────────────────────────────────────────────────────────────────

async def ensure_manual_paste_source(session: AsyncSession, org_id: str) -> str:
    """Idempotently create the per-org 'manual_paste' source. Returns its id."""
    # Look for an existing one (name is not unique-constrained; we match by
    # (org_id, name) and pick the oldest if multiple exist).
    row = (await session.execute(
        select(Source.id)
        .where(Source.org_id == org_id)
        .where(Source.name == MANUAL_PASTE_SOURCE_NAME)
        .where(Source.deleted_at.is_(None))
        .order_by(Source.created_at.asc())
        .limit(1)
    )).first()
    if row:
        return str(row[0])

    src = Source(
        org_id=org_id,
        name=MANUAL_PASTE_SOURCE_NAME,
        source_type="manual",
        description="Content pasted directly by a user on /chains/new.",
        collection_method="user_paste",
        retention_policy=None,
        config={},
    )
    session.add(src)
    await session.flush()
    await session.refresh(src)
    log.info("extract.source_created", org_id=org_id[:8], source_id=str(src.id)[:8])
    return str(src.id)


async def persist_paste(
    session: AsyncSession,
    org_id: str,
    source_id: str,
    raw_text: str,
    *,
    created_by: str | None = None,
) -> str:
    """Insert a raw_records row for the paste. Returns the new id."""
    rr = RawRecord(
        org_id=org_id,
        source_id=source_id,
        ingestion_job_id=None,
        source_record_id=None,
        author_identifier=created_by,
        original_timestamp=None,
        text=raw_text,
        language=None,
        metadata_={"kind": "chain_paste"},
        content_hash=content_hash(raw_text),
    )
    session.add(rr)
    await session.flush()
    await session.refresh(rr)
    return str(rr.id)


# ─────────────────────────────────────────────────────────────────────
# JSON-first, then LLM extraction
# ─────────────────────────────────────────────────────────────────────

_EXTRACT_PROMPT_NAME = "chain.extract"


async def extract_events_from_any_input(
    session: AsyncSession,
    org_id: str,
    raw_text: str,
    *,
    use_cache: bool = False,
) -> list[EventInput]:
    """Take arbitrary text and return EventInput objects ready for the pipeline."""
    stripped = raw_text.strip()
    if not stripped:
        raise ValueError("Empty input")

    # Path 1 & 2: try JSON
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, dict) and "events" in parsed:
            events, _ = _parse_events_payload(parsed)
            if events:
                log.info("extract.json_events", count=len(events))
                return events

        if isinstance(parsed, dict) and "posts" in parsed:
            events = _convert_posts(parsed["posts"])
            if events:
                log.info("extract.json_posts", count=len(events))
                return events

        # Other JSON shapes → fall through to LLM with the raw JSON string

    # Path 3: LLM extraction
    return await _extract_via_llm(session, org_id, stripped, use_cache=use_cache)


def _parse_events_payload(payload: dict[str, Any]) -> tuple[list[EventInput], dict]:
    """Thin wrapper so callers can import just extract.py."""
    from packages.ai.chain.event_extractor import parse_news_event_json
    return parse_news_event_json(payload)


def _convert_posts(posts: list[Any]) -> list[EventInput]:
    """Convert a list of Facebook-style records to EventInput objects."""
    out: list[EventInput] = []
    for i, p in enumerate(posts):
        if not isinstance(p, dict):
            continue
        raw_text = str(
            p.get("text") or p.get("content") or p.get("body") or p.get("message") or ""
        ).strip()
        if not raw_text:
            continue
        first_sentence = raw_text.split(".")[0][:400] or raw_text[:120]
        author = p.get("author") or p.get("author_name") or p.get("page_name")
        occurred_at_raw = p.get("created_at") or p.get("posted_at") or p.get("date")
        try:
            out.append(validate_and_parse_event({
                "external_id": p.get("post_id") or p.get("id") or f"post_{i}",
                "headline": first_sentence,
                "summary": raw_text[:2000],
                "occurred_at": occurred_at_raw,
                "actors": [str(author)] if author else ["unknown"],
                "event_type": "report",
                "status": "fact",
                "confidence": 0.7,
            }, i))
        except Exception as exc:
            log.warning("extract.post_skip", i=i, error=str(exc)[:100])
    return out


async def _extract_via_llm(
    session: AsyncSession,
    org_id: str,
    text: str,
    *,
    use_cache: bool = False,
) -> list[EventInput]:
    """Call the LLM with chain.extract and parse the response."""
    # Truncate extremely long pastes — the LLM has a token budget.
    max_chars = 20_000
    if len(text) > max_chars:
        text = text[:max_chars]
        log.info("extract.truncated", max_chars=max_chars)

    prompt, pv = render_with_version(
        _EXTRACT_PROMPT_NAME,
        text=text,
    )

    raw = await complete(
        role="reasoning",
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=pv,
        purpose="chain.extract",
        temperature=0.2,
        max_tokens=4000,
        json_mode=True,
        use_cache=use_cache,
    )

    # The model returns a JSON array of event objects.
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("extract.parse_failed", snippet=raw[:300])
        raise ValueError("Extraction produced invalid JSON")

    if isinstance(parsed, dict) and "events" in parsed:
        parsed = parsed["events"]
    if not isinstance(parsed, list):
        raise ValueError(f"Extraction returned {type(parsed).__name__}, expected array")

    events: list[EventInput] = []
    for i, raw_ev in enumerate(parsed):
        try:
            events.append(validate_and_parse_event(raw_ev, i))
        except Exception as exc:
            log.warning("extract.event_skip", i=i, error=str(exc)[:120])

    if not events:
        raise ValueError("Extraction produced no valid events")

    # S.6.3.1 — merge events that describe the same fact.
    events = _merge_duplicates(events)

    if not events:
        raise ValueError("Extraction produced no valid events after dedupe")
    log.info("extract.llm_done", count=len(events))
    return events

# ─────────────────────────────────────────────────────────────────────
# S.6.3.1 — dedupe near-identical events
# ─────────────────────────────────────────────────────────────────────

def _jaccard(a: str, b: str) -> float:
    """Word-set Jaccard similarity, lowercased. Cheap proxy for
    'are these two describing the same thing?' — good enough to catch
    the LLM restating one fact as two events."""
    import re as _re
    wa = set(_re.findall(r"[a-z0-9]+", (a or "").lower()))
    wb = set(_re.findall(r"[a-z0-9]+", (b or "").lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _merge_duplicates(events: list[EventInput]) -> list[EventInput]:
    """Merge events that describe the same fact.

    Two events are considered duplicates when:
      - their actor sets match (case-insensitive, order-insensitive), AND
      - their event_type matches, AND
      - Jaccard similarity of (headline + summary) word sets >= 0.6

    When a duplicate pair is found, we keep the one with the longer
    summary (more detail). The dropped one is logged.
    """
    if len(events) < 2:
        return events

    kept: list[EventInput] = []
    for ev in events:
        merged = False
        for i, existing in enumerate(kept):
            same_actors = (
                sorted(a.strip().lower() for a in (ev.actors or []))
                == sorted(a.strip().lower() for a in (existing.actors or []))
            )
            same_type = (ev.event_type or "").lower() == (existing.event_type or "").lower()
            if not (same_actors and same_type):
                continue
            sim = _jaccard(
                f"{ev.headline} {ev.summary}",
                f"{existing.headline} {existing.summary}",
            )
            if sim < 0.6:
                continue
            # Duplicate. Keep the one with the longer summary.
            if len(ev.summary or "") > len(existing.summary or ""):
                log.info(
                    "extract.deduped",
                    kept=ev.headline[:60],
                    dropped=existing.headline[:60],
                    similarity=round(sim, 2),
                )
                kept[i] = ev
            else:
                log.info(
                    "extract.deduped",
                    kept=existing.headline[:60],
                    dropped=ev.headline[:60],
                    similarity=round(sim, 2),
                )
            merged = True
            break
        if not merged:
            kept.append(ev)

    if len(kept) != len(events):
        log.info("extract.dedupe_summary", before=len(events), after=len(kept))
    return kept
