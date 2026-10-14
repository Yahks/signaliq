"""Generate a daily brief from the last 24 hours of org activity.

Gathers:
  - News events from the last 24h (with chain membership)
  - Chain updates from the last 24h
  - Live quotes for watchlist symbols across the org
  - Impacts and scenarios from chains updated in the window

Feeds a structured JSON to the LLM (daily_brief.trading prompt).
Returns a markdown brief + provenance (which events and chains were used).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.prompts.loader import render, render_with_version
from packages.ai.router import complete
from packages.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class DailyBriefResult:
    title: str
    body: str
    word_count: int
    event_ids: list[str] = field(default_factory=list)
    chain_ids: list[str] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)


async def gather_brief_data(
    session: AsyncSession,
    *,
    org_id: str,
    window_hours: int = 24,
) -> dict:
    """Collect everything the brief needs from the last N hours."""
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)

    events = (await session.execute(sql_text("""
        SELECT e.id::text, e.headline, e.summary, e.actors, e.event_type,
               e.status, e.confidence,
               e.occurred_at, e.subtopic_slug
        FROM news_events e
        WHERE e.org_id = :oid
          AND e.created_at >= :cutoff
        ORDER BY e.occurred_at DESC NULLS LAST
        LIMIT 20
    """), {"oid": org_id, "cutoff": cutoff})).all()

    event_rows = [
        {
            "id": r[0],
            "headline": r[1],
            # Keep summaries short — 200 chars max
            "summary": (r[2] or "")[:200],
            "actors": list(r[3] or [])[:4],
            "event_type": r[4],
            "status": r[5],
        }
        for r in events
    ]

    updates = (await session.execute(sql_text("""
        SELECT cu.chain_id::text, nc.title,
               count(*) as update_count,
               array_agg(e.headline) as headlines
        FROM chain_updates cu
        JOIN news_chains nc ON nc.id = cu.chain_id
        JOIN news_events e ON e.id = cu.event_id
        WHERE cu.org_id = :oid
          AND cu.created_at >= :cutoff
        GROUP BY cu.chain_id, nc.title
        ORDER BY update_count DESC
        LIMIT 20
    """), {"oid": org_id, "cutoff": cutoff})).all()

    chain_updates = [
        {
            "chain_id": r[0],
            "chain_title": r[1],
            "update_count": r[2],
            "new_headlines": list(r[3] or [])[:5],
        }
        for r in updates
    ]
    chain_ids = [c["chain_id"] for c in chain_updates]

    watchlist = (await session.execute(sql_text("""
        SELECT cw.label, cw.symbol, cw.live_quote
        FROM chain_watchlist cw
        WHERE cw.org_id = :oid
          AND cw.live_quote IS NOT NULL
          AND cw.live_quote != '{}'::jsonb
        ORDER BY (cw.live_quote->>'daily_change_pct')::float DESC NULLS LAST
        LIMIT 30
    """), {"oid": org_id})).all()

    seen_symbols = set()
    quotes = []
    for r in watchlist:
        sym = r[1]
        if sym in seen_symbols:
            continue
        seen_symbols.add(sym)
        q = r[2] or {}
        quotes.append({
            "label": r[0],
            "symbol": sym,
            "current_price": q.get("current_price"),
            "daily_change_pct": q.get("daily_change_pct"),
            "daily_change_abs": q.get("daily_change_abs"),
            "vol_30d_pct": q.get("vol_30d_pct"),
        })

    impacts: list[dict] = []
    if chain_ids:
        rows = (await session.execute(sql_text("""
            SELECT ci.label, ci.category, ci.direction, ci.strength, ci.reasoning
            FROM chain_impacts ci
            WHERE ci.org_id = :oid
              AND ci.chain_id = ANY(:cids)
            ORDER BY ci.position
            LIMIT 20
        """), {"oid": org_id, "cids": chain_ids})).all()
        impacts = [
            {
                "label": r[0], "category": r[1], "direction": r[2],
                "strength": r[3], "reasoning": (r[4] or "")[:200],
            }
            for r in rows
        ]

    scenarios: list[dict] = []
    if chain_ids:
        rows = (await session.execute(sql_text("""
            SELECT cs.name, cs.condition, cs.quantified
            FROM chain_scenarios cs
            WHERE cs.org_id = :oid
              AND cs.chain_id = ANY(:cids)
            ORDER BY cs.created_at DESC
            LIMIT 10
        """), {"oid": org_id, "cids": chain_ids})).all()
        scenarios = [
            {
                "name": r[0],
                "condition": (r[1] or "")[:200],
                "primary_symbol": (r[2] or {}).get("primary_symbol"),
                "expected_move_low_pct": (r[2] or {}).get("expected_move_low_pct"),
                "expected_move_high_pct": (r[2] or {}).get("expected_move_high_pct"),
                "confidence": (r[2] or {}).get("confidence"),
            }
            for r in rows
        ]

    return {
        "window_hours": window_hours,
        "window_start": cutoff.isoformat(),
        "as_of": datetime.now(UTC).isoformat(),
        "event_count": len(event_rows),
        "chain_update_count": len(chain_updates),
        "events": event_rows,
        "chain_updates": chain_updates,
        "watchlist_quotes": quotes,
        "impacts": impacts,
        "scenarios": scenarios,
    }


async def generate_daily_brief(
    session: AsyncSession,
    *,
    org_id: str,
    window_hours: int = 24,
) -> DailyBriefResult | None:
    """Gather, LLM-generate, and return a brief. Does not persist."""
    data = await gather_brief_data(session, org_id=org_id, window_hours=window_hours)

    if data["event_count"] == 0 and data["chain_update_count"] == 0:
        log.info("brief.no_data", org_id=org_id[:8])
        return None

    # Keep the payload lean so the LLM prompt stays under token limits.
    # The brief is a summary — the model doesn't need every detail.
    data_for_prompt = {
        "as_of": data["as_of"],
        "window_hours": window_hours,
        "event_count": data["event_count"],
        "chain_update_count": data["chain_update_count"],
        "events": data["events"][:12],
        "chain_updates": data["chain_updates"][:6],
        "watchlist_quotes": data["watchlist_quotes"][:15],
        "impacts": data["impacts"][:10],
        "scenarios": data["scenarios"][:6],
    }
    data_json = json.dumps(data_for_prompt, indent=2, default=str)

    prompt, _pv = render_with_version("daily_brief.trading", block_slug="trading", data=data_json)

    try:
        raw = await complete(
            role="reasoning",
            system=None,
            user=prompt,
            org_id=org_id,
            prompt_version_id=_pv,
            purpose="daily_brief.trading",
            temperature=0.4,
            max_tokens=1800,
            json_mode=False,
            use_cache=False,
        )
    except Exception as exc:
        log.warning("brief.llm_failed", error=str(exc)[:200])
        return None

    body = raw.strip()
    title = "Daily Brief"
    if body.startswith("# "):
        lines = body.split("\n", 1)
        title = lines[0][2:].strip()[:500] or "Daily Brief"
        body = lines[1].strip() if len(lines) > 1 else body

    return DailyBriefResult(
        title=title,
        body=body,
        word_count=len(body.split()),
        event_ids=[e["id"] for e in data["events"]],
        chain_ids=[c["chain_id"] for c in data["chain_updates"]],
        raw_data=data_for_prompt,
    )
