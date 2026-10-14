"""
SignalIQ — Phase T.5.4: FRED client + 2 subblocks.

New source (packages/trading/sources/fred.py):
  - fetch_series(series_id, limit, sort_order) → list[{date, value}]
  - fetch_releases(days_ahead) → list[{release_name, date, ...}]
  - Redis-cached (1h TTL), never raises

Two generators:
  - economic_events : upcoming releases + recent values
  - credit_risk     : yield curve + credit spreads
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "packages" / "trading" / "sources"
SUBBLOCKS_DIR = ROOT / "packages" / "trading" / "subblocks"
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "trading.py"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


# ─────────────────────────────────────────────────────────────────────
# 1. FRED client
# ─────────────────────────────────────────────────────────────────────

FRED_SRC = '''"""FRED (Federal Reserve Economic Data) client.

Free, government source. No ToS concerns. Rate limit ~120 req/min.

Caches observations in Redis (db 3, same as scraper) for 1 hour —
FRED data updates slowly, so this is safe.
"""
from __future__ import annotations

import json as _json
from typing import Any

import httpx

from packages.core.logging import get_logger

log = get_logger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred"
DEFAULT_TIMEOUT = 15.0
CACHE_TTL_SECONDS = 3600  # 1 hour
REDIS_DB = 3  # same as scraper

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
            _redis_client = redis.Redis.from_url(
                f"{base}/{REDIS_DB}", decode_responses=True,
            )
        except Exception as exc:
            log.warning("fred.redis_init_failed", error=str(exc)[:200])
            _redis_client = None
    return _redis_client


def _api_key() -> str | None:
    try:
        from packages.core.config import get_settings
        return getattr(get_settings(), "fred_api_key", None)
    except Exception:
        return None


async def fetch_series(
    series_id: str,
    *,
    limit: int = 30,
    sort_order: str = "desc",
) -> list[dict[str, Any]] | None:
    """Fetch the last N observations for a FRED series.

    Returns [{date: 'YYYY-MM-DD', value: float}, ...] newest first.
    Returns None on failure, never raises.
    """
    key = _api_key()
    if not key:
        log.warning("fred.no_api_key", series=series_id)
        return None

    cache_key = f"fred:series:{series_id}:{limit}:{sort_order}"
    r = _get_redis()
    if r is not None:
        try:
            cached = r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    url = f"{FRED_BASE}/series/observations"
    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "limit": limit,
        "sort_order": sort_order,
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("fred.fetch_failed", series=series_id, error=str(exc)[:200])
        return None

    out: list[dict[str, Any]] = []
    for obs in data.get("observations", []):
        v = obs.get("value")
        if v is None or v == ".":
            continue
        try:
            out.append({
                "date": obs.get("date"),
                "value": float(v),
            })
        except (ValueError, TypeError):
            continue

    if r is not None and out:
        try:
            r.setex(cache_key, CACHE_TTL_SECONDS, _json.dumps(out))
        except Exception:
            pass

    log.info("fred.series_fetched", series=series_id, count=len(out))
    return out


async def fetch_releases(days_ahead: int = 14) -> list[dict[str, Any]] | None:
    """Fetch upcoming economic release dates within the next N days.

    Returns [{release_id, release_name, date}, ...] sorted ascending.
    Returns None on failure.
    """
    key = _api_key()
    if not key:
        return None

    from datetime import UTC, datetime, timedelta

    cache_key = f"fred:releases:{days_ahead}"
    r = _get_redis()
    if r is not None:
        try:
            cached = r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    today = datetime.now(UTC).date()
    end = today + timedelta(days=days_ahead)

    url = f"{FRED_BASE}/releases/dates"
    params = {
        "api_key": key,
        "file_type": "json",
        "realtime_start": today.isoformat(),
        "realtime_end": end.isoformat(),
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc",
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("fred.releases_failed", error=str(exc)[:200])
        return None

    out: list[dict[str, Any]] = []
    for rel in data.get("release_dates", []):
        out.append({
            "release_id": rel.get("release_id"),
            "release_name": rel.get("release_name"),
            "date": rel.get("date"),
        })

    if r is not None and out:
        try:
            r.setex(cache_key, CACHE_TTL_SECONDS, _json.dumps(out))
        except Exception:
            pass

    log.info("fred.releases_fetched", count=len(out), days=days_ahead)
    return out
'''


# ─────────────────────────────────────────────────────────────────────
# 2. economic_events generator
# ─────────────────────────────────────────────────────────────────────

ECONOMIC_EVENTS_SRC = '''"""economic_events: upcoming releases + recent values from FRED."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from .base import call_llm_json_verified, persist_post, render_markdown_table
from ._fred_shared import fetch_series, fetch_releases, fmt_value

log = get_logger(__name__)

# Series to include recent values for
RECENT_SERIES = [
    ("CPIAUCSL", "CPI (all urban consumers)"),
    ("UNRATE", "Unemployment rate"),
    ("PAYEMS", "Nonfarm payrolls"),
    ("FEDFUNDS", "Federal funds rate"),
    ("GDP", "Gross domestic product"),
]


async def generate(session: AsyncSession, *, org_id: str, subblock: dict[str, Any]) -> dict[str, Any] | None:
    # Upcoming releases
    releases = await fetch_releases(days_ahead=14)
    if releases is None:
        releases = []

    # Recent values
    recent: list[dict[str, Any]] = []
    for series_id, label in RECENT_SERIES:
        obs = await fetch_series(series_id, limit=2, sort_order="desc")
        if obs and len(obs) >= 1:
            latest = obs[0]
            prev = obs[1] if len(obs) > 1 else None
            recent.append({
                "series_id": series_id,
                "label": label,
                "date": latest["date"],
                "value": latest["value"],
                "previous": prev["value"] if prev else None,
            })

    if not releases and not recent:
        log.warning("economic_events.no_data", org_id=org_id)
        return {"status": "no_data", "slug": subblock["slug"]}

    sections: list[str] = []
    if releases:
        sections.append(render_markdown_table(
            [{"date": r["date"], "release": r["release_name"]}
             for r in releases[:30]],
            ["date", "release"],
            title="Upcoming economic releases (next 14 days)",
        ))
    if recent:
        sections.append(render_markdown_table(
            [{"indicator": r["label"], "latest_date": r["date"],
              "latest": fmt_value(r["value"]), "previous": fmt_value(r["previous"])}
             for r in recent],
            ["indicator", "latest_date", "latest", "previous"],
            title="Most recent indicator values",
        ))

    payload = "\\n\\n".join(sections)

    prompt = (
        f"Economic events data from FRED.\\n\\n{payload}\\n\\n"
        f"Write a brief economic-events note. Structure:\\n"
        f"1. Headline: one sentence on the most important indicator reading.\\n"
        f"2. Recent indicators: 1 sentence per indicator, citing the value and whether it rose/fell.\\n"
        f"3. Calendar: name 3-5 upcoming releases from the table.\\n"
        f"4. One-sentence caveat about data revisions and lag.\\n\\n"
        f"Use ONLY the values in the tables. Do NOT invent dates, releases, or forecast numbers.\\n"
        f"No causal language ('due to', 'driven by', 'reflecting').\\n"
        f"~180 words. Do NOT give buy/sell advice."
    )

    post = await call_llm_json_verified(
        session, org_id=org_id,
        system=subblock.get("style_guide") or "You summarize macro data.",
        user=prompt,
        prompt_name="trading.economic_events",
        source_payload={"releases": releases, "recent": recent},
        target_words=180,
    )

    post_id = await persist_post(
        session, org_id=org_id, subblock=subblock, post=post,
        source_payload={"releases": releases, "recent": recent},
        source_urls=["https://fred.stlouisfed.org/"],
        symbols=[s[0] for s in RECENT_SERIES],
    )
    await session.commit()

    log.info("economic_events.persisted", post_id=post_id[:8], words=post.word_count)
    return {"status": "ok", "slug": subblock["slug"], "post_id": post_id, "word_count": post.word_count}
'''


# ─────────────────────────────────────────────────────────────────────
# 3. credit_risk generator
# ─────────────────────────────────────────────────────────────────────

CREDIT_RISK_SRC = '''"""credit_risk: yield curve + credit spreads from FRED."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from .base import call_llm_json_verified, persist_post, render_markdown_table
from ._fred_shared import fetch_series, fmt_value

log = get_logger(__name__)

SERIES = [
    ("DGS10", "10-year Treasury yield"),
    ("DGS2", "2-year Treasury yield"),
    ("T10Y2Y", "10Y-2Y spread"),
    ("BAMLH0A0HYM2", "High-yield credit spread"),
    ("BAMLC0A0CM", "Investment-grade credit spread"),
]


async def generate(session: AsyncSession, *, org_id: str, subblock: dict[str, Any]) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for series_id, label in SERIES:
        obs = await fetch_series(series_id, limit=30, sort_order="desc")
        if not obs or len(obs) < 2:
            continue
        latest = obs[0]
        month_ago = obs[-1]
        rows.append({
            "series_id": series_id,
            "label": label,
            "latest_date": latest["date"],
            "latest": latest["value"],
            "month_ago": month_ago["value"],
        })

    if not rows:
        log.warning("credit_risk.no_data", org_id=org_id)
        return {"status": "no_data", "slug": subblock["slug"]}

    payload = render_markdown_table(
        [{"indicator": r["label"], "as_of": r["latest_date"],
          "latest": fmt_value(r["latest"]),
          "30d_ago": fmt_value(r["month_ago"])} for r in rows],
        ["indicator", "as_of", "latest", "30d_ago"],
        title="Credit and rates indicators (latest + 30 days ago)",
    )

    prompt = (
        f"Credit & rates data from FRED.\\n\\n{payload}\\n\\n"
        f"Write a brief credit & risk note. Structure:\\n"
        f"1. Headline: one sentence on the state of rates and spreads.\\n"
        f"2. Yield curve: 2Y vs 10Y spread and what it shows.\\n"
        f"3. Credit spreads: HY and IG levels, direction vs 30 days ago.\\n"
        f"4. One-sentence framing: what these levels imply for risk appetite.\\n\\n"
        f"Use ONLY numbers from the table. Do NOT invent levels, targets, or forecasts.\\n"
        f"No causal language. Do NOT predict rate moves.\\n"
        f"~180 words. Do NOT give buy/sell advice."
    )

    post = await call_llm_json_verified(
        session, org_id=org_id,
        system=subblock.get("style_guide") or "You summarize credit conditions.",
        user=prompt,
        prompt_name="trading.credit_risk",
        source_payload={"indicators": rows},
        target_words=180,
    )

    post_id = await persist_post(
        session, org_id=org_id, subblock=subblock, post=post,
        source_payload={"indicators": rows},
        source_urls=["https://fred.stlouisfed.org/"],
        symbols=[s[0] for s in SERIES],
    )
    await session.commit()

    log.info("credit_risk.persisted", post_id=post_id[:8], words=post.word_count)
    return {"status": "ok", "slug": subblock["slug"], "post_id": post_id, "word_count": post.word_count}
'''


FRED_SHARED_SRC = '''"""Shared helpers for FRED-backed trading subblocks."""
from __future__ import annotations

from packages.trading.sources.fred import fetch_releases, fetch_series

__all__ = ["fetch_series", "fetch_releases", "fmt_value"]


def fmt_value(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}"
'''


# ─────────────────────────────────────────────────────────────────────
# 4. Patch config.py to add fred_api_key
# ─────────────────────────────────────────────────────────────────────

CONFIG = ROOT / "packages" / "core" / "config.py"


def step1_add_fred_config() -> None:
    print("\n[1/5] Add FRED_API_KEY to Settings + .env")

    src = CONFIG.read_text()
    if "fred_api_key" in src:
        print("  ↷ config.py: fred_api_key already present")
    else:
        anchor = '    redis_url: str = "redis://localhost:6379/0"'
        if anchor not in src:
            raise SystemExit("❌ config.py: redis_url anchor not found")
        src = src.replace(
            anchor,
            anchor + '\n\n    # --- External API keys ---\n    fred_api_key: str | None = None',
            1,
        )
        CONFIG.write_text(src)
        print("  ✓ config.py: fred_api_key added")

    for p in (ENV_FILE, ENV_EXAMPLE):
        if not p.exists():
            continue
        content = p.read_text()
        if "FRED_API_KEY" in content:
            print(f"  ↷ {p.name}: FRED_API_KEY already present")
            continue
        if not content.endswith("\n"):
            content += "\n"
        content += "\n# --- FRED (Federal Reserve Economic Data) ---\nFRED_API_KEY=\n"
        p.write_text(content)
        print(f"  ✓ {p.name}: FRED_API_KEY added (empty — fill it in)")


def step2_write_fred_client() -> None:
    print("\n[2/5] Write FRED client")
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    (SOURCES_DIR / "fred.py").write_text(FRED_SRC)
    print("  ✓ wrote packages/trading/sources/fred.py")


def step3_write_generators() -> None:
    print("\n[3/5] Write 2 generators + shared helper")
    (SUBBLOCKS_DIR / "_fred_shared.py").write_text(FRED_SHARED_SRC)
    print("  ✓ wrote _fred_shared.py")
    (SUBBLOCKS_DIR / "economic_events.py").write_text(ECONOMIC_EVENTS_SRC)
    print("  ✓ wrote economic_events.py")
    (SUBBLOCKS_DIR / "credit_risk.py").write_text(CREDIT_RISK_SRC)
    print("  ✓ wrote credit_risk.py")


def step4_add_tasks_and_map() -> None:
    print("\n[4/5] Add Celery tasks + slug map entries")

    src = TASK_FILE.read_text()
    added = 0
    for slug in ("economic_events", "credit_risk"):
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
        print(f"  ✓ added {added} Celery tasks")

    trading_router = ROOT / "apps" / "api" / "routers" / "trading.py"
    src = trading_router.read_text()
    anchor = '    "technical_indicators": "tasks.trading_generate_technical_indicators",'
    if anchor not in src:
        raise SystemExit("❌ slug map anchor not found")
    added_map = 0
    for slug in ("economic_events", "credit_risk"):
        entry = f'    "{slug}": "tasks.trading_generate_{slug}",'
        if entry not in src:
            src = src.replace(anchor, anchor + "\n" + entry, 1)
            anchor = entry
            added_map += 1
    if added_map:
        trading_router.write_text(src)
        print(f"  ✓ added {added_map} slug map entries")


def step5_test() -> bool:
    print("\n[5/5] Test FRED client + both generators")

    # Check FRED_API_KEY set
    import os
    env_path = ROOT / ".env"
    fred_key_set = False
    for line in env_path.read_text().splitlines():
        if line.startswith("FRED_API_KEY="):
            val = line.split("=", 1)[1].strip()
            if val:
                fred_key_set = True
            break
    if not fred_key_set:
        print("  ⚠ FRED_API_KEY is empty in .env")
        print("     Get one free at https://fredaccount.stlouisfed.org/apikeys")
        print("     Add to .env, then re-run this script.")
        return False

    # Test fetch_series
    r = subprocess.run([
        "python", "-c", """
import asyncio, sys
sys.path.insert(0, '.')
from packages.trading.sources.fred import fetch_series, fetch_releases

async def main():
    obs = await fetch_series('DGS10', limit=3, sort_order='desc')
    print(f'DGS10 last 3: {obs}')
    rel = await fetch_releases(days_ahead=7)
    if rel:
        print(f'releases next 7d: {len(rel)}')
        for r in rel[:3]:
            print(f'  {r[\"date\"]}: {r[\"release_name\"]}')
    else:
        print('releases: none or failed')

asyncio.run(main())
        """.strip()
    ], cwd=ROOT, capture_output=True, text=True, timeout=60)
    print(r.stdout)
    if r.returncode != 0 or "DGS10 last 3: None" in r.stdout:
        print(r.stderr[-800:])
        return False

    # Run both generators
    for slug in ("economic_events", "credit_risk"):
        print(f"\n--- {slug} ---")
        r = subprocess.run([
            "python", "-c", f"""
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
        await session.execute(text(f"SET app.current_org_id = '{{org_id}}'"))
        await session.commit()
        result = await generate_subblock(session, org_id=org_id, slug='{slug}')
        print(f'result: {{result}}')
    await engine.dispose()

asyncio.run(main())
            """.strip()
        ], cwd=ROOT, capture_output=True, text=True, timeout=180)
        print(r.stdout)
        if r.returncode != 0:
            print(r.stderr[-1500:])
            return False

    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.5.4: FRED client + economic_events + credit_risk")
    print("=" * 70)

    step1_add_fred_config()
    step2_write_fred_client()
    step3_write_generators()
    step4_add_tasks_and_map()
    ok = step5_test()

    print("=" * 70)
    if ok:
        print("✅ T.5.4 complete — 15/16 subblocks ship")
        sys.exit(0)
    print("⚠ T.5.4 written but not verified")
    print("   Fill in FRED_API_KEY in .env (free key at fredaccount.stlouisfed.org)")
    print("   Then re-run this script.")
    sys.exit(1)


if __name__ == "__main__":
    main()
