"""Read the trader's process document. Drafts are never read by the register."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


DIMENSION_TO_SECTION = {
    "D1": "daily_scan", "D2": "entry_logic", "D3": "position_sizing",
    "D4": "exit_logic", "D5": "rolling", "D6": "holding_period",
    "D7": "multi_strategy_blend", "D8": "chart_overlay",
    "D9": "research_to_rules", "D10": "stocks_vs_options",
    "D11": "universes", "D12": "no_backtest_rule",
    "D13": "emotion_removal", "D14": None,
}

SECTION_LABELS = {
    "daily_scan": "the daily scan", "entry_logic": "entry logic",
    "position_sizing": "position sizing", "exit_logic": "exit logic",
    "holding_period": "holding periods", "research_to_rules": "research-to-rules",
    "chart_overlay": "the chart overlay", "no_backtest_rule": "the no-backtest rule",
    "multi_strategy_blend": "multi-strategy blending",
    "rolling": "rolling positions",
    "stocks_vs_options": "stocks vs options",
    "universes": "universes",
    "emotion_removal": "emotion removal",
    "fundamental_metrics": "fundamental metrics",
    "trade_history_summary": "trade history summary",
}


def load_synthetic_trades() -> list[dict]:
    """Read content/synthetic_trades.yaml.

    Returns trades in the same shape as get_recent_trades, so any
    caller can treat real and synthetic trades uniformly.

    The file is written by packages/trading/synthetic_trades.py on
    a 2-hour beat. It is NEVER imported into trade_log — the two
    sources stay separate so a synthetic trade can never leak into
    a subblock that did not opt in.
    """
    import yaml
    from datetime import datetime
    path = (
        Path(__file__).resolve().parents[2]
        / "content" / "synthetic_trades.yaml"
    )
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return []
    trades = data.get("trades", [])
    out: list[dict] = []
    for t in trades:
        try:
            dt = datetime.fromisoformat(str(t["date"]))
        except (KeyError, ValueError):
            continue
        out.append({
            "traded_at": dt,
            "instrument": t.get("instrument", "NQ"),
            "direction": t.get("direction", "long"),
            "setup": t.get("setup", ""),
            "entry": t.get("entry"),
            "contracts": t.get("contracts"),
            "stop": t.get("stop"),
            "atr_at_entry": t.get("atr_at_entry"),
            "exit": t.get("exit"),
            "exit_reason": t.get("exit_reason", ""),
            "result": t.get("result"),
        })
    return out


async def get_process_excerpt(
    session: AsyncSession, org_id: str, dimension_id: str,
    include_synthetic: bool = False,
) -> str:
    """Return a bounded excerpt of the trader's process + history.

    Groq's gpt-oss-120b free tier has an 8,000 tokens-per-minute
    limit. The full process + 5 history versions is ~32,000 chars
    (~8,500 tokens), right at the edge. These caps keep the excerpt
    around 4,000 chars / ~1,200 tokens.

    Bounds:
      - current: each section capped at 500 chars
      - history: 2 versions, each section capped at 200 chars
      - total excerpt hard-capped at 4,500 chars
    """
    MAX_SECTION_CHARS = 400
    MAX_HISTORY_VERSIONS = 1
    MAX_HISTORY_CHARS = 150
    # Raised again: the previous cap cut off the CURRENT PROCESS
    # section, so the model never saw the source rules the
    # TRADE RULE ANALYSIS was computed from. 8000 chars keeps
    # RECENT TRADES + TRADE RULE ANALYSIS + CURRENT PROCESS
    # under the Groq 8k-token/minute ceiling.
    MAX_TOTAL_CHARS = 8000

    def _trunc(text: str, limit: int) -> str:
        s = (text or "").strip()
        if len(s) <= limit:
            return s
        return s[:limit].rstrip() + "…"

    current = (await session.execute(sql_text("""
        SELECT daily_scan, entry_logic, position_sizing, exit_logic,
               holding_period, research_to_rules, chart_overlay,
               no_backtest_rule, multi_strategy_blend,
               rolling, stocks_vs_options, universes, emotion_removal,
               fundamental_metrics, trade_history_summary
        FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL LIMIT 1
    """), {"oid": org_id})).first()
    if not current:
        return ""

    cols = (
        "daily_scan", "entry_logic", "position_sizing", "exit_logic",
        "holding_period", "research_to_rules", "chart_overlay",
        "no_backtest_rule", "multi_strategy_blend",
        "rolling", "stocks_vs_options", "universes", "emotion_removal",
        "fundamental_metrics", "trade_history_summary",
    )
    emphasis = DIMENSION_TO_SECTION.get(dimension_id)

        # S.9.13 — recent trades. The hook for every article opens on
    # a specific trade from the trade log.
    recent_rows = (await session.execute(sql_text("""
        SELECT traded_at, instrument, direction, contracts,
               entry, stop, exit, exit_reason, result,
               percent_of_equity, hold_time, setup, lesson
        FROM trade_log
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY traded_at DESC
        LIMIT 5
    """), {"oid": org_id})).all()

    _tcols = (
        "traded_at", "instrument", "direction", "contracts",
        "entry", "stop", "exit", "exit_reason", "result",
        "percent_of_equity", "hold_time", "setup", "lesson",
    )
    recent = [dict(zip(_tcols, r)) for r in recent_rows]

    if include_synthetic:
        _synth = load_synthetic_trades()
        for _t in _synth[:3]:
            recent.append({
                "traded_at": _t["traded_at"],
                "instrument": _t.get("instrument", "NQ"),
                "direction": _t.get("direction", "long"),
                "contracts": _t.get("contracts"),
                "entry": _t.get("entry"),
                "stop": _t.get("stop"),
                "exit": _t.get("exit"),
                "exit_reason": _t.get("exit_reason", ""),
                "result": _t.get("result"),
                "percent_of_equity": None,
                "hold_time": "",
                "setup": _t.get("setup", ""),
                "lesson": "",
                "_synthetic": True,
            })

    parts: list[str] = []

    if recent:
        parts.append("=== RECENT TRADES (newest first) ===")
        for r in recent:
            date = r["traded_at"].strftime("%Y-%m-%d") if r["traded_at"] else "unknown"
            synth_mark = " (synthetic)" if r.get("_synthetic") else ""
            inst = r["instrument"] or "?"
            direction = r["direction"] or "?"
            contracts = r["contracts"] if r["contracts"] is not None else "?"
            entry = r["entry"] if r["entry"] is not None else "?"
            stop = r["stop"] if r["stop"] is not None else "?"
            exit_p = r["exit"] if r["exit"] is not None else "?"
            reason = r["exit_reason"] or "?"
            dollars = f"${r['result']:.0f}" if r["result"] is not None else "?"
            pct = f"{r['percent_of_equity']:.2f}%" if r["percent_of_equity"] is not None else "?"
            hold = r["hold_time"] or "?"
            setup = r["setup"] or "?"
            lesson = r["lesson"] or ""
            parts.append(
                f"{date}{synth_mark} | {inst} | {direction} | {contracts} contracts | "
                f"entry {entry} | stop {stop} | exit {exit_p} | {reason} | "
                f"{dollars} ({pct}) | held {hold}\n"
                f"  setup: {setup}\n"
                f"  lesson: {lesson}"
            )
        parts.append("")

    # S.9.14 — pre-computed rule analysis. The model quotes it.
    try:
        from .trade_rule_analysis import get_rule_analysis
        analysis = await get_rule_analysis(
            session, org_id, limit=5,
            include_synthetic=include_synthetic,
        )
        if analysis:
            parts.append("")
            parts.append(analysis)
    except Exception as exc:
        log.warning("trader_process.rule_analysis_failed",
                    error=str(exc)[:200])

    parts.append("=== CURRENT PROCESS ===")

    # S.9.12 — trade_history_summary renders FIRST so the excerpt
    # total-length cap never trims it. It is the evidence source for
    # every performance claim the register asks for.
    current_dict = dict(zip(cols, current))
    th = current_dict.get("trade_history_summary", "")
    if th and th.strip():
        parts.append(
            f"--- TRADE HISTORY SUMMARY ---\n{_trunc(th, 800)}"
        )

    for c in cols:
        if c == "trade_history_summary":
            continue
        v = current_dict.get(c)
        if not v or not v.strip():
            continue
        label = SECTION_LABELS[c]
        marker = "  ← EMPHASIS" if c == emphasis else ""
        parts.append(
            f"--- {label.upper()}{marker} ---\n"
            f"{_trunc(v, MAX_SECTION_CHARS)}"
        )

    history = (await session.execute(sql_text("""
        SELECT daily_scan, entry_logic, position_sizing, exit_logic,
               holding_period, research_to_rules, chart_overlay,
               no_backtest_rule, multi_strategy_blend,
               rolling, stocks_vs_options, universes, emotion_removal,
               fundamental_metrics, trade_history_summary,
               created_at
        FROM trader_process_history
        WHERE org_id = :oid
        ORDER BY created_at DESC
        LIMIT :n
    """), {"oid": org_id, "n": MAX_HISTORY_VERSIONS})).all()

    if history:
        parts.append("")
        parts.append(
            "=== PRIOR VERSIONS (evolution context only) ==="
        )
        for h in history:
            created = h[-1]
            date = created.strftime("%Y-%m-%d") if created else "unknown"
            version_lines = [f"--- {date} ---"]
            for c, v in zip(cols, h[:-1]):
                if v and v.strip():
                    version_lines.append(
                        f"{SECTION_LABELS[c]}: "
                        f"{_trunc(v, MAX_HISTORY_CHARS)}"
                    )
            if len(version_lines) > 1:
                parts.append("\n".join(version_lines))

    out = "\n\n".join(parts)
    if len(out) > MAX_TOTAL_CHARS:
        out = out[:MAX_TOTAL_CHARS].rstrip() + "\n…"
    return out
