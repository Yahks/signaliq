"""
SignalIQ — Phase T.5.3: technical_indicators generator.

Computes RSI(14), MACD(12,26,9), ATR(14), SMA(50), SMA(200) from
OHLC bars (via market_data), then asks the LLM to narrate the readings.

All math is local — no external API. If a symbol fails, it's skipped.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBBLOCKS_DIR = ROOT / "packages" / "trading" / "subblocks"
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "trading.py"


GENERATOR_SRC = '''"""technical_indicators: compute RSI/MACD/ATR/SMA from OHLC, narrate readings."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from .base import call_llm_json_verified, persist_post, render_markdown_table
from ._yahoo_shared import fmt_num

log = get_logger(__name__)

# Tracked symbols — liquid ETFs with clean OHLC history
SYMBOLS = ["SPY", "QQQ", "IWM", "TLT", "GLD"]


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _rsi(closes: list[float], window: int = 14) -> float | None:
    if len(closes) < window + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-window, 0):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains += delta
        else:
            losses += -delta
    avg_gain = gains / window
    avg_loss = losses / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(values: list[float], window: int) -> list[float]:
    """Standard EMA series. Returns list of same length as input."""
    if not values:
        return []
    k = 2.0 / (window + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _macd(closes: list[float]) -> tuple[float, float, float] | None:
    """Returns (macd_line, signal_line, histogram) for the last bar."""
    if len(closes) < 35:
        return None
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    signal = _ema(macd_line, 9)
    return (macd_line[-1], signal[-1], macd_line[-1] - signal[-1])


def _atr(highs: list[float], lows: list[float], closes: list[float], window: int = 14) -> float | None:
    if len(closes) < window + 1:
        return None
    trs = []
    for i in range(-window, 0):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs) / len(trs)


def _compute_for_symbol(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """bars: list of {close, high, low} sorted oldest→newest."""
    if not bars or len(bars) < 30:
        return None
    closes = [b["close"] for b in bars if b.get("close") is not None]
    highs = [b["high"] for b in bars if b.get("high") is not None]
    lows = [b["low"] for b in bars if b.get("low") is not None]
    if len(closes) < 30 or len(highs) < 30 or len(lows) < 30:
        return None

    out: dict[str, Any] = {
        "last_close": closes[-1],
        "rsi_14": _rsi(closes),
        "sma_50": _sma(closes, 50),
        "sma_200": _sma(closes, 200),
        "atr_14": _atr(highs, lows, closes),
    }
    macd = _macd(closes)
    if macd:
        out["macd"], out["macd_signal"], out["macd_hist"] = macd
    return out


async def _fetch_bars(symbol: str) -> list[dict[str, Any]] | None:
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
    return None


async def generate(session: AsyncSession, *, org_id: str, subblock: dict[str, Any]) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for sym in SYMBOLS:
        bars = await _fetch_bars(sym)
        if bars is None:
            log.info("technical.skipped_no_bars", symbol=sym)
            continue
        computed = _compute_for_symbol(bars)
        if computed is None:
            continue
        rows.append({"symbol": sym, **computed})

    if not rows:
        log.warning("technical.no_data", org_id=org_id)
        return {"status": "no_data", "slug": subblock["slug"],
                "note": "market_data does not currently expose OHLC bars; see T.5.3 note"}

    # Render table for the LLM
    table_rows = []
    for r in rows:
        table_rows.append({
            "symbol": r["symbol"],
            "close": fmt_num(r.get("last_close")),
            "rsi_14": fmt_num(r.get("rsi_14"), 1),
            "macd": fmt_num(r.get("macd"), 2),
            "macd_sig": fmt_num(r.get("macd_signal"), 2),
            "sma_50": fmt_num(r.get("sma_50")),
            "sma_200": fmt_num(r.get("sma_200")),
            "atr_14": fmt_num(r.get("atr_14")),
        })
    payload = render_markdown_table(
        table_rows,
        ["symbol", "close", "rsi_14", "macd", "macd_sig", "sma_50", "sma_200", "atr_14"],
        title="Technical indicator readings (latest daily bar)",
    )

    prompt = (
        f"Today's technical indicator readings for tracked ETFs.\\n\\n{payload}\\n\\n"
        f"Write a brief technical note. Structure:\\n"
        f"1. One sentence: overall technical posture across the group.\\n"
        f"2. RSI: which symbols are overbought (>70) or oversold (<30).\\n"
        f"3. MACD: which show positive vs negative momentum.\\n"
        f"4. SMA: which are above/below their 50- and 200-day.\\n"
        f"5. One-sentence caveat: indicators describe, they don't predict.\\n\\n"
        f"Use ONLY the values in the table. No invented levels, no predictions.\\n"
        f"No causal language. ~150 words. Do NOT give buy/sell advice."
    )

    post = await call_llm_json_verified(
        session, org_id=org_id,
        system=subblock.get("style_guide") or "You write technical notes.",
        user=prompt,
        prompt_name="trading.technical_indicators",
        source_payload={"readings": rows},
        target_words=150,
    )

    post_id = await persist_post(
        session, org_id=org_id, subblock=subblock, post=post,
        source_payload={"readings": rows},
        source_urls=["https://finance.yahoo.com/"],
        symbols=[r["symbol"] for r in rows],
    )
    await session.commit()
    log.info("technical.persisted", post_id=post_id[:8], words=post.word_count, symbols=len(rows))
    return {"status": "ok", "slug": subblock["slug"], "post_id": post_id,
            "word_count": post.word_count, "symbols": len(rows)}
'''


def step1_write_generator() -> None:
    print("\n[1/4] Write technical_indicators generator")
    (SUBBLOCKS_DIR / "technical_indicators.py").write_text(GENERATOR_SRC)
    print("  ✓ wrote technical_indicators.py")


def step2_check_market_data() -> None:
    print("\n[2/4] Check market_data exposes OHLC bars")
    r = subprocess.run([
        "python", "-c",
        "from packages.ingestion.sources.market_data import fetch_quote, Quote; "
        "import inspect; "
        "src = inspect.getsource(fetch_quote); "
        "print('has raw_bars attr:', 'raw_bars' in src); "
        "print('returns Quote with fields:', [f for f in Quote.__dataclass_fields__])"
    ], cwd=ROOT, capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-500:])


def step3_add_task() -> None:
    print("\n[3/4] Add Celery task")
    src = TASK_FILE.read_text()
    if "trading_generate_technical_indicators" in src:
        print("  ↷ task already present")
        return
    block = '''

@celery_app.task(name="tasks.trading_generate_technical_indicators", bind=True)
def trading_generate_technical_indicators(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate technical_indicators for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = asyncio.run(_generate_for_org(o["id"], "technical_indicators"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}
'''
    TASK_FILE.write_text(src.rstrip() + block)
    print(f"  ✓ {TASK_FILE.name}: task added")


def step4_update_slug_map() -> None:
    print("\n[4/4] Extend API slug → task map")
    trading_router = ROOT / "apps" / "api" / "routers" / "trading.py"
    src = trading_router.read_text()
    entry = '    "technical_indicators": "tasks.trading_generate_technical_indicators",'
    if entry in src:
        print("  ↷ slug map already has technical_indicators")
        return
    marker = '    "asset_classes": "tasks.trading_generate_asset_classes",'
    if marker not in src:
        raise SystemExit("❌ slug map anchor not found")
    trading_router.write_text(src.replace(marker, marker + "\n" + entry, 1))
    print("  ✓ slug map updated")


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.5.3: technical_indicators generator")
    print("=" * 70)

    step1_write_generator()
    step2_check_market_data()
    step3_add_task()
    step4_update_slug_map()

    print("=" * 70)
    print("✅ T.5.3 written — running generator once to test")
    print()

    # Try it
    r = subprocess.run([
        "python", "-c",
        '''
import asyncio
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
        result = await generate_subblock(session, org_id=org_id, slug="technical_indicators")
        print(f"result: {result}")
    await engine.dispose()

asyncio.run(main())
        '''.strip()
    ], cwd=ROOT, capture_output=True, text=True, timeout=180)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-1500:])


if __name__ == "__main__":
    main()
