"""
SignalIQ — Phase T.5.3.1: Extend market_data with OHLC bars.

Adds fetch_bars(symbol, range_, interval) → list[{date, open, high, low, close, volume}]
Same Yahoo chart endpoint as fetch_quote. Adds timezone import.
Then patches technical_indicators to use it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKET_DATA = ROOT / "packages" / "ingestion" / "sources" / "market_data.py"
TECH = ROOT / "packages" / "trading" / "subblocks" / "technical_indicators.py"


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


FETCH_BARS_SRC = '''

# ─────────────────────────────────────────────────────────────────────
# Raw OHLC bars (for indicator computation)
# ─────────────────────────────────────────────────────────────────────

async def fetch_bars(
    symbol: str,
    *,
    range_: str = "3mo",
    interval: str = "1d",
) -> list[dict[str, Any]] | None:
    """Fetch raw OHLC bars for a symbol.

    Returns list of dicts: {date, open, high, low, close, volume}
    sorted oldest -> newest. Returns None on any failure.

    Uses the same Yahoo chart endpoint as fetch_quote.
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"range": range_, "interval": interval}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url, params=params,
                headers={"User-Agent": "SignalIQ/0.1 (+contact: ops@signaliq.local)"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("market_data.bars_fetch_failed", symbol=symbol, error=str(exc)[:200])
        return None

    try:
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators") or {}).get("quote") or [{}]
        q0 = quote[0]

        opens = q0.get("open") or []
        highs = q0.get("high") or []
        lows = q0.get("low") or []
        closes = q0.get("close") or []
        volumes = q0.get("volume") or []

        bars: list[dict[str, Any]] = []
        for i, ts in enumerate(timestamps):
            try:
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                lo = lows[i] if i < len(lows) else None
                c = closes[i] if i < len(closes) else None
                v = volumes[i] if i < len(volumes) else None
                # Skip incomplete bars
                if c is None or h is None or lo is None:
                    continue
                bars.append({
                    "date": datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(),
                    "open": _safe_float(o),
                    "high": _safe_float(h),
                    "low": _safe_float(lo),
                    "close": _safe_float(c),
                    "volume": int(v) if v is not None else None,
                })
            except (IndexError, TypeError, ValueError):
                continue

        log.info("market_data.bars_fetched", symbol=symbol, count=len(bars))
        return bars if bars else None
    except Exception as exc:
        log.warning("market_data.bars_parse_failed", symbol=symbol, error=str(exc)[:200])
        return None
'''


def step1_fix_datetime_import() -> None:
    print("\n[1/4] Ensure timezone import")

    src = MARKET_DATA.read_text()
    if "from datetime import datetime, timezone" in src:
        print("  ↷ datetime, timezone already imported")
        return
    if "from datetime import datetime\n" in src:
        MARKET_DATA.write_text(
            src.replace(
                "from datetime import datetime\n",
                "from datetime import datetime, timezone\n",
                1,
            )
        )
        print("  ✓ added timezone to datetime import")
    else:
        raise SystemExit("❌ 'from datetime import datetime' line not found")


def step2_append_fetch_bars() -> None:
    print("\n[2/4] Append fetch_bars() to market_data.py")

    src = MARKET_DATA.read_text()
    if "async def fetch_bars(" in src:
        print("  ↷ fetch_bars already present")
        return

    src = src.rstrip() + "\n" + FETCH_BARS_SRC
    MARKET_DATA.write_text(src)
    print("  ✓ appended fetch_bars()")


def step3_patch_technical() -> None:
    print("\n[3/4] Patch technical_indicators.py to use fetch_bars")

    _patch_and_verify(
        TECH,
        '''async def _fetch_bars(symbol: str) -> list[dict[str, Any]] | None:
    """Fetch OHLC bars via market_data. Returns list of {close, high, low} or None."""
    from packages.ingestion.sources.market_data import fetch_quote

    try:
        q = await fetch_quote(symbol, range_="3mo", interval="1d")
    except Exception as exc:
        log.warning("technical.fetch_failed", symbol=symbol, error=str(exc)[:200])
        return None

    # Quote doesn't expose full OHLC — we need raw. If the fetch_quote interface
    # doesn't give bars, we skip (future: extend market_data to return bars).
    bars = getattr(q, "raw_bars", None) or getattr(q, "bars", None)
    if bars:
        return bars

    # Fallback: synthesise a minimal series from available stats
    # (doesn't give true RSI/MACD — will produce None for those)
    last = getattr(q, "latest_price", None)
    if last is None:
        return None
    # Cannot compute indicators from a single price
    return None''',
        '''async def _fetch_bars(symbol: str) -> list[dict[str, Any]] | None:
    """Fetch OHLC bars via market_data.fetch_bars()."""
    from packages.ingestion.sources.market_data import fetch_bars

    try:
        return await fetch_bars(symbol, range_="3mo", interval="1d")
    except Exception as exc:
        log.warning("technical.fetch_failed", symbol=symbol, error=str(exc)[:200])
        return None''',
        "use fetch_bars from market_data",
    )


def step4_test() -> bool:
    print("\n[4/4] Test fetch_bars + generator")

    r = subprocess.run([
        "python", "-c", """
import asyncio, sys
sys.path.insert(0, '.')
from packages.ingestion.sources.market_data import fetch_bars

async def main():
    bars = await fetch_bars('SPY', range_='3mo', interval='1d')
    if bars is None:
        print('FAIL: fetch_bars returned None')
        return
    print(f'OK: SPY bars = {len(bars)}')
    for b in bars[:3]:
        print(f'  {b["date"]}: O={b["open"]} H={b["high"]} L={b["low"]} C={b["close"]} V={b["volume"]}')

asyncio.run(main())
        """.strip()
    ], cwd=ROOT, capture_output=True, text=True, timeout=60)
    print(r.stdout)
    if r.returncode != 0 or "OK:" not in r.stdout:
        print(r.stderr[-1000:])
        return False

    # Now run the generator
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
        result = await generate_subblock(session, org_id=org_id, slug='technical_indicators')
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
    print("SignalIQ — Phase T.5.3.1: Extend market_data with OHLC bars")
    print("=" * 70)

    step1_fix_datetime_import()
    step2_append_fetch_bars()
    step3_patch_technical()
    ok = step4_test()

    print("=" * 70)
    if ok:
        print("✅ T.5.3.1 complete — technical_indicators generates real posts")
        sys.exit(0)
    print("❌ T.5.3.1 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
