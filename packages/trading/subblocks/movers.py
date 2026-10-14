"""movers subblock: scan for stocks crossing daily move thresholds,
generate an article for each one using the Studio pipeline.

Not a data-driven writer like the other subblocks. This one is a
scanner: it decides WHAT to write about based on today's market,
then delegates the actual generation to generate_studio_response.

Scheduling: runs on the hourly_market cadence during market hours.
Config (from the subblock's training_config):
  threshold_pct        — minimum |daily change %| to flag (default 5.0)
  max_articles_per_run — cap on articles per scan (default 3)
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    from ..movers_universe import REFERENCE_SYMBOLS, get_universe
    from ..subblocks._yahoo_shared import fetch_quotes
    from ..sources.sec_edgar import fetch_filings

    cfg = subblock.get("training_config") or {}
    threshold = float(cfg.get("threshold_pct") or 5.0)
    max_articles = int(cfg.get("max_articles_per_run") or 3)

    universe = get_universe()
    log.info("movers.scan_start", universe=len(universe), threshold=threshold)

    # Fetch the equity universe AND the reference symbols. The
    # references are context, not movers — they feed the event
    # record the four market-structure subblocks read.
    quotes = await fetch_quotes(universe)
    reference_quotes = await fetch_quotes(REFERENCE_SYMBOLS)


    # Flag the movers.
    flagged: list[dict[str, Any]] = []
    for q in quotes:
        pct = q.get("change_pct")
        if pct is None:
            continue
        if abs(pct) < threshold:
            continue
        flagged.append(q)

    # Sort by absolute move, biggest first.
    flagged.sort(key=lambda q: -abs(q.get("change_pct") or 0))

    # Build and persist the event record. This is the shared
    # structured context the four market-structure subblocks
    # (economic_events, market_cap, asset_classes, stock_lists)
    # read to write their pieces without any additional fetch.
    from ..market_events import build_event_record, persist_event
    event_record = build_event_record(reference_quotes, flagged)
    event_id = await persist_event(session, org_id, event_record)
    log.info(
        "movers.event_persisted",
        event_id=event_id[:8],
        regime=event_record.get("volatility", {}).get("regime"),
        movers=len(event_record.get("top_movers", [])),
    )

    if not flagged:
        log.info("movers.no_movers", org_id=org_id[:8], threshold=threshold)
        return {"status": "no_movers", "slug": subblock["slug"]}

    # Filter out tickers we've written about in the last 12 hours
    # (avoid repeated coverage).
    recent_rows = (await session.execute(sql_text("""
        SELECT source_payload->'tickers'->>0 AS ticker
        FROM trading_posts
        WHERE org_id = :oid
          AND subblock_id = :sid
          AND created_at > now() - interval '12 hours'
    """), {"oid": org_id, "sid": subblock["id"]})).all()
    recently_covered = {r[0] for r in recent_rows if r[0]}

    to_write = [q for q in flagged if q["symbol"] not in recently_covered]
    to_write = to_write[:max_articles]

    if not to_write:
        log.info("movers.all_covered", org_id=org_id[:8], flagged=len(flagged))
        return {"status": "all_covered", "slug": subblock["slug"]}

    log.info(
        "movers.will_generate",
        org_id=org_id[:8],
        flagged=len(flagged),
        writing=len(to_write),
        symbols=[q["symbol"] for q in to_write],
    )

    # Generate an article per flagged ticker using the shared
    # Studio generation path (generate_article_content). This does
    # NOT insert into studio_messages — the movers subblock is a
    # scheduled generator, not a chat session.
    from ..studio import generate_article_content

    written: list[dict[str, Any]] = []
    for q in to_write:
        sym = q["symbol"]
        pct = q["change_pct"]
        direction = "up" if pct > 0 else "down"

        # S.8.54 — fetch recent SEC filings for this ticker. Adds
        # material-event and insider-activity context the news feed
        # may not carry. Never raises; empty on failure.
        filings = await fetch_filings(sym, limit=8)
        filings_md = ""
        if filings:
            lines = []
            for f in filings[:5]:
                form = f["form"]
                date = f["date"]
                if form == "4":
                    lines.append(f"- Form 4 (insider transaction) filed {date}")
                elif form == "8-K":
                    lines.append(f"- 8-K (material event) filed {date}")
                elif form in ("10-K", "10-Q"):
                    lines.append(f"- {form} (financial report) filed {date}")
                else:
                    lines.append(f"- {form} filed {date}")
            filings_md = "Recent SEC filings:\n" + "\n".join(lines)

        # S.8.56 — fetch 3-month daily bars and compute the indicators
        # a trader checks before a position: RSI, MACD, SMA-50/200,
        # ATR, last close. Reuses technical_indicators' computation.
        from ._yahoo_shared import (
            _fetch_bars as _fetch_ti_bars,
            _compute_for_symbol as _compute_indicators,
        )

        bars_md = ""
        try:
            bars = await _fetch_ti_bars(sym)
            if bars:
                ind = _compute_indicators(bars)
                if ind:
                    _last = ind.get("last_close")
                    _rsi = ind.get("rsi_14")
                    _sma50 = ind.get("sma_50")
                    _sma200 = ind.get("sma_200")
                    _atr = ind.get("atr_14")
                    _macd = ind.get("macd")
                    _macd_sig = ind.get("macd_signal")
                    lines = ["Ticker technicals (3-month daily bars):"]
                    if _last is not None:
                        lines.append(f"- Last close: ${_last:.2f}")
                    if _rsi is not None:
                        lines.append(f"- RSI(14): {_rsi:.1f}")
                    if _sma50 is not None and _last is not None:
                        rel = "above" if _last > _sma50 else "below"
                        lines.append(f"- SMA(50): ${_sma50:.2f} - price is {rel}")
                    if _sma200 is not None and _last is not None:
                        rel = "above" if _last > _sma200 else "below"
                        lines.append(f"- SMA(200): ${_sma200:.2f} - price is {rel}")
                    if _macd is not None and _macd_sig is not None:
                        rel = "above" if _macd > _macd_sig else "below"
                        lines.append(f"- MACD: {_macd:.3f} vs signal {_macd_sig:.3f} ({rel})")
                    if _atr is not None:
                        lines.append(f"- ATR(14): ${_atr:.2f}")
                    bars_md = "\n".join(lines)
        except Exception as exc:
            log.warning("movers.indicators_failed", symbol=sym, error=str(exc)[:200])

        prompt = (
            f"You are a retail trader studying {sym}. It moved "
            f"{direction} {abs(pct):.1f}% today.\n\n"
            + (f"{bars_md}\n\n" if bars_md else "")
            + (f"{filings_md}\n\n" if filings_md else "")
            + "A trader never invents data. These rules are absolute:\n"
            + "- Do NOT state any figure, price, threshold, timestamp, or "
              "trade that is not present in the data above.\n"
            + "- Do NOT invent a trade (a retail trader bought 200 shares "
              "at $X). You have no trade data. An invented trade is a lie.\n"
            + "- Do NOT invent price thresholds (a break above $X, "
              "support at $Y). You have no price levels in the data.\n"
            + "- Do NOT invent analyst consensus, insider ownership, guru "
              "activity, or institutional positions. If a number is not "
              "above, do not write it.\n"
            + "- If a section of the register asks for data you do not "
              "have, write one sentence stating the data is not available. "
              "Do NOT fill the section with invented content.\n"
            + "- A trader with insufficient data says so plainly. An honest "
              "short note beats an invented long one.\n\n"
            + "If filings or news explain the move, cite them by form type "
              "and date. If they do not, say so plainly — do NOT speculate "
              "on a cause."
        )

        try:
            result = await generate_article_content(
                session,
                org_id=org_id,
                prompt=prompt,
                track="retail",
                persist=True,
                subblock_slug="movers",
                register="ticker-note",
                tier="tier_2",
            )
        except Exception as exc:
            log.warning(
                "movers.generate_failed",
                symbol=sym,
                error=str(exc)[:200],
            )
            continue

        if result["kind"] == "article" and result["post_id"]:
            # S.8.9.7 — surface the article in the Studio chat feed.
            # A single assistant message with user_id=NULL, no paired
            # user prompt. The chat UI renders NULL user_id rows as
            # system-generated.
            try:
                from packages.db.models.studio_message import StudioMessage
                chat_row = StudioMessage(
                    org_id=org_id,
                    user_id=None,
                    role="assistant",
                    content=result["content"],
                    prompt_kind="article",
                    trading_post_id=result["post_id"],
                    track="retail",
                )
                session.add(chat_row)
                await session.flush()
                log.info(
                    "movers.chat_message_created",
                    symbol=sym,
                    message_id=chat_row.id[:8],
                )
            except Exception as exc:
                # Non-fatal — the trading_post is already saved. Log
                # and continue.
                log.warning(
                    "movers.chat_message_failed",
                    symbol=sym,
                    error=str(exc)[:200],
                )

            written.append({
                "symbol": sym,
                "change_pct": pct,
                "post_id": result["post_id"],
                "word_count": len(result["content"].split()),
            })
            log.info(
                "movers.article_written",
                symbol=sym,
                pct=pct,
                post_id=result["post_id"][:8],
            )

    await session.commit()

    return {
        "status": "ok",
        "slug": subblock["slug"],
        "flagged": len(flagged),
        "written": len(written),
        "articles": written,
    }
