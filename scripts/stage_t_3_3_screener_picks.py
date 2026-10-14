"""
SignalIQ — Phase T.3.3: screener_picks generator.

Third and final scraper subblock. Sources:
  - stockanalysis.stocks   (500 rows with market_cap)
  - sec.edgar.fulltext     (recent S-1 filings)

Rules:
  - top_by_market_cap  — applied, data present
  - high_roe           — SKIPPED, we don't have ROE data
  - new_filings        — applied, EDGAR returns recent S-1s

The generator logs which rules it applied and which it skipped,
and the prompt tells the LLM not to narrate rules that weren't run.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREENER_PICKS = ROOT / "packages" / "trading" / "subblocks" / "screener_picks.py"
TASK_FILE = ROOT / "apps" / "worker" / "tasks" / "trading.py"
RUN_ONCE = ROOT / "scripts" / "run_screener_picks_once.py"


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


# ─────────────────────────────────────────────────────────────────────
# 1. The generator
# ─────────────────────────────────────────────────────────────────────

SCREENER_PICKS_SRC = '''"""screener_picks generator: rule-based screeners + EDGAR filings.

Sources:
  - stockanalysis.stocks   — /stocks/ page, 500 rows, has market_cap
  - sec.edgar.fulltext     — recent S-1 filings

Rules (from training_config.rules[]):
  - top_by_market_cap     — applied  (market_cap available)
  - high_roe              — skipped  (no ROE field in source data)
  - new_filings           — applied  (EDGAR provides S-1s)

Design: rules that reference fields not present in source_payload are
skipped with a log line. The prompt explicitly tells the LLM which
rules ran, so it never narrates a rule it didn't apply.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.trading.sources.scraper import fetch

from .base import (
    TradingPost,
    call_llm_json_verified,
    is_valid_us_ticker,
    persist_post,
    render_markdown_table,
)

log = get_logger(__name__)


def _parse_market_cap(s: str) -> float | None:
    """Parse '44.11B' → 44110000000.0, '497.65M' → 497650000.0.

    Returns None for unparseable values.
    """
    if not s:
        return None
    m = re.match(r"^([\\d.]+)\\s*([KMBT])?$", s.strip(), re.IGNORECASE)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    suffix = (m.group(2) or "").upper()
    mult = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(suffix, 1)
    return n * mult


def _normalize_stocks(rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Clean the /stocks/ table: valid tickers only, parse market_cap to float."""
    out = []
    dropped = 0
    for r in rows:
        sym = (r.get("symbol") or "").strip()
        if not sym or not is_valid_us_ticker(sym):
            dropped += 1
            continue
        cap_str = (r.get("market_cap") or "").strip()
        cap_val = _parse_market_cap(cap_str)
        if cap_val is None:
            dropped += 1
            continue
        out.append({
            "symbol": sym,
            "name": (r.get("name") or "").strip(),
            "market_cap": cap_str,
            "market_cap_value": cap_val,
        })
    if dropped:
        log.info("screener_picks.stocks_dropped", count=dropped)
    # Sort by market cap descending
    out.sort(key=lambda x: x["market_cap_value"], reverse=True)
    return out[:top_n]


def _normalize_edgar(rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Extract (company, form_type, file_date) from EDGAR response rows."""
    out = []
    for r in rows:
        src = r.get("_source") or {}
        if not isinstance(src, dict):
            continue
        display_names = src.get("display_names") or []
        form = src.get("form") or src.get("file_type") or ""
        date = src.get("file_date") or ""
        if not display_names or not form:
            continue
        # display_names is a list like ["Acme Inc.  (CIK 0001234567)"]
        company = str(display_names[0]).split("  (CIK")[0].strip()
        out.append({
            "company": company,
            "form": str(form)[:32],
            "file_date": str(date)[:10],
        })
        if len(out) >= top_n:
            break
    return out


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    cfg = subblock.get("training_config") or {}
    sources = cfg.get("sources") or []
    rules = cfg.get("rules") or []
    top_n = int(cfg.get("top_n") or 20)

    # --- 1. Fetch enabled sources ---
    fetched: dict[str, list[dict[str, Any]]] = {}
    source_urls: list[str] = []
    for src in sources:
        sid = src.get("id")
        if not sid:
            continue
        if not src.get("enabled", True):
            log.info("screener_picks.source_disabled", source_id=sid)
            continue
        result = await fetch(src, force_refresh=True)
        source_urls.append(src.get("url", ""))
        if not result.ok:
            log.warning("screener_picks.source_failed", source_id=sid, error=result.error)
            fetched[sid] = []
            continue
        fetched[sid] = result.rows
        log.info("screener_picks.source_fetched", source_id=sid, rows=len(result.rows))

    # --- 2. Apply rules ---
    stocks_raw = fetched.get("stockanalysis.stocks", [])
    edgar_raw = fetched.get("sec.edgar.fulltext", [])

    applied_rules: list[str] = []
    skipped_rules: list[str] = []

    top_by_cap: list[dict[str, Any]] = []
    new_filings: list[dict[str, Any]] = []

    for rule in rules:
        rid = rule.get("id")
        if rid == "top_by_market_cap":
            if stocks_raw:
                top_by_cap = _normalize_stocks(stocks_raw, top_n)
                applied_rules.append(rule.get("name", rid))
            else:
                skipped_rules.append(f"{rule.get('name', rid)} — no market_cap data")
        elif rid == "new_filings":
            if edgar_raw:
                new_filings = _normalize_edgar(edgar_raw, top_n)
                applied_rules.append(rule.get("name", rid))
            else:
                skipped_rules.append(f"{rule.get('name', rid)} — no filings data")
        elif rid == "high_roe":
            # We do not have ROE in the current sources
            skipped_rules.append(f"{rule.get('name', rid)} — ROE data not available")
        else:
            skipped_rules.append(f"{rule.get('name', rid)} — unknown rule")

    if not top_by_cap and not new_filings:
        log.warning("screener_picks.no_data", org_id=org_id)
        return {"status": "no_data", "slug": subblock["slug"]}

    # --- 3. Build prompt ---
    sections: list[str] = []
    if top_by_cap:
        sections.append(render_markdown_table(
            top_by_cap,
            ["symbol", "name", "market_cap"],
            title=f"Rule: {applied_rules[0] if applied_rules else 'top by market cap'}",
        ))
    if new_filings:
        sections.append(render_markdown_table(
            new_filings,
            ["company", "form", "file_date"],
            title="Rule: New SEC S-1 filings (last 7 days)",
        ))

    payload_md = "\\n\\n".join(sections)
    applied_str = ", ".join(applied_rules) if applied_rules else "none"
    skipped_str = ", ".join(skipped_rules) if skipped_rules else "none"

    user_prompt = (
        f"Today's rule-based screeners.\\n\\n"
        f"{payload_md}\\n\\n"
        f"Write a short screener summary for {subblock['name']}.\\n\\n"
        f"Rules APPLIED (narrate these): {applied_str}\\n"
        f"Rules SKIPPED (do NOT narrate, do not invent results): {skipped_str}\\n\\n"
        f"Structure (be BRIEF):\\n"
        f"1. Headline: 1-2 sentences on what the screeners flag.\\n"
        f"2. Top market-cap names (from the table above).\\n"
        f"3. Recent SEC S-1 filings (company + form type + date).\\n"
        f"4. One-line note on which rules were skipped and why "
        f"(sources don't provide ROE data).\\n\\n"
        f"Epistemic discipline:\\n"
        f"- Use ONLY data present in the tables above.\\n"
        f"- Do NOT invent ROE numbers, ratios, or filings not listed.\\n"
        f"- Do NOT write 'driven by', 'due to', 'reflecting', 'indicating'.\\n"
        f"- Total prose: 120-180 words.\\n"
        f"- Do NOT give buy/sell advice."
    )

    # --- 4. LLM ---
    post = await call_llm_json_verified(
        session,
        org_id=org_id,
        system=subblock.get("style_guide") or "You write clear screener summaries.",
        user=user_prompt,
        prompt_name="trading.screener_picks",
        source_payload={
            "top_by_market_cap": top_by_cap,
            "new_filings": new_filings,
            "rules_applied": applied_rules,
            "rules_skipped": skipped_rules,
        },
        target_words=150,
    )

    # --- 5. Symbols ---
    all_symbols = sorted({r["symbol"] for r in top_by_cap})

    # --- 6. Persist ---
    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "top_by_market_cap": top_by_cap,
            "new_filings": new_filings,
            "rules_applied": applied_rules,
            "rules_skipped": skipped_rules,
            "top_n": top_n,
        },
        source_urls=[u for u in source_urls if u],
        symbols=all_symbols[:200],
    )
    await session.commit()

    log.info(
        "screener_picks.persisted",
        post_id=post_id[:8],
        words=post.word_count,
        symbols=len(all_symbols),
        applied=len(applied_rules),
        skipped=len(skipped_rules),
    )
    return {
        "status": "ok",
        "slug": subblock["slug"],
        "post_id": post_id,
        "word_count": post.word_count,
        "symbols": len(all_symbols),
    }
'''


RUN_ONCE_SRC = '''"""Manually run the screener_picks generator once against the dev DB."""
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
        print("Running screener_picks generator…\\n")
        result = await generate_subblock(session, org_id=org_id, slug="screener_picks")
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
    print("\n[1/3] Write screener_picks generator")
    SCREENER_PICKS.write_text(SCREENER_PICKS_SRC)
    print(f"  ✓ wrote {SCREENER_PICKS.relative_to(ROOT)}")
    RUN_ONCE.write_text(RUN_ONCE_SRC)
    print(f"  ✓ wrote {RUN_ONCE.relative_to(ROOT)}")


def step2_add_task() -> None:
    print("\n[2/3] Add Celery task")
    src = TASK_FILE.read_text()
    if "trading_generate_screener_picks" in src:
        print("  ↷ task already present")
        return
    addition = '''

@celery_app.task(name="tasks.trading_generate_screener_picks", bind=True)
def trading_generate_screener_picks(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate screener_picks for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = asyncio.run(_generate_for_org(o["id"], "screener_picks"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}
'''
    TASK_FILE.write_text(src.rstrip() + addition)
    print(f"  ✓ {TASK_FILE.name}: task added")


def step3_run() -> bool:
    print("\n[3/3] Run screener_picks generator once")
    r = subprocess.run(
        ["python", "scripts/run_screener_picks_once.py"],
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
    print("SignalIQ — Phase T.3.3: screener_picks generator")
    print("=" * 70)

    step1_write_generator()
    step2_add_task()
    ok = step3_run()

    print("=" * 70)
    if ok:
        print("✅ T.3.3 complete — 3/3 scraper subblocks ship")
        sys.exit(0)
    print("❌ T.3.3 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
