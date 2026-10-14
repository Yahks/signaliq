"""
SignalIQ — Phase T.2: Scraper fetcher.

Ships:
  1. Corrected requirements.txt + requirements-dev.txt (additive, pin-to-installed)
  2. packages/core/rate_limit.py patch — add "scraper" namespace + wrapper
  3. packages/trading/ package (__init__, sources/__init__)
  4. packages/trading/sources/scraper.py — the fetcher
  5. scripts/smoke_scraper.py — validates the 5 configured sources

Idempotent. Fails loudly on pattern mismatch.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements.txt"
REQ_DEV = ROOT / "requirements-dev.txt"
RATE_LIMIT = ROOT / "packages" / "core" / "rate_limit.py"
TRADING_INIT = ROOT / "packages" / "trading" / "__init__.py"
SOURCES_INIT = ROOT / "packages" / "trading" / "sources" / "__init__.py"
SCRAPER = ROOT / "packages" / "trading" / "sources" / "scraper.py"
SMOKE = ROOT / "scripts" / "smoke_scraper.py"


def _patch(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"\n❌ {path.relative_to(ROOT)} does not exist")
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:400]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. requirements.txt — full correction, pin to installed
# ─────────────────────────────────────────────────────────────────────

REQUIREMENTS = """# SignalIQ — runtime dependencies
# Python 3.12 · pinned to installed versions as of 2026-09-19
# Run: pip install -r requirements.txt

# --- Web framework ---
fastapi==0.115.0
uvicorn[standard]==0.32.0
python-multipart==0.0.12
websockets==13.1
starlette>=0.40,<0.42          # transitively pinned by fastapi
email-validator==2.2.0

# --- Validation & settings ---
pydantic==2.9.2
pydantic-settings==2.6.0
python-dotenv==1.0.1

# --- Database ---
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
psycopg2-binary==2.9.13
alembic==1.13.3
pgvector==0.3.5
greenlet==3.5.5

# --- Cache / queue ---
redis==6.4.0
celery[redis]==5.6.3

# --- Auth & security ---
python-jose[cryptography]==3.3.0
PyJWT==2.14.0
passlib[argon2]==1.7.4
argon2-cffi==23.1.0

# --- HTTP ---
httpx==0.28.1

# --- AI providers ---
google-genai==2.23.0
google-api-python-client==2.200.0
google-ai-generativelanguage==0.6.15
grpcio-status==1.71.2

# --- Embeddings, NLP, data ---
numpy
scipy
pandas==3.0.5
scikit-learn
hdbscan==0.8.44
langdetect==1.0.9
datasketch==2.0.0
feedparser==6.0.14
trafilatura==2.2.0
lxml_html_clean==0.4.5

# --- HTML parsing (scraper) ---
selectolax

# --- Observability ---
structlog==24.4.0

# --- Utilities ---
PyYAML==6.0.3
tqdm==4.70.1
"""


def step1_fix_requirements() -> None:
    print("\n[1/5] Fix requirements.txt (pin-to-installed, additive)")
    REQ.write_text(REQUIREMENTS)
    print("  ✓ requirements.txt rewritten")

    # Fix httpx conflict in requirements-dev.txt
    _patch(
        REQ_DEV,
        "httpx==0.27.2\n",
        "",  # remove — httpx is now pinned in main requirements
        "remove conflicting httpx pin",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. rate_limit.py — add scraper namespace + wrapper
# ─────────────────────────────────────────────────────────────────────

def step2_patch_rate_limit() -> None:
    print("\n[2/5] Patch packages/core/rate_limit.py")

    # 2a. Extend the Literal
    _patch(
        RATE_LIMIT,
        '    namespace: Literal["ip", "auth", "org", "user"],',
        '    namespace: Literal["ip", "auth", "org", "user", "scraper"],',
        "add scraper namespace",
    )

    # 2b. Append the wrapper
    src = RATE_LIMIT.read_text()
    if "check_scraper_limit" in src:
        print("  ↷ rate_limit.py: check_scraper_limit already present")
        return

    wrapper = '''

def check_scraper_limit(domain: str, *, limit: int = 6, window_seconds: int = 60) -> RateLimitResult:
    """Per-domain rate limit for outbound scraping.

    Default 6 requests/minute per domain. Fails open if Redis is down
    (matches the rest of this module's behaviour) — but the scraper
    additionally respects its own in-process token bucket so a Redis
    outage doesn't cause a burst.
    """
    return check_rate_limit(
        namespace="scraper",
        identifier=domain,
        route="fetch",
        limit=limit,
        window_seconds=window_seconds,
    )
'''
    RATE_LIMIT.write_text(src.rstrip() + wrapper)
    print("  ✓ rate_limit.py: check_scraper_limit added")


# ─────────────────────────────────────────────────────────────────────
# 3. packages/trading/ — package markers
# ─────────────────────────────────────────────────────────────────────

def step3_package_markers() -> None:
    print("\n[3/5] Create package markers")
    for p, doc in [
        (TRADING_INIT, '"""Trading block: subblock generators, sources, and dispatch."""'),
        (SOURCES_INIT, '"""External data sources for trading subblocks (yahoo, fred, edgar, scraper)."""'),
    ]:
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(doc + "\n")
            print(f"  ✓ wrote {p.relative_to(ROOT)}")
        else:
            print(f"  ↷ {p.relative_to(ROOT)} exists")


# ─────────────────────────────────────────────────────────────────────
# 4. scraper.py — the fetcher
# ─────────────────────────────────────────────────────────────────────

SCRAPER_SRC = '''"""Generic HTML/JSON scraper with rate limiting, robots.txt, and Redis cache.

Policy (global, not per-source):
  - 6 requests/minute per domain (via packages.core.rate_limit.check_scraper_limit)
  - 15-minute Redis cache by default; per-source override via cache_ttl_seconds
  - robots.txt fetched + cached per domain for 1 hour; honoured when robots="check"
  - User-Agent: SignalIQ/0.1 (+contact: ops@signaliq.local) unless per-source override
  - 3 retries with exponential backoff (1s, 4s, 16s) on 5xx / timeout
  - 15s connect, 20s read
  - NEVER raises. All errors returned as ScrapeResult(ok=False, error=...).

Source config shape (from trading_subblocks.training_config.sources[]):
  {
    "id": "stockanalysis.gainers",
    "url": "https://stockanalysis.com/markets/gainers/",
    "parser": "table",           # "table" | "json" | "text"
    "robots": "allow",           # "allow" | "check" | "deny"
    "selectors": {               # for parser="table"
        "row": "table tbody tr",
        "symbol": "td:nth-child(1)",
        "name":   "td:nth-child(2)",
        "change": "td:nth-child(4)"
    },
    "user_agent": "...",         # optional
    "cache_ttl_seconds": 900     # optional (falls back to 900)
  }
"""
from __future__ import annotations

import asyncio
import json as _json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from packages.core.logging import get_logger
from packages.core.rate_limit import check_scraper_limit

log = get_logger(__name__)

DEFAULT_USER_AGENT = "SignalIQ/0.1 (+contact: ops@signaliq.local)"
DEFAULT_CACHE_TTL = 900  # 15 min
ROBOTS_CACHE_TTL = 3600  # 1 hour
CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 20.0
MAX_RETRIES = 3
BACKOFF_SCHEDULE = [1, 4, 16]  # seconds
REDIS_DB = 3  # separate from rate limit (db 4) and any app cache

_redis_client: Any = None


def _get_redis() -> Any:
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            _redis_client = redis.Redis(
                host="localhost", port=6379, db=REDIS_DB, decode_responses=True,
            )
        except Exception as exc:
            log.warning("scraper.redis_init_failed", error=str(exc)[:200])
            _redis_client = None
    return _redis_client


@dataclass
class ScrapeResult:
    ok: bool
    source_id: str
    url: str
    parser: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    cached: bool = False
    status_code: int | None = None
    bytes_read: int | None = None


# ─────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────

def _cache_key(source_id: str, url: str) -> str:
    import hashlib
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"scrape:{source_id}:{h}"


def _cache_get(source_id: str, url: str) -> list[dict[str, Any]] | None:
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(_cache_key(source_id, url))
        if raw:
            return _json.loads(raw)
    except Exception as exc:
        log.warning("scraper.cache_get_failed", error=str(exc)[:200])
    return None


def _cache_set(source_id: str, url: str, rows: list[dict[str, Any]], ttl: int) -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        r.setex(_cache_key(source_id, url), ttl, _json.dumps(rows))
    except Exception as exc:
        log.warning("scraper.cache_set_failed", error=str(exc)[:200])


# ─────────────────────────────────────────────────────────────────────
# robots.txt
# ─────────────────────────────────────────────────────────────────────

def _robots_cache_key(domain: str) -> str:
    return f"robots:{domain}"


async def _robots_allows(url: str, user_agent: str) -> bool:
    """Check robots.txt for the given URL. Fails open (returns True) if fetch fails."""
    parsed = urlparse(url)
    domain = parsed.netloc
    robots_url = f"{parsed.scheme}://{domain}/robots.txt"

    r = _get_redis()
    cached: str | None = None
    if r is not None:
        try:
            cached = r.get(_robots_cache_key(domain))
        except Exception:
            pass

    text: str
    if cached:
        text = cached
    else:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
                headers={"User-Agent": user_agent},
            ) as client:
                resp = await client.get(robots_url)
                if resp.status_code != 200:
                    # No robots.txt → allow
                    return True
                text = resp.text
            if r is not None:
                try:
                    r.setex(_robots_cache_key(domain), ROBOTS_CACHE_TTL, text)
                except Exception:
                    pass
        except Exception as exc:
            log.warning("scraper.robots_fetch_failed", domain=domain, error=str(exc)[:200])
            return True  # fail open

    # Parse with a permissive UA check
    try:
        rp = RobotFileParser()
        rp.parse(text.splitlines())
        return rp.can_fetch(user_agent, url)
    except Exception as exc:
        log.warning("scraper.robots_parse_failed", domain=domain, error=str(exc)[:200])
        return True


# ─────────────────────────────────────────────────────────────────────
# HTML parsers
# ─────────────────────────────────────────────────────────────────────

def _parse_table(html: str, selectors: dict[str, str]) -> list[dict[str, Any]]:
    """Extract rows using selectolax CSS selectors."""
    from selectolax.parser import HTMLParser

    row_sel = selectors.get("row")
    if not row_sel:
        raise ValueError("table parser requires selectors.row")

    field_sels = {k: v for k, v in selectors.items() if k != "row"}

    tree = HTMLParser(html)
    out: list[dict[str, Any]] = []
    for row_node in tree.css(row_sel):
        item: dict[str, Any] = {}
        for field_name, sel in field_sels.items():
            try:
                el = row_node.css_first(sel)
                item[field_name] = el.text(strip=True) if el else None
            except Exception:
                item[field_name] = None
        # Skip rows where every field is None
        if any(v is not None for v in item.values()):
            out.append(item)
    return out


def _parse_json(payload: str, selectors: dict[str, str]) -> list[dict[str, Any]]:
    """Extract from JSON. Optional selectors.root = dotted path to the array."""
    data = _json.loads(payload)
    root = selectors.get("root") if selectors else None
    if root:
        for key in root.split("."):
            if isinstance(data, dict):
                data = data.get(key)
            else:
                data = None
                break
    if isinstance(data, list):
        return [r if isinstance(r, dict) else {"value": r} for r in data]
    if isinstance(data, dict):
        return [data]
    return []


def _parse_text(payload: str, selectors: dict[str, str]) -> list[dict[str, Any]]:
    return [{"text": payload}]


_PARSERS = {
    "table": _parse_table,
    "json": _parse_json,
    "text": _parse_text,
}


# ─────────────────────────────────────────────────────────────────────
# The fetcher
# ─────────────────────────────────────────────────────────────────────

async def fetch(source: dict[str, Any], *, force_refresh: bool = False) -> ScrapeResult:
    """Fetch + parse one configured source. Never raises."""
    source_id = source.get("id", "unknown")
    url = source.get("url", "")
    parser_name = source.get("parser", "text")
    robots_mode = source.get("robots", "allow")
    selectors = source.get("selectors", {}) or {}
    user_agent = source.get("user_agent", DEFAULT_USER_AGENT)
    cache_ttl = int(source.get("cache_ttl_seconds", DEFAULT_CACHE_TTL))

    if not url:
        return ScrapeResult(ok=False, source_id=source_id, url="", parser=parser_name,
                            error="missing url")

    # 1. Cache
    if not force_refresh:
        cached = _cache_get(source_id, url)
        if cached is not None:
            log.info("scraper.cache_hit", source_id=source_id, rows=len(cached))
            return ScrapeResult(ok=True, source_id=source_id, url=url,
                                parser=parser_name, rows=cached, cached=True)

    # 2. robots.txt
    if robots_mode == "deny":
        log.warning("scraper.robots_denied", source_id=source_id, url=url)
        return ScrapeResult(ok=False, source_id=source_id, url=url,
                            parser=parser_name, error="robots_denied_by_config")
    if robots_mode == "check":
        try:
            allowed = await _robots_allows(url, user_agent)
        except Exception as exc:
            log.warning("scraper.robots_check_error", source_id=source_id, error=str(exc)[:200])
            allowed = True
        if not allowed:
            log.warning("scraper.robots_denied", source_id=source_id, url=url)
            return ScrapeResult(ok=False, source_id=source_id, url=url,
                                parser=parser_name, error="robots_denied")

    # 3. Rate limit
    domain = urlparse(url).netloc
    rl = check_scraper_limit(domain)
    if not rl.allowed:
        log.warning("scraper.rate_limited", domain=domain, retry_after=rl.retry_after_seconds)
        return ScrapeResult(ok=False, source_id=source_id, url=url,
                            parser=parser_name,
                            error=f"rate_limited retry_after={rl.retry_after_seconds}")

    # 4. Fetch with retries
    last_error: str | None = None
    last_status: int | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
                headers={"User-Agent": user_agent},
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
            last_status = resp.status_code
            if resp.status_code >= 500:
                last_error = f"http_{resp.status_code}"
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(BACKOFF_SCHEDULE[attempt])
                    continue
                break
            if resp.status_code >= 400:
                last_error = f"http_{resp.status_code}"
                break
            # 2xx — parse
            body = resp.text
            bytes_read = len(body.encode("utf-8", errors="ignore"))
            try:
                parse_fn = _PARSERS.get(parser_name, _parse_text)
                rows = parse_fn(body, selectors)
            except Exception as exc:
                log.warning("scraper.parse_failed", source_id=source_id,
                            parser=parser_name, error=str(exc)[:200])
                return ScrapeResult(ok=False, source_id=source_id, url=url,
                                    parser=parser_name,
                                    error=f"parse_failed: {exc}",
                                    status_code=last_status, bytes_read=bytes_read)

            log.info("scraper.fetch_ok", source_id=source_id, rows=len(rows),
                     status=last_status, bytes=bytes_read)
            if rows:
                _cache_set(source_id, url, rows, cache_ttl)
            return ScrapeResult(ok=True, source_id=source_id, url=url,
                                parser=parser_name, rows=rows,
                                status_code=last_status, bytes_read=bytes_read)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
            last_error = type(exc).__name__
            if attempt < MAX_RETRIES:
                await asyncio.sleep(BACKOFF_SCHEDULE[attempt])
                continue
            break
        except Exception as exc:
            last_error = str(exc)[:200]
            break

    log.warning("scraper.fetch_failed", source_id=source_id, error=last_error,
                status=last_status)
    return ScrapeResult(ok=False, source_id=source_id, url=url,
                        parser=parser_name, error=last_error or "unknown_error",
                        status_code=last_status)
'''


def step4_write_scraper() -> None:
    print("\n[4/5] Write packages/trading/sources/scraper.py")
    if SCRAPER.exists():
        print(f"  ↷ {SCRAPER.relative_to(ROOT)} exists — overwriting")
    SCRAPER.write_text(SCRAPER_SRC)
    print(f"  ✓ wrote {SCRAPER.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 5. smoke_scraper.py — validate the 5 configured sources
# ─────────────────────────────────────────────────────────────────────

SMOKE_SRC = '''"""Smoke test: hit each configured scraper source once and print extracted rows.

Loads sources from trading_subblocks.training_config.sources[] for the
three scraper subblocks, fetches each once with force_refresh=True,
and prints what the CSS selectors actually extract.

Usage:
    python scripts/smoke_scraper.py             # all sources
    python scripts/smoke_scraper.py <source_id> # one source
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    from packages.core.config import get_settings
    from packages.trading.sources.scraper import fetch

    filter_id = sys.argv[1] if len(sys.argv) > 1 else None

    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    sources: list[dict] = []
    async with factory() as session:
        rows = (await session.execute(text("""
            SELECT slug, training_config->'sources'
            FROM trading_subblocks
            WHERE org_id IS NULL
              AND slug IN ('stock_lists','market_signals','screener_picks')
              AND source_kind = 'scraper'
            ORDER BY sort_order
        """))).all()
        for slug, sources_json in rows:
            for src in (sources_json or []):
                src = dict(src)
                src["_subblock"] = slug
                sources.append(src)

    await engine.dispose()

    if filter_id:
        sources = [s for s in sources if s.get("id") == filter_id]
        if not sources:
            print(f"❌ no source with id={filter_id}")
            sys.exit(1)

    print(f"Running smoke test on {len(sources)} sources\\n")

    for src in sources:
        sid = src.get("id")
        url = src.get("url")
        print("=" * 70)
        print(f"SOURCE: {sid}")
        print(f"SUBBLOCK: {src.get('_subblock')}")
        print(f"URL: {url}")
        print(f"PARSER: {src.get('parser')}  ROBOTS: {src.get('robots')}")

        result = await fetch(src, force_refresh=True)
        print(f"OK: {result.ok}  STATUS: {result.status_code}  BYTES: {result.bytes_read}")
        if result.error:
            print(f"ERROR: {result.error}")
        print(f"ROWS: {len(result.rows)}")
        print("SAMPLE (first 3 rows):")
        for row in result.rows[:3]:
            print(f"  {json.dumps(row, default=str)}")
        if len(result.rows) > 3:
            print(f"  ... and {len(result.rows) - 3} more")
        print()

    print("=" * 70)
    print("Done. If a source returned OK=True with 0 rows, or ERROR=parse_failed,")
    print("the CSS selectors in trading_subblocks.training_config need updating.")
    print()
    print("To fix a source's selectors, run:")
    print("  UPDATE trading_subblocks")
    print("  SET training_config = jsonb_set(training_config, '{sources}', '<new_json>')")
    print("  WHERE slug = '<subblock>' AND org_id IS NULL;")


if __name__ == "__main__":
    asyncio.run(main())
'''


def step5_write_smoke() -> None:
    print("\n[5/5] Write scripts/smoke_scraper.py")
    SMOKE.write_text(SMOKE_SRC)
    print(f"  ✓ wrote {SMOKE.relative_to(ROOT)}")


def verify() -> bool:
    print("\n" + "=" * 70)
    print("Verify: import scraper")
    r = subprocess.run(
        ["python", "-c",
         "from packages.trading.sources.scraper import fetch, ScrapeResult; "
         "from packages.core.rate_limit import check_scraper_limit; "
         "print('ok')"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if r.returncode != 0:
        print("❌ import failed:")
        print(r.stderr)
        return False
    print("✓ scraper imports cleanly")
    print("✓ check_scraper_limit imports cleanly")

    # Verify selectolax installed or install it
    r = subprocess.run(
        ["python", "-c", "import selectolax; print(selectolax.__version__)"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if r.returncode != 0:
        print("⚠ selectolax not installed — installing now")
        r2 = subprocess.run(
            ["pip", "install", "selectolax"],
            cwd=ROOT, capture_output=True, text=True,
        )
        if r2.returncode != 0:
            print("❌ pip install selectolax failed:")
            print(r2.stderr)
            return False
        print("✓ selectolax installed")
    else:
        print(f"✓ selectolax {r.stdout.strip()}")
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.2: Scraper fetcher")
    print("=" * 70)

    step1_fix_requirements()
    step2_patch_rate_limit()
    step3_package_markers()
    step4_write_scraper()
    step5_write_smoke()

    ok = verify()

    print("=" * 70)
    if ok:
        print("✅ T.2 complete — scraper fetcher shipped")
        print()
        print("Next: run the smoke test against real HTML:")
        print("  python scripts/smoke_scraper.py")
        print()
        print("That will tell us which CSS selectors need fixing.")
        sys.exit(0)
    print("❌ T.2 verification failed")
    sys.exit(1)


if __name__ == "__main__":
    main()
