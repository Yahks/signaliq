"""
SignalIQ — Phase T.3.1: stock_lists generator.

Ships:
  1. packages/trading/subblocks/__init__.py
  2. packages/trading/subblocks/base.py       (shared: TradingPost, LLM call, persist)
  3. packages/trading/subblocks/stock_lists.py (the generator)
  4. apps/worker/tasks/trading.py              (Celery task)
  5. scripts/run_stock_lists_once.py           (manual trigger for testing)

Then runs the generator once against the dev DB and prints the resulting post.

Idempotent. Fails loudly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBBLOCKS_INIT = ROOT / "packages" / "trading" / "subblocks" / "__init__.py"
BASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"
STOCK_LISTS = ROOT / "packages" / "trading" / "subblocks" / "stock_lists.py"
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "trading.py"
RUN_ONCE = ROOT / "scripts" / "run_stock_lists_once.py"


# ─────────────────────────────────────────────────────────────────────
# 1. base.py — shared generator infrastructure
# ─────────────────────────────────────────────────────────────────────

BASE_SRC = '''"""Shared infrastructure for trading subblock generators.

Every subblock generator follows the same shape:
  1. Load subblock config (trading_subblocks row)
  2. Fetch data (scraper, yahoo, fred, edgar, or computed)
  3. Render a compact markdown prompt payload
  4. Call the reasoning LLM with the subblock's style_guide as system prompt
  5. Parse JSON response → TradingPost
  6. Persist to trading_posts with full source provenance

This module provides the pieces the generators share so each
generator file is 60-100 lines, not 300.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class TradingPost:
    title: str
    body: str
    word_count: int


# ─────────────────────────────────────────────────────────────────────
# Load subblock config
# ─────────────────────────────────────────────────────────────────────

async def load_subblock(
    session: AsyncSession,
    *,
    slug: str,
    org_id: str | None = None,
) -> dict[str, Any] | None:
    """Load a subblock row. Prefers org-specific override, falls back to global."""
    row = (await session.execute(sql_text("""
        SELECT
            ts.id::text, ts.org_id::text, ts.block_id::text,
            ts.slug, ts.name, ts.description, ts.category, ts.cadence,
            ts.source_kind, ts.theme, ts.style_guide, ts.training_config,
            ts.enabled, ts.sort_order
        FROM trading_subblocks ts
        WHERE ts.slug = :slug
          AND (ts.org_id = :org_id OR ts.org_id IS NULL)
        ORDER BY ts.org_id NULLS LAST
        LIMIT 1
    """), {"slug": slug, "org_id": org_id})).first()

    if not row:
        return None
    return {
        "id": row[0],
        "org_id": row[1],
        "block_id": row[2],
        "slug": row[3],
        "name": row[4],
        "description": row[5],
        "category": row[6],
        "cadence": row[7],
        "source_kind": row[8],
        "theme": row[9],
        "style_guide": row[10],
        "training_config": row[11],
        "enabled": row[12],
        "sort_order": row[13],
    }


# ─────────────────────────────────────────────────────────────────────
# Prompt helpers
# ─────────────────────────────────────────────────────────────────────

def render_markdown_table(
    rows: list[dict[str, Any]],
    columns: list[str],
    *,
    title: str | None = None,
) -> str:
    """Render a list of dicts as a markdown table for the LLM prompt."""
    if not rows:
        return f"### {title or 'Table'}\\n(no data)"
    lines = [f"### {title}" if title else "", "", "| " + " | ".join(columns) + " |",
             "|" + "|".join(["---"] * len(columns)) + "|"]
    for r in rows:
        cells = [str(r.get(c, "") or "").replace("|", "\\\\|") for c in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return "\\n".join([line for line in lines if line])


def _extract_json_block(text: str) -> str:
    """Pull the first balanced JSON object out of a possibly-chatty response."""
    text = text.strip()
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\\s*", "", text)
    text = re.sub(r"\\s*```\\s*$", "", text)
    # Find the first { ... } balanced block
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("unbalanced JSON")


# ─────────────────────────────────────────────────────────────────────
# LLM call
# ─────────────────────────────────────────────────────────────────────

async def call_llm_json(
    session: AsyncSession,
    *,
    org_id: str,
    system: str,
    user: str,
    prompt_name: str,
    target_words: int = 400,
) -> TradingPost:
    """Call the reasoning LLM and parse {title, body} from the response.

    Uses packages.ai.router.complete with role='reasoning'.
    """
    from packages.ai.router import complete

    full_system = (
        f"{system}\\n\\n"
        f"RULES:\\n"
        f"- Output MUST be valid JSON: {{\\"title\\": \\"...\\", \\"body\\": \\"...\\"}}\\n"
        f"- The body is markdown. Use tables, headings, and short paragraphs.\\n"
        f"- Target length: ~{target_words} words.\\n"
        f"- Use ONLY numbers and facts present in the input data.\\n"
        f"- Do not invent tickers, prices, percentages, or dates.\\n"
        f"- If the data looks degraded or empty, say so plainly rather than fabricating."
    )
    full_user = f"{user}\\n\\nReturn valid JSON with 'title' and 'body' fields."

    log.info("subblock.llm_call_start", prompt=prompt_name, words=target_words)

    resp = await complete(
        org_id=org_id,
        role="reasoning",
        system=full_system,
        user=full_user,
        purpose=prompt_name,
        temperature=0.3,
        max_tokens=2000,
    )

    text = (resp.text or "").strip()
    try:
        payload = json.loads(_extract_json_block(text))
    except Exception as exc:
        log.warning("subblock.llm_parse_failed", error=str(exc)[:200], raw=text[:400])
        # Fallback: use whole response as body, generate title from prompt_name
        return TradingPost(
            title=prompt_name.replace(".", " ").title(),
            body=text or "(empty response)",
            word_count=len((text or "").split()),
        )

    title = (payload.get("title") or prompt_name).strip()
    body = (payload.get("body") or "").strip()
    return TradingPost(
        title=title[:500],
        body=body,
        word_count=len(body.split()),
    )


# ─────────────────────────────────────────────────────────────────────
# Persist
# ─────────────────────────────────────────────────────────────────────

async def persist_post(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
    post: TradingPost,
    source_payload: dict[str, Any],
    source_urls: list[str] | None = None,
    symbols: list[str] | None = None,
) -> str:
    """Insert a trading_posts row. Returns the new post id."""
    row = (await session.execute(sql_text("""
        INSERT INTO trading_posts (
            org_id, subblock_id, title, body, format, source_kind,
            source_payload, source_urls, symbols, status, word_count, published_at
        ) VALUES (
            :org_id, :subblock_id, :title, :body, 'long_form', :source_kind,
            CAST(:source_payload AS jsonb), :source_urls, :symbols,
            'published', :word_count, now()
        )
        RETURNING id::text
    """), {
        "org_id": org_id,
        "subblock_id": subblock["id"],
        "title": post.title,
        "body": post.body,
        "source_kind": subblock["source_kind"],
        "source_payload": json.dumps(source_payload, default=str),
        "source_urls": source_urls or [],
        "symbols": symbols or [],
        "word_count": post.word_count,
    })).first()

    if not row:
        raise RuntimeError("persist_post: no row returned")
    return row[0]


# ─────────────────────────────────────────────────────────────────────
# The generator interface
# ─────────────────────────────────────────────────────────────────────

async def generate_subblock(
    session: AsyncSession,
    *,
    org_id: str,
    slug: str,
) -> dict[str, Any]:
    """Load subblock, dispatch to its generator module, persist, return summary.

    Generator modules are imported lazily so a missing one doesn't break imports.
    """
    subblock = await load_subblock(session, slug=slug, org_id=org_id)
    if not subblock:
        raise ValueError(f"subblock not found: {slug}")
    if not subblock["enabled"]:
        return {"status": "disabled", "slug": slug}

    try:
        mod = __import__(
            f"packages.trading.subblocks.{slug}",
            fromlist=["generate"],
        )
    except ImportError as exc:
        raise ValueError(f"no generator for subblock: {slug} ({exc})") from exc

    result = await mod.generate(session, org_id=org_id, subblock=subblock)
    if result is None:
        return {"status": "skipped", "slug": slug}
    return result
'''


# ─────────────────────────────────────────────────────────────────────
# 2. stock_lists.py — the generator
# ─────────────────────────────────────────────────────────────────────

STOCK_LISTS_SRC = '''"""stock_lists generator: scrape gainers/losers/active → narrated market roundup."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.trading.sources.scraper import fetch

from .base import (
    TradingPost,
    call_llm_json,
    persist_post,
    render_markdown_table,
)

log = get_logger(__name__)


def _to_rows(result_rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Normalize a scraped list into consistent keys + cap at top_n."""
    out = []
    for r in result_rows[:top_n]:
        out.append({
            "symbol": (r.get("symbol") or "").strip(),
            "name": (r.get("name") or "").strip(),
            "change": (r.get("change") or "").strip(),
        })
    return [x for x in out if x["symbol"]]


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    cfg = subblock.get("training_config") or {}
    sources = cfg.get("sources") or []
    top_n = int(cfg.get("top_n") or 15)

    # --- 1. Fetch each source ---
    fetched: dict[str, list[dict[str, Any]]] = {}
    source_urls: list[str] = []
    for src in sources:
        sid = src.get("id")
        if not sid:
            continue
        result = await fetch(src, force_refresh=True)
        source_urls.append(src.get("url", ""))
        if not result.ok:
            log.warning("stock_lists.source_failed", source_id=sid, error=result.error)
            fetched[sid] = []
            continue
        fetched[sid] = _to_rows(result.rows, top_n)
        log.info("stock_lists.source_fetched", source_id=sid, rows=len(fetched[sid]))

    # --- 2. Build the prompt payload ---
    gainers = fetched.get("stockanalysis.gainers", [])
    losers = fetched.get("stockanalysis.losers", [])
    active = fetched.get("stockanalysis.active", [])

    sections: list[str] = []
    if gainers:
        sections.append(render_markdown_table(
            gainers, ["symbol", "name", "change"],
            title=f"Top gainers (top {len(gainers)} by %)",
        ))
    if losers:
        sections.append(render_markdown_table(
            losers, ["symbol", "name", "change"],
            title=f"Top losers (top {len(losers)} by %)",
        ))
    if active:
        sections.append(render_markdown_table(
            active, ["symbol", "name", "change"],
            title=f"Most active (top {len(active)} by volume)",
        ))

    if not sections:
        log.warning("stock_lists.no_data", org_id=org_id)
        return {"status": "no_data", "slug": subblock["slug"]}

    payload_md = "\\n\\n".join(sections)

    user_prompt = (
        f"Today's market movers from StockAnalysis.com. "
        f"Use these exact tickers and numbers in your write-up. "
        f"Note: the 'change' column for the most-active list shows volume, "
        f"not percent change — reflect that in your framing.\\n\\n"
        f"{payload_md}\\n\\n"
        f"Write a short market roundup for {subblock['name']}. "
        f"Cover: (1) headline — what stands out, (2) notable movers with context, "
        f"(3) one honest caveat about single-day moves. "
        f"Do not give buy/sell advice."
    )

    # --- 3. LLM ---
    post = await call_llm_json(
        session,
        org_id=org_id,
        system=subblock.get("style_guide") or "You write clear market roundups.",
        user=user_prompt,
        prompt_name="trading.stock_lists",
        target_words=350,
    )

    # --- 4. Symbols for later filtering ---
    all_symbols = sorted({
        r["symbol"]
        for r in (gainers + losers + active)
        if r.get("symbol")
    })

    # --- 5. Persist ---
    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "gainers": gainers,
            "losers": losers,
            "active": active,
            "top_n": top_n,
        },
        source_urls=[u for u in source_urls if u],
        symbols=all_symbols[:200],
    )
    await session.commit()

    log.info(
        "stock_lists.persisted",
        post_id=post_id[:8],
        words=post.word_count,
        symbols=len(all_symbols),
    )
    return {
        "status": "ok",
        "slug": subblock["slug"],
        "post_id": post_id,
        "word_count": post.word_count,
        "symbols": len(all_symbols),
    }
'''


# ─────────────────────────────────────────────────────────────────────
# 3. Celery task
# ─────────────────────────────────────────────────────────────────────

TASK_SRC = '''"""Trading subblock generation tasks.

One task per cadence group. Each iterates orgs and calls
packages.trading.subblocks.base.generate_subblock for each subblock
in that cadence.
"""
from __future__ import annotations

import asyncio

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


def _list_orgs() -> list[dict]:
    from sqlalchemy import create_engine, text as sql_text
    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql_text(
                "SELECT id::text, slug FROM orgs WHERE deleted_at IS NULL"
            )).all()
            return [{"id": r[0], "slug": r[1]} for r in rows]
    finally:
        engine.dispose()


async def _generate_for_org(org_id: str, slug: str) -> dict:
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    from packages.core.config import get_settings
    from packages.trading.subblocks.base import generate_subblock

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_pre_ping=True, pool_recycle=180,
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        await session.execute(sql_text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()
        try:
            result = await generate_subblock(session, org_id=org_id, slug=slug)
        except Exception as exc:
            log.warning("trading.generate_failed", slug=slug, error=str(exc)[:200])
            result = {"status": "failed", "slug": slug, "error": str(exc)[:200]}

    await engine.dispose()
    return result


@celery_app.task(name="tasks.trading_generate_stock_lists", bind=True)
def trading_generate_stock_lists(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate stock_lists for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = asyncio.run(_generate_for_org(o["id"], "stock_lists"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}
'''


# ─────────────────────────────────────────────────────────────────────
# 4. run_stock_lists_once.py — manual test trigger
# ─────────────────────────────────────────────────────────────────────

RUN_ONCE_SRC = '''"""Manually run the stock_lists generator once against the dev DB.

Prints the resulting post (title, word count, first 600 chars of body).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    from packages.core.config import get_settings
    from packages.trading.subblocks.base import generate_subblock

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Pick the first non-deleted org
    async with factory() as session:
        org = (await session.execute(text(
            "SELECT id::text, slug FROM orgs WHERE deleted_at IS NULL "
            "ORDER BY created_at LIMIT 1"
        ))).first()
    if not org:
        print("❌ no org found")
        sys.exit(1)
    org_id, org_slug = org
    print(f"Org: {org_slug} ({org_id[:8]}…)")

    async with factory() as session:
        await session.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()
        print("Running stock_lists generator…\\n")
        result = await generate_subblock(session, org_id=org_id, slug="stock_lists")
        print(f"Result: {result}\\n")

    # Fetch the just-created post
    async with factory() as session:
        await session.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()
        row = (await session.execute(text("""
            SELECT title, body, word_count, symbols, source_urls
            FROM trading_posts
            WHERE org_id = :o
            ORDER BY created_at DESC LIMIT 1
        """), {"o": org_id})).first()
        if row:
            title, body, wc, symbols, urls = row
            print("=" * 70)
            print(f"TITLE: {title}")
            print(f"WORDS: {wc}")
            print(f"SYMBOLS: {len(symbols or [])}  e.g. {(symbols or [])[:5]}")
            print(f"SOURCES: {urls}")
            print("=" * 70)
            print("BODY (first 1500 chars):")
            print((body or "")[:1500])
            print("…")
        else:
            print("❌ no post found")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
'''


# ─────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────

def write_files() -> None:
    print("[1/3] Write files")
    SUBBLOCKS_INIT.parent.mkdir(parents=True, exist_ok=True)

    for path, content in [
        (SUBBLOCKS_INIT, '"""Trading subblock generators."""\n'),
        (BASE, BASE_SRC),
        (STOCK_LISTS, STOCK_LISTS_SRC),
        (TASK_FILE, TASK_SRC),
        (RUN_ONCE, RUN_ONCE_SRC),
    ]:
        path.write_text(content)
        print(f"  ✓ wrote {path.relative_to(ROOT)}")


def verify_imports() -> bool:
    print("\n[2/3] Verify imports")
    r = subprocess.run(
        ["python", "-c",
         "from packages.trading.subblocks.base import generate_subblock, TradingPost; "
         "from packages.trading.subblocks import stock_lists; "
         "print('ok')"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if r.returncode != 0:
        print("  ❌ import failed:")
        print(r.stderr)
        return False
    print("  ✓ imports clean")
    return True


def run_once() -> bool:
    print("\n[3/3] Run stock_lists generator once")
    r = subprocess.run(
        ["python", "scripts/run_stock_lists_once.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    print(r.stdout[-6000:] if len(r.stdout) > 6000 else r.stdout)
    if r.returncode != 0:
        print("  ❌ generator failed:")
        print(r.stderr[-1000:])
        return False
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.3.1: stock_lists generator")
    print("=" * 70)

    write_files()
    ok_imports = verify_imports()
    if not ok_imports:
        sys.exit(1)

    ok_run = run_once()

    print("=" * 70)
    if ok_run:
        print("✅ T.3.1 complete — stock_lists generates real posts")
        print("   Next: T.3.2 — market_signals generator (same pattern)")
        sys.exit(0)
    print("❌ T.3.1 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
