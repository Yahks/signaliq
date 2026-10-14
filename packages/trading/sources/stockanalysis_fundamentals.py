"""StockAnalysis.com fundamentals fetcher.

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
