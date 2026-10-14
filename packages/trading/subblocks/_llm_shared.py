"""Shared helpers for LLM-curated trading subblocks.

These subblocks don't fetch external data — the LLM writes the content
directly, guided by:
  - the subblock's style_guide (voice/tone)
  - a topic pool from training_config
  - recent posts (to avoid repetition)

Topic selection is least-recently-used: we look at the last N posts for
this subblock, extract their topics from source_payload, and pick a topic
from the pool that hasn't been covered recently.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


# Which training_config key holds the topic pool, per subblock
_POOL_KEY_BY_SLUG = {
    "trading_psychology": "topic_pool",
    "investment_strategies": "strategy_pool",
    "trading_styles": "style_pool",
    "class_room": "curriculum",
    "trading_tools": "tool_pool",
}


async def pick_next_topic(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
    pool_key: str | None = None,
) -> str | None:
    """Return the least-recently-used topic from the subblock's pool.

    Reads the last 20 posts for this subblock, extracts the "topic" field
    from each one's source_payload, and returns the first pool entry that
    wasn't used recently (or the least-recently used if all have been used).

    pool_key defaults to the subblock's mapped key, but callers can
    override (e.g. fundamental_metrics uses "topic_pool" dynamically).
    """
    slug = subblock["slug"]
    if pool_key is None:
        pool_key = _POOL_KEY_BY_SLUG.get(slug)
    if not pool_key:
        log.warning("llm_subblock.no_pool_key", slug=slug)
        return None

    cfg = subblock.get("training_config") or {}
    pool = cfg.get(pool_key) or []
    if not pool:
        log.warning("llm_subblock.empty_pool", slug=slug, key=pool_key)
        return None

    # Recent topics for this subblock
    rows = (await session.execute(sql_text("""
        SELECT source_payload->>'topic' AS topic
        FROM trading_posts
        WHERE org_id = :org_id
          AND subblock_id = :subblock_id
          AND status IN ('published', 'draft')
        ORDER BY created_at DESC
        LIMIT 20
    """), {
        "org_id": org_id,
        "subblock_id": subblock["id"],
    })).all()

    recent_topics = [r[0] for r in rows if r[0]]

    # First pool item not in recent list
    for topic in pool:
        if topic not in recent_topics:
            log.info("llm_subblock.topic_picked", slug=slug, topic=topic, fresh=True)
            return topic

    # All topics used recently — fall back to the one used longest ago
    # (pool[-1] since we just scanned newest-first)
    for topic in reversed(pool):
        if topic in recent_topics:
            log.info("llm_subblock.topic_picked", slug=slug, topic=topic, fresh=False)
            return topic

    # Shouldn't reach here if pool is non-empty
    return pool[0]
