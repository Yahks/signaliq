"""
SignalIQ — Phase T.5.5: fundamental_metrics generator (the last one).

Uses StockAnalysis.com's SvelteKit __data.json sidecar endpoint:
  GET https://stockanalysis.com/stocks/{symbol}/statistics/__data.json

The response is a devalue-serialized index graph. We resolve it to
structured objects, then extract metrics from each section (valuation,
ratios, financialEfficiency, margins, etc.).

One post per company, from a rotating universe of top US tickers.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "packages" / "trading" / "sources"
SUBBLOCKS_DIR = ROOT / "packages" / "trading" / "subblocks"
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "trading.py"


# ─────────────────────────────────────────────────────────────────────
# 1. Source: stockanalysis_fundamentals.py
# ─────────────────────────────────────────────────────────────────────

SOURCE_SRC = '''"""StockAnalysis.com fundamentals fetcher.

Uses the SvelteKit __data.json sidecar endpoint that powers the
/stocks/{symbol}/statistics/ page. Returns pre-structured metrics.

Response format is devalue-serialized: a top-level `data` array where
every object/list/string is stored once and referenced by integer index.
We resolve the graph, then pull metrics from named sections.

Redis-cached for 1 hour. Never raises.
"""
from __future__ import annotations

import json as _json
from typing import Any

import httpx

from packages.core.logging import get_logger

log = get_logger(__name__)

BASE_URL = "https://stockanalysis.com/stocks/{symbol}/statistics/__data.json"
DEFAULT_TIMEOUT = 20.0
CACHE_TTL_SECONDS = 3600
REDIS_DB = 3
USER_AGENT = "SignalIQ/0.1 (+contact: ops@signaliq.local)"

_redis_client: Any = None


def _get_redis() -> Any:
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            from packages.core.config import get_settings
            s = get_settings()
            url = getattr(s, "redis_url", "redis://localhost:6379/0")
            base = url.rsplit("/", 1)[0] if "/" in url else url
            _redis_client = redis.Redis.from_url(f"{base}/{REDIS_DB}", decode_responses=True)
        except Exception:
            _redis_client = None
    return _redis_client


# ─────────────────────────────────────────────────────────────────────
# Devalue resolver
# ─────────────────────────────────────────────────────────────────────

def _resolve(data: list, value: Any, seen: set | None = None) -> Any:
    """Resolve a devalue index graph to structured objects.

    - int → look up data[int] recursively
    - dict → resolve each value
    - list → resolve each item
    - other → return as-is

    Guards against cycles and out-of-range indices.
    """
    if seen is None:
        seen = set()
    if isinstance(value, int):
        if value in seen or value < 0 or value >= len(data):
            return None
        return _resolve(data, data[value], seen | {value})
    if isinstance(value, dict):
        return {k: _resolve(data, v, seen) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(data, v, seen) for v in value]
    return value


# ─────────────────────────────────────────────────────────────────────
# Metric extraction
# ─────────────────────────────────────────────────────────────────────

# Which sections we care about and their human labels
SECTIONS = [
    ("valuation",           "Valuation"),
    ("ratios",              "Ratios"),
    ("evRatios",            "EV Ratios"),
    ("financialEfficiency", "Efficiency"),
    ("margins",             "Margins"),
    ("incomeStatement",     "Income Statement"),
    ("balanceSheet",        "Balance Sheet"),
    ("dividends",           "Dividends"),
]

# Metrics we want to keep (id → human label)
WANTED_IDS = {
    "marketcap":              "Market Cap",
    "enterpriseValue":        "Enterprise Value",
    "pe":                     "PE Ratio",
    "peForward":              "Forward PE",
    "ps":                     "PS Ratio",
    "pb":                     "PB Ratio",
    "pfcf":                   "P/FCF Ratio",
    "pegRatio":               "PEG Ratio",
    "evEbitda":               "EV/EBITDA",
    "evSales":                "EV/Sales",
    "roe":                    "ROE",
    "roa":                    "ROA",
    "roic":                   "ROIC",
    "grossMargin":            "Gross Margin",
    "operatingMargin":        "Operating Margin",
    "netMargin":              "Net Margin",
    "profitMargin":           "Profit Margin",
    "revenue":                "Revenue (TTM)",
    "netIncome":              "Net Income (TTM)",
    "eps":                    "EPS (Diluted)",
    "dividendYield":          "Dividend Yield",
    "payoutRatio":            "Payout Ratio",
    "totalDebt":              "Total Debt",
    "totalCash":              "Total Cash",
    "debtEquity":             "Debt/Equity",
    "currentRatio":           "Current Ratio",
    "quickRatio":             "Quick Ratio",
    "employees":              "Employees",
}


def _extract_metrics(root: dict, data: list) -> dict[str, str]:
    """Walk each section's `data` list, extract {id: value} pairs.

    Returns a flat dict: {"Market Cap": "4.91T", "PE Ratio": "38.56", ...}
    """
    out: dict[str, str] = {}

    for section_key, _ in SECTIONS:
        section = root.get(section_key)
        if not isinstance(section, dict):
            continue
        data_ref = section.get("data")
        if data_ref is None:
            continue
        resolved = _resolve(data, data_ref)
        if not isinstance(resolved, list):
            continue
        for item in resolved:
            if not isinstance(item, dict):
                continue
            mid = item.get("id")
            val = item.get("value")
            if mid and val is not None and mid in WANTED_IDS:
                out[WANTED_IDS[mid]] = str(val)

    return out


async def fetch_fundamentals(symbol: str) -> dict[str, Any] | None:
    """Fetch + resolve fundamentals for one symbol.

    Returns {
      "symbol": "AAPL",
      "name": "Apple Inc.",
      "metrics": {"Market Cap": "4.91T", "PE Ratio": "38.56", ...},
      "sections": {...raw...},
    }
    or None on any failure.
    """
    sym = symbol.lower().strip()
    cache_key = f"sa:fundamentals:{sym}"
    r = _get_redis()
    if r is not None:
        try:
            cached = r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    url = BASE_URL.format(symbol=sym)
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT})
            if resp.status_code != 200:
                log.warning("sa_fund.http_error", symbol=sym, status=resp.status_code)
                return None
            payload = resp.json()
    except Exception as exc:
        log.warning("sa_fund.fetch_failed", symbol=sym, error=str(exc)[:200])
        return None

    try:
        nodes = payload.get("nodes") or []
        # Find the node with our stats data (has a 'data' list of length > 100
        # containing a root object with 'valuation' key)
        stats_node = None
        for n in nodes:
            d = n.get("data")
            if isinstance(d, list) and len(d) > 50:
                if isinstance(d[0], dict) and "valuation" in d[0]:
                    stats_node = d
                    break
        if stats_node is None:
            log.warning("sa_fund.no_stats_node", symbol=sym)
            return None

        root = _resolve(stats_node, 0)
        metrics = _extract_metrics(root, stats_node)

        # Grab symbol/name from a sibling node if present
        symbol_name = sym.upper()
        company_name = None
        for n in nodes:
            d = n.get("data")
            if isinstance(d, list) and len(d) >= 7:
                if isinstance(d[0], dict) and "info" in d[0]:
                    resolved = _resolve(d, 0)
                    info = resolved.get("info", {}) if isinstance(resolved, dict) else {}
                    if isinstance(info, dict):
                        symbol_name = info.get("ticker") or symbol_name
                        company_name = info.get("nameFull") or info.get("name")
                    break

        result = {
            "symbol": symbol_name,
            "name": company_name,
            "metrics": metrics,
            "fetched_at": _utc_iso(),
        }

        log.info("sa_fund.fetched", symbol=sym, metrics=len(metrics))
        if r is not None:
            try:
                r.setex(cache_key, CACHE_TTL_SECONDS, _json.dumps(result))
            except Exception:
                pass
        return result

    except Exception as exc:
        log.warning("sa_fund.parse_failed", symbol=sym, error=str(exc)[:200])
        return None


def _utc_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()
'''


# ─────────────────────────────────────────────────────────────────────
# 2. Generator: fundamental_metrics.py
# ─────────────────────────────────────────────────────────────────────

GENERATOR_SRC = '''"""fundamental_metrics: one company per post, from StockAnalysis stats."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.trading.sources.stockanalysis_fundamentals import fetch_fundamentals

from .base import call_llm_json_verified, persist_post, render_markdown_table
from ._llm_shared import pick_next_topic

log = get_logger(__name__)

# Rotating universe of top US tickers
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "BRK-B",
    "LLY", "JPM", "WMT", "V", "AVGO", "XOM", "UNH", "MA", "COST",
    "JNJ", "HD", "PG", "NFLX", "ADBE", "CRM", "AMD", "PEP", "KO",
    "BAC", "TMO", "ABT", "ORCL", "ACN", "MRK", "CVX", "WFC", "DIS",
    "CSCO", "INTC", "QCOM", "TXN", "VZ", "PFE", "NKE", "MCD", "PM",
    "RTX", "HON", "UNP", "IBM", "CAT", "GS",
]


async def generate(session: AsyncSession, *, org_id: str, subblock: dict[str, Any]) -> dict[str, Any] | None:
    # Adapt subblock's training_config to have a topic_pool so pick_next_topic works
    cfg = dict(subblock.get("training_config") or {})
    if "topic_pool" not in cfg:
        cfg["topic_pool"] = UNIVERSE
    subblock_with_pool = {**subblock, "training_config": cfg}

    symbol = await pick_next_topic(session, org_id=org_id, subblock=subblock_with_pool)
    if not symbol:
        log.warning("fundamental_metrics.no_symbol", org_id=org_id)
        return {"status": "no_symbol", "slug": subblock["slug"]}

    data = await fetch_fundamentals(symbol)
    if not data or not data.get("metrics"):
        log.warning("fundamental_metrics.no_data", symbol=symbol)
        return {"status": "no_data", "slug": subblock["slug"], "symbol": symbol}

    metrics = data["metrics"]
    name = data.get("name") or symbol

    # Render as a two-column table
    rows = [{"metric": k, "value": v} for k, v in metrics.items()]
    payload = render_markdown_table(
        rows, ["metric", "value"],
        title=f"{name} ({symbol}) — Fundamental Metrics",
    )

    prompt = (
        f"Fundamental metrics for {name} ({symbol}) from StockAnalysis.com.\\n\\n"
        f"{payload}\\n\\n"
        f"Write a brief fundamental snapshot. Structure:\\n"
        f"1. Headline: one sentence on what the valuation and quality metrics show.\\n"
        f"2. Valuation: cite the PE, PS, PB values in context.\\n"
        f"3. Quality: cite the ROE, ROA, ROIC values and any margins.\\n"
        f"4. One-sentence caveat: fundamental ratios are historical and lag the market.\\n\\n"
        f"Use ONLY the numbers in the table above.\\n"
        f"Do NOT invent metrics, comparisons to peers, or forecasts.\\n"
        f"No causal language ('driven by', 'because of', 'reflecting').\\n"
        f"~180 words. Do NOT give buy/sell advice."
    )

    post = await call_llm_json_verified(
        session, org_id=org_id,
        system=subblock.get("style_guide") or "You write equity fundamentals summaries.",
        user=prompt,
        prompt_name="trading.fundamental_metrics",
        source_payload={"symbol": symbol, "name": name, "metrics": metrics},
        target_words=180,
    )

    post_id = await persist_post(
        session, org_id=org_id, subblock=subblock, post=post,
        source_payload={"symbol": symbol, "name": name, "metrics": metrics,
                        "source": "stockanalysis.com"},
        source_urls=[f"https://stockanalysis.com/stocks/{symbol.lower()}/statistics/"],
        symbols=[symbol],
    )
    await session.commit()

    log.info(
        "fundamental_metrics.persisted",
        post_id=post_id[:8], symbol=symbol, metrics=len(metrics),
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok", "slug": subblock["slug"],
        "post_id": post_id, "symbol": symbol, "word_count": post.word_count,
    }
'''


def step1_write_source() -> None:
    print("\n[1/4] Write fundamentals source")
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    (SOURCES_DIR / "stockanalysis_fundamentals.py").write_text(SOURCE_SRC)
    print("  ✓ wrote packages/trading/sources/stockanalysis_fundamentals.py")


def step2_write_generator() -> None:
    print("\n[2/4] Write generator")
    (SUBBLOCKS_DIR / "fundamental_metrics.py").write_text(GENERATOR_SRC)
    print("  ✓ wrote packages/trading/subblocks/fundamental_metrics.py")


def step3_add_task_and_map() -> None:
    print("\n[3/4] Add Celery task + slug map entry")

    src = TASK_FILE.read_text()
    if "trading_generate_fundamental_metrics" not in src:
        block = '''

@celery_app.task(name="tasks.trading_generate_fundamental_metrics", bind=True)
def trading_generate_fundamental_metrics(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate fundamental_metrics for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = asyncio.run(_generate_for_org(o["id"], "fundamental_metrics"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}
'''
        TASK_FILE.write_text(src.rstrip() + block)
        print("  ✓ Celery task added")

    trading_router = ROOT / "apps" / "api" / "routers" / "trading.py"
    src = trading_router.read_text()
    entry = '    "fundamental_metrics": "tasks.trading_generate_fundamental_metrics",'
    if entry in src:
        print("  ↷ slug map already has fundamental_metrics")
    else:
        anchor = '    "credit_risk": "tasks.trading_generate_credit_risk",'
        if anchor not in src:
            raise SystemExit("❌ slug map anchor not found")
        trading_router.write_text(src.replace(anchor, anchor + "\n" + entry, 1))
        print("  ✓ slug map updated")


def step4_test() -> bool:
    print("\n[4/4] Test fetch_fundamentals + generator")

    r = subprocess.run([
        "python", "-c", """
import asyncio, sys, json
sys.path.insert(0, '.')
from packages.trading.sources.stockanalysis_fundamentals import fetch_fundamentals

async def main():
    data = await fetch_fundamentals('AAPL')
    if not data:
        print('FAIL: fetch_fundamentals returned None')
        return
    print(f'symbol: {data[\"symbol\"]}')
    print(f'name:   {data[\"name\"]}')
    print(f'metrics: {len(data[\"metrics\"])}')
    for k, v in list(data['metrics'].items())[:12]:
        print(f'  {k:20} {v}')

asyncio.run(main())
        """.strip()
    ], cwd=ROOT, capture_output=True, text=True, timeout=60)
    print(r.stdout)
    if r.returncode != 0 or "FAIL" in r.stdout:
        print(r.stderr[-800:])
        return False

    print()
    r2 = subprocess.run([
        "python", "-c", """
import asyncio, sys
sys.path.insert(0, '.')
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.trading.subblocks.base import generate_subblock

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        org = (await session.execute(text(
            "SELECT id::text FROM orgs WHERE slug = 'aardesst'"
        ))).first()
    org_id = org[0]
    async with factory() as session:
        await session.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()
        result = await generate_subblock(session, org_id=org_id, slug='fundamental_metrics')
        print(f'result: {result}')
    await engine.dispose()

asyncio.run(main())
        """.strip()
    ], cwd=ROOT, capture_output=True, text=True, timeout=180)
    print(r2.stdout)
    if r2.returncode != 0:
        print(r2.stderr[-1500:])
        return False
    return "status': 'ok'" in r2.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.5.5: fundamental_metrics (final subblock)")
    print("=" * 70)

    step1_write_source()
    step2_write_generator()
    step3_add_task_and_map()
    ok = step4_test()

    print("=" * 70)
    if ok:
        print("✅ T.5.5 complete — ALL 16 SUBCLOCKS SHIP")
        sys.exit(0)
    print("❌ T.5.5 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
