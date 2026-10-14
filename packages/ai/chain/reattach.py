"""Auto-reattach: match new events to existing chains.

When a new event is inserted, we:
    1. Embed its headline + summary
    2. Find nearby chains via pgvector cosine similarity (block-scoped)
    3. Ask the LLM (chain.reattach.trading) whether it belongs
    4. If yes, insert a chain_update row + bump chain counters
    5. If enough new updates accumulate, trigger re-narration

This is what makes chains "living".
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.prompts.loader import render, render_with_version
from packages.ai.router import complete, embed
from packages.core.logging import get_logger

log = get_logger(__name__)

# Cosine similarity threshold for the fast pre-filter
SIMILARITY_THRESHOLD = 0.65

# Number of candidate chains to consider
TOP_K = 3

# If this many new updates attach in a row, re-narrate the chain
RENARRATE_AFTER_N_UPDATES = 2


@dataclass
class AttachDecision:
    chain_id: str
    attach: bool
    reasoning: str
    link_type: str | None
    confidence: float


async def embed_event(
    session: AsyncSession,
    *,
    event_id: str,
    headline: str,
    summary: str,
    org_id: str,
) -> list[float] | None:
    """Embed the event and store the vector. Returns the vector."""
    text = f"{headline}. {summary}"[:1500]
    try:
        vecs = await embed(texts=[text], org_id=org_id, purpose="event.embed.reattach")
        if not vecs:
            return None
        v = vecs[0]
        vstr = "[" + ",".join(str(float(x)) for x in v) + "]"
        await session.execute(sql_text("""
            UPDATE news_events
            SET embedding = CAST(:vec AS vector(768))
            WHERE id = :event_id
        """), {"vec": vstr, "event_id": event_id})
        return v
    except Exception as exc:
        log.warning("reattach.embed_failed", event_id=event_id[:8], error=str(exc)[:200])
        return None


async def find_candidate_chains(
    session: AsyncSession,
    *,
    event_vector: list[float],
    block_id: str,
    org_id: str,
) -> list[dict]:
    """Find the top-K chains most similar to this event vector."""
    vstr = "[" + ",".join(str(float(x)) for x in event_vector) + "]"
    rows = (await session.execute(sql_text("""
        SELECT
            c.id::text,
            c.title,
            c.thesis,
            c.update_count,
            1 - (c.embedding <=> CAST(:q AS vector(768))) AS similarity
        FROM news_chains c
        WHERE c.org_id = :org_id
          AND c.block_id = :block_id
          AND c.embedding IS NOT NULL
          AND c.status IN ('ready', 'developing')
        ORDER BY c.embedding <=> CAST(:q AS vector(768))
        LIMIT :k
    """), {
        "q": vstr, "org_id": org_id, "block_id": block_id, "k": TOP_K,
    })).all()

    return [
        {
            "id": r[0], "title": r[1], "thesis": r[2],
            "update_count": r[3], "similarity": float(r[4]),
        }
        for r in rows
        if float(r[4]) >= SIMILARITY_THRESHOLD
    ]


def _format_chain_events(events: list[dict]) -> str:
    lines = []
    for i, e in enumerate(events, 1):
        lines.append(
            f"  [{i}] {e.get('headline', '?')}\n"
            f"      {e.get('summary', '')[:200]}"
        )
    return "\n".join(lines) if lines else "  (no events yet)"


async def ask_llm_to_attach(
    *,
    chain: dict,
    chain_events: list[dict],
    new_event: dict,
    org_id: str,
) -> AttachDecision:
    """Ask the LLM whether the new event belongs to this chain."""
    prompt, _pv = render_with_version(
        "chain.reattach",
        block_slug="trading",
        chain_title=chain["title"],
        chain_thesis=chain.get("thesis") or "(no thesis)",
        chain_events=_format_chain_events(chain_events),
        new_headline=new_event.get("headline", ""),
        new_summary=new_event.get("summary", ""),
        new_actors=", ".join(new_event.get("actors") or []),
        new_occurred_at=new_event.get("occurred_at") or "(unknown)",
    )
    try:
        raw = await complete(
            role="fast",  # bounded decision — no need for reasoning model
            system=None,
            user=prompt,
            prompt_version_id=_pv,
            org_id=org_id,
            purpose="chain.reattach.trading",
            temperature=0.1,
            max_tokens=400,
            json_mode=True,
            use_cache=True,
        )
        parsed = json.loads(raw)
    except Exception as exc:
        log.warning("reattach.llm_failed", chain_id=chain["id"][:8], error=str(exc)[:200])
        return AttachDecision(
            chain_id=chain["id"], attach=False,
            reasoning="LLM failed", link_type=None, confidence=0.0,
        )

    return AttachDecision(
        chain_id=chain["id"],
        attach=bool(parsed.get("attach")),
        reasoning=str(parsed.get("reasoning", ""))[:500],
        link_type=str(parsed.get("link_type", ""))[:32] or None,
        confidence=float(parsed.get("confidence", 0.5)),
    )


async def reattach_event(
    session: AsyncSession,
    *,
    event_id: str,
    org_id: str,
    block_id: str,
) -> AttachDecision | None:
    """Main entry: try to attach this event to an existing chain.

    Returns the AttachDecision if the event was attached, else None.
    """
    # Load event
    row = (await session.execute(sql_text("""
        SELECT id::text, headline, summary, actors, occurred_at, embedding::text
        FROM news_events
        WHERE id = :event_id AND org_id = :org_id
    """), {"event_id": event_id, "org_id": org_id})).first()
    if not row:
        return None
    event = {
        "id": row[0], "headline": row[1], "summary": row[2],
        "actors": list(row[3] or []),
        "occurred_at": row[4].isoformat() if row[4] else None,
    }

    # Embed if missing
    if not row[5]:
        vec = await embed_event(
            session,
            event_id=event_id,
            headline=event["headline"],
            summary=event["summary"],
            org_id=org_id,
        )
        if not vec:
            return None
        event_vector = vec
    else:
        event_vector = [float(x) for x in row[5].strip("[]").split(",")]

    # Find candidates
    candidates = await find_candidate_chains(
        session,
        event_vector=event_vector,
        block_id=block_id,
        org_id=org_id,
    )
    if not candidates:
        log.info("reattach.no_candidates", event_id=event_id[:8])
        return None

    # Ask the LLM per candidate; use the first "yes"
    for candidate in candidates:
        # Skip if already attached
        exists = (await session.execute(sql_text("""
            SELECT 1 FROM chain_updates
            WHERE chain_id = :cid AND event_id = :eid
            LIMIT 1
        """), {"cid": candidate["id"], "eid": event_id})).first()
        if exists:
            continue

        # Load chain's current events
        chain_events_rows = (await session.execute(sql_text("""
            SELECT e.headline, e.summary, e.actors
            FROM chain_links cl
            JOIN news_events e ON e.id = cl.from_event_id
            WHERE cl.chain_id = :cid
            UNION
            SELECT e.headline, e.summary, e.actors
            FROM chain_links cl
            JOIN news_events e ON e.id = cl.to_event_id
            WHERE cl.chain_id = :cid
        """), {"cid": candidate["id"]})).all()
        chain_events = [
            {"headline": r[0], "summary": r[1], "actors": list(r[2] or [])}
            for r in chain_events_rows
        ]

        decision = await ask_llm_to_attach(
            chain=candidate,
            chain_events=chain_events,
            new_event=event,
            org_id=org_id,
        )
        log.info(
            "reattach.decision",
            event_id=event_id[:8], chain_id=candidate["id"][:8],
            attach=decision.attach, similarity=candidate["similarity"],
            confidence=decision.confidence,
        )
        if decision.attach:
            # Insert chain_update
            await session.execute(sql_text("""
                INSERT INTO chain_updates
                    (org_id, chain_id, event_id, similarity, reasoning, attached_by)
                VALUES
                    (:org_id, :chain_id, :event_id, :similarity, :reasoning, 'auto')
                ON CONFLICT (chain_id, event_id) DO NOTHING
            """), {
                "org_id": org_id,
                "chain_id": candidate["id"],
                "event_id": event_id,
                "similarity": candidate["similarity"],
                "reasoning": decision.reasoning,
            })
            # Bump counters + mark developing
            await session.execute(sql_text("""
                UPDATE news_chains
                SET update_count = update_count + 1,
                    last_updated_at = now(),
                    status = 'developing'
                WHERE id = :cid
            """), {"cid": candidate["id"]})

            # Notify all followers of this chain
            try:
                await _notify_followers(
                    session,
                    org_id=org_id,
                    chain_id=candidate["id"],
                    chain_title=candidate["title"],
                    event_id=event_id,
                    event_headline=event["headline"],
                    reasoning=decision.reasoning,
                )
            except Exception as exc:
                log.warning("reattach.notify_failed", error=str(exc)[:200])

            await session.commit()
            return decision

    return None

async def _notify_followers(
    session: AsyncSession,
    *,
    org_id: str,
    chain_id: str,
    chain_title: str,
    event_id: str,
    event_headline: str,
    reasoning: str | None,
) -> None:
    """Insert a notification row for every user following this chain."""
    followers = (await session.execute(sql_text("""
        SELECT user_id::text
        FROM chain_follows
        WHERE chain_id = :cid AND org_id = :org_id
    """), {"cid": chain_id, "org_id": org_id})).all()

    if not followers:
        log.info("reattach.no_followers", chain_id=chain_id[:8])
        return

    title = f"New event in '{chain_title[:120]}'"
    body = f"{event_headline[:400]}"
    if reasoning:
        body += f"\n\n{reasoning[:400]}"

    for (user_id,) in followers:
        await session.execute(sql_text("""
            INSERT INTO notifications
                (org_id, user_id, kind, chain_id, event_id, title, body)
            VALUES
                (:org_id, :user_id, 'chain_update', :chain_id, :event_id, :title, :body)
        """), {
            "org_id": org_id,
            "user_id": user_id,
            "chain_id": chain_id,
            "event_id": event_id,
            "title": title,
            "body": body,
        })
    log.info("reattach.notified", chain_id=chain_id[:8], followers=len(followers))
