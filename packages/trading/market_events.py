"""Market event record — built by movers, read by the four
market-structure subblocks.

The event record is a structured snapshot of the market state at
the time movers ran: volatility regime, segment behavior,
asset-class behavior, top movers.

No internet fetches here. Movers fetches once. The four
downstream subblocks read.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


# ── Regime thresholds ────────────────────────────────────────────
# VIX level → regime. Standard practitioner bands.
VIX_COMPRESSED = 15.0       # below this = compressed
VIX_EXPANDED = 25.0         # above this = expanded
# Between = transitional.


def _quote(quotes: list[dict], symbol: str) -> dict | None:
    for q in quotes:
        if q.get("symbol") == symbol:
            return q
    return None


def _classify_regime(vix: dict | None) -> str:
    if not vix or vix.get("price") is None:
        return "unknown"
    level = vix["price"]
    if level < VIX_COMPRESSED:
        return "compressed"
    if level > VIX_EXPANDED:
        return "expanded"
    return "transitional"


def build_event_record(
    reference_quotes: list[dict],
    flagged: list[dict] | None = None,
) -> dict:
    """Build the structured event record.

    reference_quotes comes from fetch_quotes(REFERENCE_SYMBOLS).
    flagged is the list of individual equities that moved past the
    threshold this run — the top_movers block. Optional; if None,
    the top_movers block is empty.
    """
    vix   = _quote(reference_quotes, "^VIX")
    spy   = _quote(reference_quotes, "SPY")
    mdy   = _quote(reference_quotes, "MDY")
    iwm   = _quote(reference_quotes, "IWM")
    tlt   = _quote(reference_quotes, "TLT")
    gld   = _quote(reference_quotes, "GLD")
    hyg   = _quote(reference_quotes, "HYG")
    uup   = _quote(reference_quotes, "UUP")

    def _pct(q: dict | None) -> float | None:
        if not q:
            return None
        v = q.get("change_pct")
        return round(float(v), 2) if v is not None else None

    segments = [
        {"segment": "large_cap", "symbol": "SPY", "change_pct": _pct(spy)},
        {"segment": "mid_cap",   "symbol": "MDY", "change_pct": _pct(mdy)},
        {"segment": "small_cap", "symbol": "IWM", "change_pct": _pct(iwm)},
    ]

    asset_classes = [
        {"class": "equities",    "symbol": "SPY", "change_pct": _pct(spy)},
        {"class": "rates",       "symbol": "TLT", "change_pct": _pct(tlt)},
        {"class": "commodities", "symbol": "GLD", "change_pct": _pct(gld)},
        {"class": "credit",      "symbol": "HYG", "change_pct": _pct(hyg)},
        {"class": "currencies",  "symbol": "UUP", "change_pct": _pct(uup)},
    ]

    regime = _classify_regime(vix)
    now = datetime.now(UTC)

    top_movers = []
    for q in (flagged or [])[:20]:
        sym = q.get("symbol")
        if not sym:
            continue
        top_movers.append({
            "symbol": sym,
            "change_pct": _pct(q),
            "price": q.get("price"),
            "name": q.get("name"),
        })

    return {
        "generated_at": now.isoformat(),
        "event": {
            "type": "session_close",
            "description": f"Session close on {now.date().isoformat()}",
            "session_date": now.date().isoformat(),
            "market_state": regime,
        },
        "volatility": {
            "vix_level": vix["price"] if vix else None,
            "vix_change_pct": _pct(vix),
            "regime": regime,
        },
        "segments": segments,
        "asset_classes": asset_classes,
        "top_movers": top_movers,
    }


def format_event_block(event: dict) -> str:
    """Format the event record for the model prompt."""
    if not event:
        return ""

    lines: list[str] = []

    ev = event.get("event", {})
    if ev:
        lines.append("--- EVENT ---")
        lines.append(f"Type:   {ev.get('type')}")
        lines.append(f"When:   {ev.get('session_date')}")
        lines.append(f"State:  {ev.get('market_state')}")
        lines.append("")

    vol = event.get("volatility", {})
    lines.append("--- MARKET CONTEXT ---")
    lines.append(f"Volatility level:  {vol.get('vix_level')}")
    lines.append(f"Volatility change: {vol.get('vix_change_pct')}%")
    lines.append(f"Volatility regime: {vol.get('regime')}")
    lines.append(
        "  (Regime thresholds: below 14 = compressed, "
        "14-22 = transitional, above 22 = expanded.)"
    )
    lines.append("")

    lines.append("--- SEGMENT BEHAVIOR ---")
    for s in event.get("segments", []):
        lines.append(f"{s['segment']:12}: {s.get('change_pct')}%")

    lines.append("")
    lines.append("--- ASSET-CLASS BEHAVIOR ---")
    for a in event.get("asset_classes", []):
        lines.append(f"{a['class']:12}: {a.get('change_pct')}%")

    movers = event.get("top_movers", [])
    if movers:
        lines.append("")
        lines.append("--- TOP MOVERS ---")
        lines.append(
            "  (Direction and magnitude only. Do NOT name the "
            "underlying security — no ticker, no company name.)"
        )
        for m in movers[:20]:
            pct = m.get("change_pct")
            if pct is None:
                continue
            sign = "+" if pct >= 0 else ""
            lines.append(f"  {sign}{pct}%")

    return "\n".join(lines)

async def get_latest_event(
    session: AsyncSession, org_id: str,
) -> dict | None:
    """Return the newest market_events row for the org, or None."""
    row = (await session.execute(sql_text("""
        SELECT event_record, event_at, volatility_regime
        FROM market_events
        WHERE org_id = :oid
        ORDER BY event_at DESC
        LIMIT 1
    """), {"oid": org_id})).first()
    if not row:
        return None
    record = dict(row[0] or {})
    record["_event_at"] = row[1].isoformat() if row[1] else None
    record["_volatility_regime"] = row[2]
    return record


async def persist_event(
    session: AsyncSession, org_id: str, record: dict,
) -> str:
    """Insert a new market_events row. Returns the id."""
    import json as _json
    row = (await session.execute(sql_text("""
        INSERT INTO market_events (org_id, event_record, volatility_regime)
        VALUES (:oid, CAST(:rec AS jsonb), :regime)
        RETURNING id::text
    """), {
        "oid": org_id,
        "rec": _json.dumps(record, default=str),
        "regime": (record.get("volatility") or {}).get("regime"),
    })).first()
    await session.commit()
    return row[0]
