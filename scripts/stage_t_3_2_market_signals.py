"""
SignalIQ — Phase T.3.2: market_signals generator.

Second trading subblock. Same pattern as stock_lists:
  - fetch sources via scraper
  - render markdown
  - LLM narrates
  - verify against source_payload
  - persist

Difference: this subblock's sources return symbol + name only (no ratings
or price targets). The generator narrates what's actually available and
notes the coverage limitation honestly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKET_SIGNALS = ROOT / "packages" / "trading" / "subblocks" / "market_signals.py"
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "trading.py"
RUN_ONCE = ROOT / "scripts" / "run_market_signals_once.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"\n❌ {path.relative_to(ROOT)} does not exist")
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:500]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    check = path.read_text()
    if new not in check:
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new text not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. The generator
# ─────────────────────────────────────────────────────────────────────

MARKET_SIGNALS_SRC = '''"""market_signals generator: screener lists → analyst-signal summary.

Sources (from training_config):
  - stockanalysis.screener    — general stock screener (symbol, name)
  - finviz.screener_buy       — Finviz buy-or-better screener (symbol, name)

Both sources provide ticker + company name only. This generator:
  1. Fetches the lists
  2. Narrates what the screener coverage shows (which names are flagged)
  3. Explicitly notes that per-symbol rating/target detail is not
     available from these sources, to avoid fabrication

Future: swap in StockAnalysis ratings page or Yahoo quoteSummary for
per-symbol analyst data. This generator is designed for that upgrade —
change training_config.sources, and the prompt adapts.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.trading.sources.scraper import fetch

from .base import (
    TradingPost,
    call_llm_json_verified,
    persist_post,
    render_markdown_table,
)

log = get_logger(__name__)


def _to_rows(rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    out = []
    for r in rows[:top_n]:
        out.append({
            "symbol": (r.get("symbol") or "").strip(),
            "name": (r.get("name") or "").strip(),
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
    top_n = int(cfg.get("top_n") or 20)

    # --- 1. Fetch ---
    fetched: dict[str, list[dict[str, Any]]] = {}
    source_urls: list[str] = []
    for src in sources:
        sid = src.get("id")
        if not sid:
            continue
        result = await fetch(src, force_refresh=True)
        source_urls.append(src.get("url", ""))
        if not result.ok:
            log.warning("market_signals.source_failed", source_id=sid, error=result.error)
            fetched[sid] = []
            continue
        fetched[sid] = _to_rows(result.rows, top_n)
        log.info("market_signals.source_fetched", source_id=sid, rows=len(fetched[sid]))

    screener = fetched.get("stockanalysis.screener", [])
    finviz = fetched.get("finviz.screener_buy", [])

    if not screener and not finviz:
        log.warning("market_signals.no_data", org_id=org_id)
        return {"status": "no_data", "slug": subblock["slug"]}

    # --- 2. Build prompt payload ---
    sections: list[str] = []
    if screener:
        sections.append(render_markdown_table(
            screener, ["symbol", "name"],
            title=f"StockAnalysis stock screener (top {len(screener)} names)",
        ))
    if finviz:
        sections.append(render_markdown_table(
            finviz, ["symbol", "name"],
            title=f"Finviz Buy-or-Better screener (top {len(finviz)} names)",
        ))

    payload_md = "\\n\\n".join(sections)

    user_prompt = (
        f"Today's analyst-signal coverage from StockAnalysis and Finviz "
        f"screener pages.\\n\\n"
        f"{payload_md}\\n\\n"
        f"Write a short signal summary for {subblock['name']}.\\n\\n"
        f"Structure (be BRIEF):\\n"
        f"1. Headline: 1-2 sentences on what the screeners flag today.\\n"
        f"2. A short paragraph noting which names appear on Finviz's "
        f"buy-or-better list and which appear on the general screener — "
        f"just the tickers, no invented ratings.\\n"
        f"3. An explicit coverage note stating that per-symbol analyst "
        f"ratings and price targets are not available from these sources; "
        f"only ticker + company name is provided.\\n\\n"
        f"Epistemic discipline:\\n"
        f"- State ONLY what the source data shows — ticker and name.\\n"
        f"- Do NOT invent ratings, price targets, upgrades, downgrades,\\n"
        f"  or analyst commentary. You have none of that in the data.\\n"
        f"- Do NOT write 'driven by', 'due to', 'reflecting', 'indicating'.\\n"
        f"- Total prose: 100-150 words.\\n"
        f"- Do NOT give buy/sell advice."
    )

    # --- 3. LLM (with verification) ---
    post = await call_llm_json_verified(
        session,
        org_id=org_id,
        system=subblock.get("style_guide") or "You write clear market summaries.",
        user=user_prompt,
        prompt_name="trading.market_signals",
        source_payload={
            "screener": screener,
            "finviz": finviz,
        },
        target_words=150,
    )

    # --- 4. Symbols ---
    all_symbols = sorted({r["symbol"] for r in (screener + finviz) if r.get("symbol")})

    # --- 5. Persist ---
    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "screener": screener,
            "finviz": finviz,
            "top_n": top_n,
            "note": "sources provide ticker+name only; no ratings or targets",
        },
        source_urls=[u for u in source_urls if u],
        symbols=all_symbols[:200],
    )
    await session.commit()

    log.info(
        "market_signals.persisted",
        post_id=post_id[:8],
        words=post.word_count,
        symbols=len(all_symbols),
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok",
        "slug": subblock["slug"],
        "post_id": post_id,
        "word_count": post.word_count,
        "symbols": len(all_symbols),
    }
'''


RUN_ONCE_SRC = '''"""Manually run the market_signals generator once against the dev DB."""
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
        print("Running market_signals generator…\\n")
        result = await generate_subblock(session, org_id=org_id, slug="market_signals")
        print(f"Result: {result}\\n")

    async with factory() as session:
        await session.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()
        row = (await session.execute(text("""
            SELECT title, body, word_count, symbols, status
            FROM trading_posts
            WHERE org_id = :o
            ORDER BY created_at DESC LIMIT 1
        """), {"o": org_id})).first()
        if row:
            title, body, wc, symbols, status = row
            print("=" * 70)
            print(f"TITLE: {title}")
            print(f"STATUS: {status}")
            print(f"WORDS: {wc}")
            print(f"SYMBOLS: {len(symbols or [])}  e.g. {(symbols or [])[:5]}")
            print("=" * 70)
            print("BODY (first 2000 chars):")
            print((body or "")[:2000])
            print("…")
        else:
            print("❌ no post found")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
'''


def step1_write_generator() -> None:
    print("\n[1/3] Write market_signals generator")
    if MARKET_SIGNALS.exists():
        print(f"  ↷ {MARKET_SIGNALS.name} exists — overwriting")
    MARKET_SIGNALS.write_text(MARKET_SIGNALS_SRC)
    print(f"  ✓ wrote {MARKET_SIGNALS.relative_to(ROOT)}")

    if RUN_ONCE.exists():
        print(f"  ↷ {RUN_ONCE.name} exists — overwriting")
    RUN_ONCE.write_text(RUN_ONCE_SRC)
    print(f"  ✓ wrote {RUN_ONCE.relative_to(ROOT)}")


def step2_add_task() -> None:
    print("\n[2/3] Add Celery task")
    src = TASK_FILE.read_text()
    if "trading_generate_market_signals" in src:
        print("  ↷ task already present")
        return

    addition = '''

@celery_app.task(name="tasks.trading_generate_market_signals", bind=True)
def trading_generate_market_signals(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate market_signals for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = asyncio.run(_generate_for_org(o["id"], "market_signals"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}
'''
    TASK_FILE.write_text(src.rstrip() + addition)
    print(f"  ✓ {TASK_FILE.name}: task added")


def step3_run() -> bool:
    print("\n[3/3] Run market_signals generator once")
    r = subprocess.run(
        ["python", "scripts/run_market_signals_once.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    print(r.stdout[-6000:] if len(r.stdout) > 6000 else r.stdout)
    if r.returncode != 0:
        print("  ❌ failed:")
        print(r.stderr[-1500:])
        return False
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.3.2: market_signals generator")
    print("=" * 70)

    step1_write_generator()
    step2_add_task()
    ok = step3_run()

    print("=" * 70)
    if ok:
        print("✅ T.3.2 complete — second scraper subblock ships")
        sys.exit(0)
    print("❌ T.3.2 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
