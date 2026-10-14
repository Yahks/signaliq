"""
SignalIQ — Phase T.5.1: LLM-curated subblock generators.

Five generators that don't need external data:
  - trading_psychology
  - investment_strategies
  - trading_styles
  - class_room
  - trading_tools

Each picks a topic from training_config and generates an article.
Topic selection uses least-recently-used tracking via trading_posts.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBBLOCKS_DIR = ROOT / "packages" / "trading" / "subblocks"
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "trading.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(f"\n❌ {path.name}: pattern not found for '{label}'")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# Shared base for the 5 LLM-curated subblocks
# ─────────────────────────────────────────────────────────────────────

SHARED_SRC = '''"""Shared helpers for LLM-curated trading subblocks.

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
) -> str | None:
    """Return the least-recently-used topic from the subblock's pool.

    Reads the last 20 posts for this subblock, extracts the "topic" field
    from each one's source_payload, and returns the first pool entry that
    wasn't used recently (or the least-recently used if all have been used).
    """
    slug = subblock["slug"]
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
'''


# ─────────────────────────────────────────────────────────────────────
# The 5 generators (all follow the same shape)
# ─────────────────────────────────────────────────────────────────────

def _build_generator(slug: str, name: str, topic_label: str) -> str:
    """Produce the generator source for one LLM-curated subblock."""
    return f'''"""{slug} generator: {topic_label} for {name}."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from .base import (
    TradingPost,
    call_llm_json_verified,
    persist_post,
)
from ._llm_shared import pick_next_topic

log = get_logger(__name__)


PROMPT_TEMPLATE = """Write a short, high-quality educational piece on this topic:

**{{topic}}**

The reader is a motivated trader or investor. Assume general knowledge of markets,
but no prior familiarity with this specific topic.

Structure (BRIEF — the whole piece is 250-400 words of markdown):
1. A one-paragraph framing of why this topic matters in practice.
2. Two or three short sections with headings.
3. One concrete example (numeric or scenario-based) if it helps.
4. A one-sentence "what to watch for" caveat.

Epistemic discipline:
- No invented statistics, no fake studies, no fake historical dates.
- Concrete examples may use round numbers and be clearly hypothetical —
  label them as such ("for example, if a $100K portfolio…").
- Do NOT give buy/sell advice.
- Do NOT promise returns.

Style notes are in the system prompt.
"""


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    topic = await pick_next_topic(session, org_id=org_id, subblock=subblock)
    if not topic:
        log.warning("{slug}.no_topic", org_id=org_id)
        return {{"status": "no_topic", "slug": subblock["slug"]}}

    user_prompt = PROMPT_TEMPLATE.format(topic=topic)

    post = await call_llm_json_verified(
        session,
        org_id=org_id,
        system=subblock.get("style_guide") or "You write clear trading education.",
        user=user_prompt,
        prompt_name="trading.{slug}",
        source_payload={{"topic": topic}},
        target_words=350,
    )

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={{"topic": topic, "generator": "llm_curated"}},
        source_urls=[],
        symbols=[],
    )
    await session.commit()

    log.info(
        "{slug}.persisted",
        post_id=post_id[:8],
        topic=topic,
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {{
        "status": "ok",
        "slug": subblock["slug"],
        "post_id": post_id,
        "topic": topic,
        "word_count": post.word_count,
    }}
'''


LLM_SUBBLOCKS = [
    ("trading_psychology",   "Trading Psychology",      "a behavioural lesson"),
    ("investment_strategies", "Investment Strategies",  "a named strategy explained"),
    ("trading_styles",       "Trading Styles",          "a trading style compared"),
    ("class_room",           "Class Room",              "a curriculum lesson"),
    ("trading_tools",        "Trading Tools and Platform", "a tool or platform reviewed"),
]


def step1_write_shared() -> None:
    print("\n[1/4] Write shared helper")
    (SUBBLOCKS_DIR / "_llm_shared.py").write_text(SHARED_SRC)
    print("  ✓ wrote _llm_shared.py")


def step2_write_generators() -> None:
    print("\n[2/4] Write 5 generator files")
    for slug, name, topic_label in LLM_SUBBLOCKS:
        path = SUBBLOCKS_DIR / f"{slug}.py"
        path.write_text(_build_generator(slug, name, topic_label))
        print(f"  ✓ wrote {path.name}")


def step3_add_tasks() -> None:
    print("\n[3/4] Add Celery tasks")
    src = TASK_FILE.read_text()
    added = 0
    for slug, name, _ in LLM_SUBBLOCKS:
        task_name = f"trading_generate_{slug}"
        if task_name in src:
            continue
        block = f'''

@celery_app.task(name="tasks.{task_name}", bind=True)
def {task_name}(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate {slug} for one org, or all orgs if none given."""
    orgs = [{{"id": org_id}}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = asyncio.run(_generate_for_org(o["id"], "{slug}"))
        results.append({{"org": o["id"], **r}})
    return {{"count": len(results), "results": results}}
'''
        src = src.rstrip() + block
        added += 1
    if added:
        TASK_FILE.write_text(src)
        print(f"  ✓ added {added} tasks")
    else:
        print("  ↷ tasks already present")


# ─────────────────────────────────────────────────────────────────────
# Register in the API's slug → task map so /generate works for these
# ─────────────────────────────────────────────────────────────────────

def step4_update_api_slug_map() -> None:
    print("\n[4/4] Update API slug → task map")

    trading_router = ROOT / "apps" / "api" / "routers" / "trading.py"
    src = trading_router.read_text()

    if "_TASK_BY_SLUG" not in src:
        raise SystemExit("❌ _TASK_BY_SLUG not found in trading.py")

    old = '''_TASK_BY_SLUG = {
    "stock_lists": "tasks.trading_generate_stock_lists",
    "market_signals": "tasks.trading_generate_market_signals",
    "screener_picks": "tasks.trading_generate_screener_picks",
}'''

    new_lines = [
        '_TASK_BY_SLUG = {',
        '    "stock_lists": "tasks.trading_generate_stock_lists",',
        '    "market_signals": "tasks.trading_generate_market_signals",',
        '    "screener_picks": "tasks.trading_generate_screener_picks",',
    ]
    for slug, _, _ in LLM_SUBBLOCKS:
        new_lines.append(f'    "{slug}": "tasks.trading_generate_{slug}",')
    new_lines.append('}')
    new = "\n".join(new_lines)

    if new.strip() in src and old not in src:
        print("  ↷ slug map already updated")
        return

    if old not in src:
        raise SystemExit("❌ _TASK_BY_SLUG block not found")

    trading_router.write_text(src.replace(old, new, 1))
    print("  ✓ slug map updated with 5 new entries")


# ─────────────────────────────────────────────────────────────────────
# Restart worker + API, then trigger each generator once
# ─────────────────────────────────────────────────────────────────────

def step5_restart_and_test() -> bool:
    print("\n[5/5] Restart worker + API, run each generator once")

    subprocess.run(["pkill", "-9", "-f", "celery.*worker"], check=False)
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    import time
    time.sleep(3)

    worker_log = Path("/tmp/signaliq-worker.log")
    with worker_log.open("w") as f:
        subprocess.Popen(
            ["celery", "-A", "apps.worker.celery_app", "worker",
             "--loglevel=info", "-Q", "default,ingestion", "--hostname=worker1@%h"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )

    api_log = Path("/tmp/signaliq-api.log")
    with api_log.open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )

    time.sleep(8)

    # Run each generator directly (fast, no celery round-trip)
    for slug, name, _ in LLM_SUBBLOCKS:
        print(f"\n--- {slug} ---")
        r = subprocess.run([
            "python", "-c",
            f'''
import asyncio, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.trading.subblocks.base import generate_subblock

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    org = (await (factory().__aenter__()).execute(text(
        "SELECT id::text, slug FROM orgs WHERE slug = 'aardesst'"
    ))).first()
    async with factory() as session:
        await session.execute(text(f"SET app.current_org_id = '{{org[0]}}'"))
        await session.commit()
        result = await generate_subblock(session, org_id=org[0], slug="{slug}")
        print(f"  result: {{result}}")
    await engine.dispose()

asyncio.run(main())
            '''.strip()
        ], cwd=ROOT, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            print(f"  ❌ {slug} failed:")
            print(r.stderr[-800:])
            return False
        print(r.stdout[-600:] if len(r.stdout) > 600 else r.stdout)

    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.5.1: LLM-curated subblocks (5 generators)")
    print("=" * 70)

    step1_write_shared()
    step2_write_generators()
    step3_add_tasks()
    step4_update_api_slug_map()
    ok = step5_restart_and_test()

    print("=" * 70)
    if ok:
        print("✅ T.5.1 complete — 5 LLM-curated subblocks ship")
        print()
        print("Refresh http://localhost:3000/trading")
        print("Now 8 of 16 subblocks have generators; all 8 should produce posts.")
        sys.exit(0)
    print("❌ T.5.1 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
