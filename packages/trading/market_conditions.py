"""Read the trader's market-conditions block.

One row per save in `market_conditions`. This module returns the
latest live row for the org, or an empty dict if the trader has
never filled it in.

The register (market-regime) reads this block as the source of
every number and every process adjustment in the article.
"""
from __future__ import annotations

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

COLS = (
    "what_i_track",
    "baselines",
    "regimes",
    "thresholds",
    "adjustments",
    "mechanisms",
)


async def get_market_conditions(
    session: AsyncSession, org_id: str,
) -> dict[str, str]:
    """Return the latest live market_conditions row for the org.

    Empty dict if the trader has not saved one.
    """
    row = (await session.execute(sql_text("""
        SELECT what_i_track, baselines, regimes,
               thresholds, adjustments, mechanisms,
               realized_vol_current, spread_current, range_current,
               correlation_current, breadth_current
        FROM market_conditions
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT 1
    """), {"oid": org_id})).first()
    if not row:
        return {}
    out: dict[str, object] = {
        c: (row[i] or "").strip()
        for i, c in enumerate(COLS)
    }
    # Append the numeric current readings.
    out["realized_vol_current"] = row[6]
    out["spread_current"] = row[7]
    out["range_current"] = row[8]
    out["correlation_current"] = row[9]
    out["breadth_current"] = row[10]
    return out


async def get_research_block(
    session: AsyncSession,
    *,
    org_id: str,
) -> list[dict[str, str]]:
    """Return the org's research sources for the model prompt.

    Returns [] if the table is absent (migration not yet applied)
    or if the org has no sources. Never raises.
    """
    try:
        rows = (await session.execute(sql_text("""
            SELECT key, citation, one_line
            FROM trading_research_sources
            WHERE org_id = :org_id AND deleted_at IS NULL
            ORDER BY key
        """), {"org_id": org_id})).mappings().all()
    except Exception:
        return []
    return [dict(r) for r in rows]


def format_conditions_block(
    mc: dict[str, str],
    research_block: list[dict[str, str]] | None = None,
) -> str:
    """Format the market-conditions dict for the model prompt.

    Returns an empty string if `mc` is empty.
    """
    if not mc:
        return ""
    labels = {
        "what_i_track": "WHAT I TRACK",
        "baselines":    "BASELINES",
        "regimes":      "REGIMES I RECOGNIZE",
        "thresholds":   "THRESHOLDS",
        "adjustments":  "PROCESS ADJUSTMENTS",
        "mechanisms":   "MECHANISMS I UNDERSTAND",
    }
    parts: list[str] = []
    for c in COLS:
        v = (mc.get(c) or "").strip()
        if not v:
            continue
        parts.append(f"--- {labels[c]} ---\n{v}")

    # Current readings — the live numbers for today.
    current_fields = [
        ("realized_vol_current", "Realized vol (10-day)", "%"),
        ("spread_current", "Median spread", " points"),
        ("range_current", "Daily range", "%"),
        ("correlation_current", "Pairwise correlation", ""),
        ("breadth_current", "Candidate count", ""),
    ]
    current_lines: list[str] = []
    for field, label, unit in current_fields:
        v = mc.get(field)
        if v is None or v == "":
            continue
        current_lines.append(f"- {label}: {v}{unit}")

    if current_lines:
        parts.append(
            "--- CURRENT READINGS ---\n"
            + "\n".join(current_lines)
            + "\n\nCompare these readings against the THRESHOLDS "
            "above. Name the active regime explicitly in the "
            "opening sentence. Use the CURRENT READINGS as the "
            "live numbers for this report — not the baselines."
        )

    if research_block:
        lines: list[str] = []
        for r in research_block:
            key = (r.get("key") or "").strip()
            cite = (r.get("citation") or "").strip()
            one = (r.get("one_line") or "").strip()
            if not (key and cite):
                continue
            lines.append(f"- [{key}] {cite} — {one}")
        if lines:
            parts.append(
                "--- RESEARCH SOURCES ---\n"
                + "\n".join(lines)
                + "\n\nYou may cite these sources by author and year "
                "when the mechanism matches. Do not cite any source "
                "not listed here. Do not invent a citation."
            )

    # Metric source — where the readings come from and how they
    # are computed. Renders as a standalone paragraph after the
    # diagnostics beat. Omits silently when absent.
    ms = (mc.get("metric_source") or "").strip()
    if ms:
        parts.append(
            "--- METRIC SOURCE ---\n"
            + ms
            + "\n\nThis paragraph IS the metric source beat. Reproduce "
            "its facts exactly. Do NOT draw the metric source from "
            "the WHAT I TRACK column. Do NOT invent a computation, a "
            "window, or a sampling time that is not stated here."
        )

    return "\n\n".join(parts)



async def summarize_market_events(
    session: AsyncSession,
    *,
    org_id: str,
    hours: int = 24,
) -> str:
    """Summarize the last N hours of market_events into prose.

    The model receives this as the market-own-report section
    of the source block. Returns empty string if fewer than 2
    events exist so the caller can fall back to conditions-only.
    """
    _sql = (
        "SELECT event_record, event_at "
        "FROM market_events "
        "WHERE org_id = :oid "
        "ORDER BY event_at DESC "
        "LIMIT :lim"
    )
    try:
        rows = (await session.execute(
            sql_text(_sql), {"oid": org_id, "lim": max(4, hours)}
        )).mappings().all()
    except Exception as exc:
        log.warning("market_events.read_failed", error=str(exc)[:200])
        return ""

    if len(rows) < 2:
        return ""

    rows = list(reversed(rows))

    def _record(r):
        rec = r["event_record"]
        if isinstance(rec, str):
            import json as _json
            try:
                rec = _json.loads(rec)
            except Exception:
                return {}
        return rec or {}

    latest = _record(rows[-1])
    latest_event = latest.get("event") or {}
    latest_vol = latest.get("volatility") or {}
    state = (latest_event.get("market_state") or "").strip()
    regime = (latest_vol.get("regime") or "").strip()
    session_date = latest_event.get("session_date") or ""

    vix_levels = []
    for r in rows:
        rec = _record(r)
        v = (rec.get("volatility") or {}).get("vix_level")
        if isinstance(v, (int, float)):
            vix_levels.append(float(v))
    vix_min = round(min(vix_levels), 2) if vix_levels else None
    vix_max = round(max(vix_levels), 2) if vix_levels else None
    vix_open = round(vix_levels[0], 2) if vix_levels else None
    vix_close = round(vix_levels[-1], 2) if vix_levels else None

    asset_moves = {}
    for a in (latest.get("asset_classes") or []):
        cls = (a.get("class") or "").strip()
        pct = a.get("change_pct")
        if cls and isinstance(pct, (int, float)):
            asset_moves[cls] = float(pct)

    mover_counts = {}
    for r in rows:
        rec = _record(r)
        seen_this_row = set()
        for m in (rec.get("top_movers") or []):
            sym = (m.get("symbol") or "").strip()
            if not sym or sym in seen_this_row:
                continue
            seen_this_row.add(sym)
            entry = mover_counts.setdefault(sym, {
                "name": (m.get("name") or sym).strip(),
                "count": 0,
                "last_pct": None,
            })
            entry["count"] += 1
            last_pct = m.get("change_pct")
            if isinstance(last_pct, (int, float)):
                entry["last_pct"] = float(last_pct)
    persistent = sorted(
        mover_counts.items(),
        key=lambda kv: (-kv[1]["count"], kv[1]["last_pct"] or 0),
    )[:5]

    segment_moves = {}
    for s in (latest.get("segments") or []):
        seg = (s.get("segment") or "").strip()
        pct = s.get("change_pct")
        if seg and isinstance(pct, (int, float)):
            segment_moves[seg] = float(pct)

    sentences = []

    if session_date:
        s = "Over the last " + str(hours) + " hours the tape ran through " + session_date
        if state:
            s += " in a " + state + " state"
        if regime:
            s += " (volatility regime: " + regime + ")"
        sentences.append(s + ".")

    if segment_moves:
        pieces = []
        for seg in ("large_cap", "mid_cap", "small_cap"):
            if seg in segment_moves:
                pct = segment_moves[seg]
                sign = "+" if pct >= 0 else ""
                pieces.append(seg.replace("_", " ") + " " + sign + format(pct, ".2f") + "%")
        if pieces:
            sentences.append("Segments: " + ", ".join(pieces) + ".")

    if vix_open is not None and vix_close is not None:
        direction = "up" if vix_close > vix_open else ("down" if vix_close < vix_open else "flat")
        sentences.append(
            "VIX opened at " + str(vix_open) + ", closed at " + str(vix_close)
            + " (" + direction + " " + format(abs(vix_close - vix_open), ".2f") + "), "
            + "range " + str(vix_min) + "-" + str(vix_max) + "."
        )

    if asset_moves:
        pieces = []
        for cls in ("equities", "rates", "commodities", "credit", "currencies"):
            if cls in asset_moves:
                pct = asset_moves[cls]
                sign = "+" if pct >= 0 else ""
                pieces.append(cls + " " + sign + format(pct, ".2f") + "%")
        if pieces:
            sentences.append("Asset classes: " + ", ".join(pieces) + ".")

    if persistent:
        pieces = []
        for sym, info in persistent:
            pct = info["last_pct"]
            if pct is None:
                pieces.append(sym + " (" + str(info["count"]) + " appearances)")
            else:
                sign = "+" if pct >= 0 else ""
                pieces.append(
                    info["name"] + " " + sign + format(pct, ".2f") + "% "
                    + "(" + str(info["count"]) + " appearances in " + str(len(rows)) + " sessions)"
                )
        sentences.append("Persistent movers: " + "; ".join(pieces) + ".")

    return " ".join(sentences)


async def summarize_historical_context(
    session: AsyncSession,
    *,
    org_id: str,
    window_days: int = 90,
) -> str:
    """Read market_history and produce a historical context paragraph.

    Deterministic. No LLM. Returns empty string if there are
    fewer than 3 rows for any symbol, or no symbols at all.
    """
    from datetime import date as _date, timedelta as _td

    cutoff = _date.today() - _td(days=window_days)
    try:
        rows = (await session.execute(sql_text("""
            SELECT symbol, trade_date, close
            FROM market_history
            WHERE org_id = :oid
              AND deleted_at IS NULL
              AND trade_date >= :cutoff
            ORDER BY symbol, trade_date
        """), {"oid": org_id, "cutoff": cutoff})).mappings().all()
    except Exception as exc:
        log.warning("market_history.read_failed", error=str(exc)[:200])
        return ""

    if not rows:
        return ""

    by_symbol: dict[str, list] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(
            (r["trade_date"], float(r["close"]))
        )

    for sym in by_symbol:
        by_symbol[sym].sort()

    def pct_return(series, n):
        if len(series) < n + 1:
            return None
        old = series[-n - 1][1]
        new = series[-1][1]
        if old == 0:
            return None
        return (new - old) / old * 100.0

    def percentile(series, value):
        closes = [c for _, c in series]
        if not closes:
            return None
        below = sum(1 for c in closes if c <= value)
        return int(round(below / len(closes) * 100))

    def median(series):
        closes = sorted(c for _, c in series)
        if not closes:
            return None
        n = len(closes)
        mid = n // 2
        if n % 2 == 1:
            return closes[mid]
        return (closes[mid - 1] + closes[mid]) / 2.0

    sentences = []
    sentences.append(
        "Historical context for the last " + str(window_days)
        + " trading days:"
    )

    friendly = {
        "^VIX": "VIX",
        "SPY": "SPY",
        "MDY": "MDY",
        "IWM": "IWM",
        "TLT": "TLT",
        "GLD": "GLD",
        "HYG": "HYG",
        "UUP": "UUP",
    }

    for sym in sorted(by_symbol.keys()):
        series = by_symbol[sym]
        if len(series) < 3:
            continue
        name = friendly.get(sym, sym)
        latest = series[-1][1]
        lo = min(c for _, c in series)
        hi = max(c for _, c in series)
        med = median(series)
        p = percentile(series, latest)
        ret20 = pct_return(series, 20)
        parts = []
        parts.append(name + " closed at " + format(latest, ".2f") + ".")
        parts.append("90-day range " + format(lo, ".2f") + "-" + format(hi, ".2f") + ".")
        if med is not None:
            parts.append("Median " + format(med, ".2f") + ".")
        if p is not None:
            parts.append("Current print is at the " + str(p) + "th percentile.")
        if ret20 is not None:
            sign = "+" if ret20 >= 0 else ""
            parts.append("20-session return " + sign + format(ret20, ".2f") + "%.")
        sentences.append(" ".join(parts))

    spy = by_symbol.get("SPY")
    iwm = by_symbol.get("IWM")
    if spy and iwm and len(spy) >= 21 and len(iwm) >= 21:
        s_ret = pct_return(spy, 20)
        i_ret = pct_return(iwm, 20)
        if s_ret is not None and i_ret is not None:
            diff = s_ret - i_ret
            sign = "+" if diff >= 0 else ""
            sentences.append(
                "SPY vs IWM over 20 sessions: SPY "
                + ("+" if s_ret >= 0 else "") + format(s_ret, ".2f") + "%, "
                + "IWM "
                + ("+" if i_ret >= 0 else "") + format(i_ret, ".2f") + "%. "
                + "Spread " + sign + format(diff, ".2f") + " points."
            )

    tlt = by_symbol.get("TLT")
    if tlt and spy and len(tlt) >= 21 and len(spy) >= 21:
        t_ret = pct_return(tlt, 20)
        s_ret = pct_return(spy, 20)
        if t_ret is not None and s_ret is not None:
            sentences.append(
                "TLT vs SPY over 20 sessions: TLT "
                + ("+" if t_ret >= 0 else "") + format(t_ret, ".2f") + "%, "
                + "SPY "
                + ("+" if s_ret >= 0 else "") + format(s_ret, ".2f") + "%."
            )

    vix = by_symbol.get("^VIX")
    if vix and spy and len(vix) >= 21 and len(spy) >= 21:
        v_ret = pct_return(vix, 20)
        s_ret = pct_return(spy, 20)
        if v_ret is not None and s_ret is not None:
            direction = "inverse" if (v_ret * s_ret) < 0 else "same-direction"
            sentences.append(
                "VIX vs SPY over 20 sessions: " + direction + " move. "
                + "VIX " + ("+" if v_ret >= 0 else "") + format(v_ret, ".2f") + "%, "
                + "SPY " + ("+" if s_ret >= 0 else "") + format(s_ret, ".2f") + "%."
            )

    return "\n".join(sentences) if sentences else ""
