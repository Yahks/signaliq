"""
SignalIQ — Phase T.5.2: Yahoo market data generators.

Four subblocks that use market_data.get_quote_cached(symbol):

  - market_analysis    : indices + sectors overview
  - market_condition   : regime classification (VIX, rates, credit, dollar)
  - market_cap         : top caps with current prices
  - asset_classes      : cross-asset performance table
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBBLOCKS_DIR = ROOT / "packages" / "trading" / "subblocks"
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "trading.py"


# ─────────────────────────────────────────────────────────────────────
# Shared Yahoo helper
# ─────────────────────────────────────────────────────────────────────

YAHOO_SHARED_SRC = '''"""Shared helper for Yahoo-backed trading subblocks.

Wraps packages.ingestion.sources.market_cache.get_quote_cached with:
  - parallel-ish sequential fetching (rate-limited by the cache layer)
  - graceful per-symbol failure (empty entry, not raise)
  - consistent output shape for prompt rendering
"""
from __future__ import annotations

from typing import Any

from packages.core.logging import get_logger

log = get_logger(__name__)


async def fetch_quotes(symbols: list[str]) -> list[dict[str, Any]]:
    """Fetch quotes for the given symbols. Failures become None fields.

    Returns list of dicts with stable keys:
      symbol, price, change_pct, high_30d, low_30d, vol_30d, name
    """
    from packages.ingestion.sources.market_cache import get_quote_cached

    out: list[dict[str, Any]] = []
    for sym in symbols:
        try:
            q = await get_quote_cached(sym, force_refresh=False)
        except Exception as exc:
            log.warning("yahoo.fetch_failed", symbol=sym, error=str(exc)[:200])
            q = None

        if q is None:
            out.append({
                "symbol": sym, "price": None, "change_pct": None,
                "high_30d": None, "low_30d": None, "vol_30d": None, "name": None,
            })
            continue

        out.append({
            "symbol": sym,
            "name": getattr(q, "name", None),
            "price": getattr(q, "latest_price", None),
            "change_pct": getattr(q, "daily_change_pct", None),
            "high_30d": getattr(q, "high_30d", None),
            "low_30d": getattr(q, "low_30d", None),
            "vol_30d": getattr(q, "volatility_30d_pct", None),
        })

    ok = sum(1 for r in out if r["price"] is not None)
    log.info("yahoo.fetched", requested=len(symbols), ok=ok)
    return out


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def fmt_num(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}"
'''


# ─────────────────────────────────────────────────────────────────────
# 4 generators
# ─────────────────────────────────────────────────────────────────────

def _gen_market_analysis() -> str:
    return '''"""market_analysis: index + sector overview from Yahoo quotes."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from .base import call_llm_json_verified, persist_post, render_markdown_table
from ._yahoo_shared import fetch_quotes, fmt_pct

log = get_logger(__name__)

INDICES = ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX"]
SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC"]


async def generate(session: AsyncSession, *, org_id: str, subblock: dict[str, Any]) -> dict | None:
    quotes = await fetch_quotes(INDICES + SECTORS)
    if not quotes or not any(q["price"] is not None for q in quotes):
        return {"status": "no_data", "slug": subblock["slug"]}

    idx_rows = [q for q in quotes if q["symbol"] in INDICES]
    sec_rows = [q for q in quotes if q["symbol"] in SECTORS]
    # Sort sectors by change
    sec_rows.sort(key=lambda q: (q["change_pct"] is None, -(q["change_pct"] or 0)))

    payload = (
        render_markdown_table(
            [{"symbol": q["symbol"], "name": q["name"] or q["symbol"],
              "price": q["price"], "change": fmt_pct(q["change_pct"])} for q in idx_rows],
            ["symbol", "name", "price", "change"],
            title="Indices",
        )
        + "\\n\\n"
        + render_markdown_table(
            [{"symbol": q["symbol"], "name": q["name"] or q["symbol"],
              "change": fmt_pct(q["change_pct"])} for q in sec_rows],
            ["symbol", "name", "change"],
            title="Sector ETFs (sorted by % change, best first)",
        )
    )

    prompt = (
        f"Today's market snapshot from Yahoo Finance.\\n\\n{payload}\\n\\n"
        f"Write a brief market analysis. Structure:\\n"
        f"1. Headline: where did the indices close today (1-2 sentences)\\n"
        f"2. Index summary: which moved and by how much\\n"
        f"3. Sector rotation: which sectors led, which lagged — cite the % changes\\n"
        f"4. One sentence: what the rotation suggests about risk appetite\\n\\n"
        f"Use ONLY the numbers in the tables. Do NOT invent causes or news.\\n"
        f"Do NOT write 'driven by', 'due to', 'after', 'reflecting'.\\n"
        f"~150 words of prose. Do NOT give buy/sell advice."
    )

    post = await call_llm_json_verified(
        session, org_id=org_id,
        system=subblock.get("style_guide") or "You write market analysis.",
        user=prompt,
        prompt_name="trading.market_analysis",
        source_payload={"indices": idx_rows, "sectors": sec_rows},
        target_words=150,
    )

    post_id = await persist_post(
        session, org_id=org_id, subblock=subblock, post=post,
        source_payload={"indices": idx_rows, "sectors": sec_rows},
        source_urls=["https://finance.yahoo.com/"],
        symbols=INDICES + SECTORS,
    )
    await session.commit()
    log.info("market_analysis.persisted", post_id=post_id[:8], words=post.word_count)
    return {"status": "ok", "slug": subblock["slug"], "post_id": post_id, "word_count": post.word_count}
'''


def _gen_market_condition() -> str:
    return '''"""market_condition: regime classification from VIX, rates, credit, dollar."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from .base import call_llm_json_verified, persist_post, render_markdown_table
from ._yahoo_shared import fetch_quotes, fmt_pct

log = get_logger(__name__)

SIGNALS = ["^VIX", "^VIX3M", "^TNX", "DX-Y.NYB", "^GSPC", "HYG", "LQD"]


async def generate(session: AsyncSession, *, org_id: str, subblock: dict[str, Any]) -> dict | None:
    quotes = await fetch_quotes(SIGNALS)
    if not quotes or not any(q["price"] is not None for q in quotes):
        return {"status": "no_data", "slug": subblock["slug"]}

    payload = render_markdown_table(
        [{"symbol": q["symbol"], "name": q["name"] or q["symbol"],
          "price": q["price"], "change": fmt_pct(q["change_pct"]),
          "vol_30d": q["vol_30d"]} for q in quotes],
        ["symbol", "name", "price", "change", "vol_30d"],
        title="Regime signals",
    )

    prompt = (
        f"Today's market regime signals from Yahoo Finance.\\n\\n{payload}\\n\\n"
        f"Write a brief market-condition read. Structure:\\n"
        f"1. Regime classification: trending / chopping / risk-on / risk-off — pick one per signal\\n"
        f"2. Evidence: cite specific levels and % changes from the table\\n"
        f"3. What would change the classification: one sentence\\n\\n"
        f"Use ONLY the numbers above. Do NOT invent causes or news.\\n"
        f"No causal language ('driven by', 'due to', 'reflecting').\\n"
        f"~150 words. Do NOT give buy/sell advice."
    )

    post = await call_llm_json_verified(
        session, org_id=org_id,
        system=subblock.get("style_guide") or "You classify market regimes.",
        user=prompt,
        prompt_name="trading.market_condition",
        source_payload={"signals": quotes},
        target_words=150,
    )

    post_id = await persist_post(
        session, org_id=org_id, subblock=subblock, post=post,
        source_payload={"signals": quotes},
        source_urls=["https://finance.yahoo.com/"],
        symbols=SIGNALS,
    )
    await session.commit()
    log.info("market_condition.persisted", post_id=post_id[:8], words=post.word_count)
    return {"status": "ok", "slug": subblock["slug"], "post_id": post_id, "word_count": post.word_count}
'''


def _gen_market_cap() -> str:
    return '''"""market_cap: top-cap names with current prices from Yahoo."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from .base import call_llm_json_verified, persist_post, render_markdown_table
from ._yahoo_shared import fetch_quotes, fmt_pct

log = get_logger(__name__)

# Frozen list of 20 largest US equities (updated manually, ~quarterly)
TOP_CAP = [
    "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "BRK-B",
    "LLY", "JPM", "WMT", "V", "AVGO", "XOM", "UNH", "MA", "COST",
    "JNJ", "HD", "PG",
]


async def generate(session: AsyncSession, *, org_id: str, subblock: dict[str, Any]) -> dict | None:
    quotes = await fetch_quotes(TOP_CAP)
    if not quotes or not any(q["price"] is not None for q in quotes):
        return {"status": "no_data", "slug": subblock["slug"]}

    sorted_q = sorted(quotes, key=lambda q: -(q["price"] or 0))
    payload = render_markdown_table(
        [{"symbol": q["symbol"], "name": q["name"] or q["symbol"],
          "price": q["price"], "change": fmt_pct(q["change_pct"])} for q in quotes],
        ["symbol", "name", "price", "change"],
        title="Top US equities by market cap (with today's price and change)",
    )

    prompt = (
        f"Today's top US equities by market cap.\\n\\n{payload}\\n\\n"
        f"Write a brief note on today's cap-table leadership. Structure:\\n"
        f"1. Which names moved most today (top 3-5 by absolute % change, up or down)\\n"
        f"2. Range of moves across the group\\n"
        f"3. One sentence caveat about single-day moves.\\n\\n"
        f"Use ONLY the numbers above. Do NOT invent market caps, news, or causes.\\n"
        f"~150 words. Do NOT give buy/sell advice."
    )

    post = await call_llm_json_verified(
        session, org_id=org_id,
        system=subblock.get("style_guide") or "You summarize market leadership.",
        user=prompt,
        prompt_name="trading.market_cap",
        source_payload={"quotes": quotes},
        target_words=150,
    )

    post_id = await persist_post(
        session, org_id=org_id, subblock=subblock, post=post,
        source_payload={"quotes": quotes, "universe": TOP_CAP},
        source_urls=["https://finance.yahoo.com/"],
        symbols=TOP_CAP,
    )
    await session.commit()
    log.info("market_cap.persisted", post_id=post_id[:8], words=post.word_count)
    return {"status": "ok", "slug": subblock["slug"], "post_id": post_id, "word_count": post.word_count}
'''


def _gen_asset_classes() -> str:
    return '''"""asset_classes: cross-asset performance table."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from .base import call_llm_json_verified, persist_post, render_markdown_table
from ._yahoo_shared import fetch_quotes, fmt_pct

log = get_logger(__name__)

ASSET_CLASSES = {
    "Equities (US)":  ["^GSPC", "^IXIC", "^RUT"],
    "Bonds":          ["^TNX", "TLT", "IEF", "SHY"],
    "Commodities":    ["GC=F", "CL=F", "SI=F", "NG=F"],
    "FX":             ["DX-Y.NYB", "EURUSD=X", "JPY=X"],
    "Crypto":         ["BTC-USD", "ETH-USD"],
}


async def generate(session: AsyncSession, *, org_id: str, subblock: dict[str, Any]) -> dict | None:
    all_syms = [s for syms in ASSET_CLASSES.values() for s in syms]
    quotes = await fetch_quotes(all_syms)
    if not quotes or not any(q["price"] is not None for q in quotes):
        return {"status": "no_data", "slug": subblock["slug"]}

    by_symbol = {q["symbol"]: q for q in quotes}
    sections: list[str] = []
    for cls_name, syms in ASSET_CLASSES.items():
        rows = [by_symbol.get(s) for s in syms]
        rows = [r for r in rows if r]
        sections.append(render_markdown_table(
            [{"symbol": q["symbol"], "name": q["name"] or q["symbol"],
              "price": q["price"], "change": fmt_pct(q["change_pct"])} for q in rows],
            ["symbol", "name", "price", "change"],
            title=cls_name,
        ))

    payload = "\\n\\n".join(sections)

    prompt = (
        f"Today's cross-asset performance.\\n\\n{payload}\\n\\n"
        f"Write a brief cross-asset read. Structure:\\n"
        f"1. One-sentence headline on the day's cross-asset theme.\\n"
        f"2. One short paragraph per asset class (equities, bonds, commodities, FX, crypto).\\n"
        f"3. One sentence: what the cross-asset pattern shows about positioning.\\n\\n"
        f"Use ONLY numbers above. Do NOT invent causes. No causal language.\\n"
        f"~200 words. Do NOT give buy/sell advice."
    )

    post = await call_llm_json_verified(
        session, org_id=org_id,
        system=subblock.get("style_guide") or "You write cross-asset summaries.",
        user=prompt,
        prompt_name="trading.asset_classes",
        source_payload={"quotes": quotes, "classes": list(ASSET_CLASSES.keys())},
        target_words=200,
    )

    post_id = await persist_post(
        session, org_id=org_id, subblock=subblock, post=post,
        source_payload={"quotes": quotes, "classes": list(ASSET_CLASSES.keys())},
        source_urls=["https://finance.yahoo.com/"],
        symbols=all_syms,
    )
    await session.commit()
    log.info("asset_classes.persisted", post_id=post_id[:8], words=post.word_count)
    return {"status": "ok", "slug": subblock["slug"], "post_id": post_id, "word_count": post.word_count}
'''


YAHOO_GENERATORS = [
    ("market_analysis",  _gen_market_analysis),
    ("market_condition", _gen_market_condition),
    ("market_cap",       _gen_market_cap),
    ("asset_classes",    _gen_asset_classes),
]


def step1_write_shared() -> None:
    print("\n[1/4] Write _yahoo_shared.py")
    (SUBBLOCKS_DIR / "_yahoo_shared.py").write_text(YAHOO_SHARED_SRC)
    print("  ✓ wrote _yahoo_shared.py")


def step2_write_generators() -> None:
    print("\n[2/4] Write 4 Yahoo generators")
    for slug, build in YAHOO_GENERATORS:
        (SUBBLOCKS_DIR / f"{slug}.py").write_text(build())
        print(f"  ✓ wrote {slug}.py")


def step3_add_tasks() -> None:
    print("\n[3/4] Add Celery tasks")
    src = TASK_FILE.read_text()
    added = 0
    for slug, _ in YAHOO_GENERATORS:
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


def step4_update_slug_map() -> None:
    print("\n[4/4] Extend API slug → task map")
    trading_router = ROOT / "apps" / "api" / "routers" / "trading.py"
    src = trading_router.read_text()

    added = 0
    for slug, _ in YAHOO_GENERATORS:
        entry = f'    "{slug}": "tasks.trading_generate_{slug}",'
        if entry in src:
            continue
        # Insert before the closing brace
        marker = '    "trading_tools": "tasks.trading_generate_trading_tools",'
        if marker in src:
            src = src.replace(marker, marker + "\n" + entry, 1)
            added += 1
        else:
            raise SystemExit("❌ slug map anchor not found")

    if added:
        trading_router.write_text(src)
        print(f"  ✓ added {added} slug → task entries")
    else:
        print("  ↷ slug map already updated")


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.5.2: Yahoo market generators (4)")
    print("=" * 70)

    step1_write_shared()
    step2_write_generators()
    step3_add_tasks()
    step4_update_slug_map()

    print("=" * 70)
    print("✅ T.5.2 written — now run each generator once")
    print()
    print("Run this to test:")
    print("  python - << 'PYEOF'")
    print("  ... (see the follow-up command) ...")
    print("  PYEOF")
    print()
    print("Or refresh /trading after triggering via API /generate.")


if __name__ == "__main__":
    main()
