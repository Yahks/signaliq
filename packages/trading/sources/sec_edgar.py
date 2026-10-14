"""SEC EDGAR XBRL client.

Free, official, no scraping. SEC requires a declared User-Agent
per https://www.sec.gov/os/webmaster-faq#developers.

Rate limit: stay under 10 req/s. We cache the CIK map for 24h and
each company's facts for 1h.
"""
from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from typing import Any

import httpx

from packages.core.logging import get_logger

log = get_logger(__name__)

SEC_BASE = "https://data.sec.gov"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DEFAULT_TIMEOUT = 15.0
USER_AGENT = "SignalIQ/0.1 (contact: ops@signaliq.local)"

CIK_CACHE_TTL = 86400
FACTS_CACHE_TTL = 3600
REDIS_DB = 3

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


async def _load_cik_map() -> dict[str, int]:
    r = _get_redis()
    if r is not None:
        try:
            cached = r.get("sec:cik_map")
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(TICKERS_URL, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("sec.tickers_fetch_failed", error=str(exc)[:200])
        return {}

    out: dict[str, int] = {}
    for entry in (data or {}).values():
        t = (entry.get("ticker") or "").strip().upper()
        c = entry.get("cik_str")
        if t and c:
            out[t] = int(c)

    if r is not None:
        try:
            r.setex("sec:cik_map", CIK_CACHE_TTL, _json.dumps(out))
        except Exception:
            pass
    log.info("sec.cik_map_loaded", count=len(out))
    return out


async def _cik_for(symbol: str) -> int | None:
    cmap = await _load_cik_map()
    return cmap.get(symbol.strip().upper())


# Each metric may live under several XBRL tags across a company's history
# (Apple moved from Revenues → RevenueFromContractWithCustomer... in 2019).
# We try the tags in order and use the first that returns a recent 10-K.
#
# (label, [tag1, tag2, tag3], unit_key)
METRICS: list[tuple[str, list[str], str]] = [
    ("Revenue (FY)", [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ], "USD"),
    ("Net Income (FY)", ["NetIncomeLoss"], "USD"),
    ("Operating Income (FY)", ["OperatingIncomeLoss"], "USD"),
    ("EPS (Diluted, FY)", ["EarningsPerShareDiluted"], "USD/shares"),
    ("Shareholders' Equity", ["StockholdersEquity"], "USD"),
    ("Total Assets", ["Assets"], "USD"),
    ("Total Liabilities", ["Liabilities"], "USD"),
    ("Long-Term Debt", [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ], "USD"),
    ("Cash & Equivalents", [
        "CashAndCashEquivalentsAtCarryingValue",
    ], "USD"),
]


def _annual_only(units: list[dict]) -> list[dict]:
    annual = [
        e for e in units
        if (e.get("form") or "").startswith("10-K")
        and e.get("val") is not None
        and e.get("end")
    ]
    annual.sort(key=lambda e: e.get("end") or "", reverse=True)
    return annual


async def _try_tag(cik: int, tag: str, unit_key: str) -> dict | None:
    """Fetch one tag. Return the latest annual entry or None."""
    url = f"{SEC_BASE}/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT})
            if resp.status_code != 200:
                return None
            d = resp.json()
    except Exception:
        return None

    units = (d.get("units") or {}).get(unit_key, [])
    annual = _annual_only(units)
    if not annual:
        return None

    latest = annual[0]
    return {
        "val": latest.get("val"),
        "fy": latest.get("fy"),
        "fp": latest.get("fp"),
        "end": latest.get("end"),
        "entityName": d.get("entityName"),
        "tag_used": tag,
    }


async def fetch_company_facts(symbol: str) -> dict[str, Any] | None:
    """Fetch SEC XBRL financial data for one symbol.

    Returns {symbol, name, metrics: {Label: Value}, source, fetched_at}.
    Never raises.
    """
    cik = await _cik_for(symbol)
    if not cik:
        log.warning("sec.no_cik", symbol=symbol)
        return None

    cache_key = f"sec:facts:v3:{cik}"  # v3 — best-of-tags per metric
    r = _get_redis()
    if r is not None:
        try:
            cached = r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    metrics: dict[str, str] = {}
    entity_name: str | None = None

    for label, tags, unit_key in METRICS:
        # Try EVERY tag and pick the one with the most recent annual
        # end date. A single tag can lag: NVDA's
        # RevenueFromContractWithCustomer... shows FY2022 while the
        # same period is on another tag with a FY2026 end.
        hit = None
        for tag in tags:
            candidate = await _try_tag(cik, tag, unit_key)
            if not candidate:
                continue
            if hit is None or (candidate.get("end") or "") > (hit.get("end") or ""):
                hit = candidate
        if not hit:
            continue
        if entity_name is None:
            entity_name = hit.get("entityName")
        val = hit["val"]
        fy = hit["fy"]
        end = hit["end"]
        # Format value with locale-style commas for large numbers.
        if unit_key == "USD" and isinstance(val, (int, float)) and abs(val) >= 1000:
            val_str = f"{val:,.0f}"
        else:
            val_str = str(val)
        metrics[label] = f"{val_str} (FY{fy}, {end})"

    if not metrics:
        log.warning("sec.no_metrics", symbol=symbol, cik=cik)
        return None

    result = {
        "symbol": symbol.upper(),
        "name": entity_name,
        "metrics": metrics,
        "source": "SEC EDGAR (XBRL)",
        "fetched_at": datetime.now(UTC).isoformat(),
    }

    if r is not None:
        try:
            r.setex(cache_key, FACTS_CACHE_TTL, _json.dumps(result))
        except Exception:
            pass

    log.info("sec.fetched", symbol=symbol, cik=cik, metrics=len(metrics))
    return result


# ─────────────────────────────────────────────────────────────────────
# Submissions API — filing history
# ─────────────────────────────────────────────────────────────────────

async def fetch_filings(symbol: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent SEC filings for a symbol.

    Reads data.sec.gov/submissions/CIK{cik}.json and pulls the
    "recent" filings array. Returns up to `limit` entries, newest
    first (the API returns them in that order).

    Each entry:
      {form, date, report_date, description, accession}

    Never raises. Returns [] on any failure.
    """
    cik = await _cik_for(symbol)
    if not cik:
        log.warning("sec.no_cik_for_filings", symbol=symbol)
        return []

    cache_key = f"sec:filings:{cik}:{limit}"
    r = _get_redis()
    if r is not None:
        try:
            cached = r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    url = f"{SEC_BASE}/submissions/CIK{cik:010d}.json"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT})
            if resp.status_code != 200:
                log.warning("sec.submissions_http", symbol=symbol,
                            status=resp.status_code)
                return []
            d = resp.json()
    except Exception as exc:
        log.warning("sec.submissions_failed", symbol=symbol, error=str(exc)[:200])
        return []

    recent = (d.get("filings") or {}).get("recent") or {}
    forms = recent.get("form", []) or []
    dates = recent.get("filingDate", []) or []
    reports = recent.get("reportDate", []) or []
    descriptions = recent.get("primaryDocDescription", []) or []
    accessions = recent.get("accessionNumber", []) or []

    out: list[dict[str, Any]] = []
    for i in range(min(len(forms), limit)):
        out.append({
            "form": forms[i] if i < len(forms) else "",
            "date": dates[i] if i < len(dates) else "",
            "report_date": reports[i] if i < len(reports) else "",
            "description": descriptions[i] if i < len(descriptions) else "",
            "accession": accessions[i] if i < len(accessions) else "",
        })

    if r is not None:
        try:
            r.setex(cache_key, FACTS_CACHE_TTL, _json.dumps(out))
        except Exception:
            pass

    log.info("sec.filings_fetched", symbol=symbol, count=len(out))
    return out
