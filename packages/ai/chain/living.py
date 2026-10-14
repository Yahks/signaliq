"""Living-chain enrichment: impacts, scenarios, watchlist, reattach.

These run after narration. Each is a bounded LLM call. Failures are soft:
the chain still saves without them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from packages.ai.prompts.loader import render, render_with_version
from packages.ai.router import complete
from packages.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class Impact:
    label: str
    category: str
    direction: str | None
    strength: str | None
    reasoning: str | None


@dataclass
class Scenario:
    name: str
    condition: str
    reasoning: str
    candidate_symbols: list[str] = field(default_factory=list)
    direction: str | None = None
    quantified: dict = field(default_factory=dict)


@dataclass
class WatchItem:
    label: str
    symbol: str | None
    reasoning: str | None
    live_quote: dict = field(default_factory=dict)


@dataclass
class LivingEnrichment:
    impacts: list[Impact] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    watchlist: list[WatchItem] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def _format_events(events: list[dict]) -> str:
    lines = []
    for i, e in enumerate(events, 1):
        lines.append(
            f"[{i}] {e.get('headline', '?')}\n"
            f"    {e.get('summary', '')[:400]}\n"
            f"    actors: {', '.join(e.get('actors') or [])}"
        )
    return "\n\n".join(lines)


def _format_impacts(impacts: list[Impact]) -> str:
    if not impacts:
        return "(none)"
    return "\n".join(
        f"- {i.label} ({i.category}, {i.direction or '?'}, {i.strength or '?'}): {i.reasoning or ''}"
        for i in impacts
    )


async def extract_impacts(
    events: list[dict],
    *,
    org_id: str,
    block_slug: str,
) -> list[Impact]:
    """Extract what this chain could affect."""
    try:
        prompt, _pv = render_with_version(
            "chain.impacts",
            block_slug=block_slug,
            events=_format_events(events),
        )
        raw = await complete(
            role="reasoning",
            system=None,
            user=prompt,
            org_id=org_id,
            prompt_version_id=_pv,
            purpose=f"chain.impacts.{block_slug}",
            temperature=0.3,
            max_tokens=2000,
            json_mode=True,
            use_cache=True,
        )
        parsed = json.loads(raw)
        items = parsed.get("impacts") or []
        out: list[Impact] = []
        for it in items:
            if not isinstance(it, dict) or not it.get("label"):
                continue
            direction = str(it.get("direction", "")).lower() or None
            if direction and direction not in {"up", "down", "mixed", "uncertain"}:
                direction = "uncertain"
            strength = str(it.get("strength", "")).lower() or None
            if strength and strength not in {"strong", "moderate", "weak", "unknown"}:
                strength = "unknown"
            out.append(Impact(
                label=str(it["label"])[:255],
                category=str(it.get("category", "sector"))[:64],
                direction=direction,
                strength=strength,
                reasoning=str(it.get("reasoning", ""))[:1000] or None,
            ))
        log.info("living.impacts", count=len(out), block=block_slug)
        return out
    except Exception as exc:
        log.warning("living.impacts_failed", error=str(exc)[:200])
        return []


async def extract_scenarios(
    events: list[dict],
    impacts: list[Impact],
    *,
    org_id: str,
    block_slug: str,
) -> list[Scenario]:
    try:
        prompt, _pv = render_with_version(
            "chain.scenarios",
            block_slug=block_slug,
            events=_format_events(events),
            impacts=_format_impacts(impacts),
        )
        raw = await complete(
            role="reasoning",
            system=None,
            user=prompt,
            org_id=org_id,
            prompt_version_id=_pv,
            purpose=f"chain.scenarios.{block_slug}",
            temperature=0.4,
            max_tokens=2000,
            json_mode=True,
            use_cache=True,
        )
        parsed = json.loads(raw)
        out: list[Scenario] = []
        for s in parsed.get("scenarios") or []:
            if not isinstance(s, dict) or not s.get("name") or not s.get("condition"):
                continue
            symbols = s.get("candidate_symbols") or []
            if not isinstance(symbols, list):
                symbols = []
            symbols = [str(x)[:32] for x in symbols if x][:5]
            direction = str(s.get("direction", "")).lower() or None
            if direction and direction not in {"up", "down", "mixed", "uncertain"}:
                direction = "uncertain"
            out.append(Scenario(
                name=str(s["name"])[:255],
                condition=str(s["condition"])[:2000],
                reasoning=str(s.get("reasoning", ""))[:2000],
                candidate_symbols=symbols,
                direction=direction,
            ))
        log.info("living.scenarios", count=len(out), block=block_slug)

        # Quantify each scenario with real market data (best-effort)
        import asyncio
        from packages.ai.chain.quantify import quantify_scenario
        async def _quant(s: Scenario):
            try:
                q = await quantify_scenario(
                    name=s.name, condition=s.condition, reasoning=s.reasoning,
                    direction=s.direction, candidate_symbols=s.candidate_symbols,
                )
                s.quantified = {
                    "primary_symbol": q.primary_symbol,
                    "current_price": q.current_price,
                    "vol_30d_pct": q.vol_30d_pct,
                    "expected_move_low_pct": q.expected_move_low_pct,
                    "expected_move_high_pct": q.expected_move_high_pct,
                    "confidence": q.confidence,
                    "basis": q.basis,
                    "notes": q.notes,
                }
            except Exception as exc:
                log.warning("living.quantify_failed", scenario=s.name[:40], error=str(exc)[:200])
        await asyncio.gather(*(_quant(s) for s in out))

        return out
    except Exception as exc:
        log.warning("living.scenarios_failed", error=str(exc)[:200])
        return []


async def extract_watchlist(
    events: list[dict],
    impacts: list[Impact],
    *,
    org_id: str,
    block_slug: str,
) -> list[WatchItem]:
    try:
        prompt, _pv = render_with_version(
            "chain.watchlist",
            block_slug=block_slug,
            events=_format_events(events),
            impacts=_format_impacts(impacts),
        )
        raw = await complete(
            role="reasoning",
            system=None,
            user=prompt,
            org_id=org_id,
            prompt_version_id=_pv,
            purpose=f"chain.watchlist.{block_slug}",
            temperature=0.3,
            max_tokens=1500,
            json_mode=True,
            use_cache=True,
        )
        parsed = json.loads(raw)
        out: list[WatchItem] = []
        for w in parsed.get("watchlist") or []:
            if not isinstance(w, dict) or not w.get("label"):
                continue
            out.append(WatchItem(
                label=str(w["label"])[:255],
                symbol=(str(w["symbol"])[:32] if w.get("symbol") else None),
                reasoning=(str(w.get("reasoning", ""))[:1000] or None),
            ))
        log.info("living.watchlist", count=len(out), block=block_slug)

        # Fetch live quotes for items with symbols (best-effort, in parallel)
        import asyncio
        from packages.ingestion.sources.market_cache import get_quote_cached
        async def _fetch_quote(w: WatchItem):
            if not w.symbol:
                return
            try:
                q = await get_quote_cached(w.symbol)
                if q is not None and q.latest_price is not None:
                    w.live_quote = {
                        "symbol": w.symbol,
                        "current_price": q.latest_price,
                        "daily_change_abs": q.daily_change_abs,
                        "daily_change_pct": q.daily_change_pct,
                        "vol_30d_pct": q.volatility_30d_pct,
                        "as_of": q.as_of,
                    }
            except Exception as exc:
                log.warning("living.watchlist_quote_failed", symbol=w.symbol, error=str(exc)[:120])
        await asyncio.gather(*(_fetch_quote(w) for w in out))

        return out
    except Exception as exc:
        log.warning("living.watchlist_failed", error=str(exc)[:200])
        return []


async def run_living_enrichment(
    events: list[dict],
    *,
    org_id: str,
    block_slug: str,
) -> LivingEnrichment:
    """Run all three enrichments. Errors are captured, not raised."""
    result = LivingEnrichment()
    result.impacts = await extract_impacts(events, org_id=org_id, block_slug=block_slug)
    result.scenarios = await extract_scenarios(events, result.impacts, org_id=org_id, block_slug=block_slug)
    result.watchlist = await extract_watchlist(events, result.impacts, org_id=org_id, block_slug=block_slug)
    return result
