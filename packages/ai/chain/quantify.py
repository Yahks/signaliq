"""Quantify scenarios using real market data.

Design (A+B+C from design doc):
    A. expected_move_pct = ±1.5 × daily_vol_pct (sign from direction)
    B. confidence adjusts downward when vol is very high (>5% daily)
    C. sanity cap: |expected_move_pct| <= 20

Every number is sourced. If a symbol can't be resolved, quantitative
fields are null and the UI hides them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from packages.core.logging import get_logger
from packages.ingestion.sources.market_cache import get_quote_cached

log = get_logger(__name__)

# Sanity cap for scenarios — no single-day move claim above this
MAX_EXPECTED_MOVE_PCT = 20.0

# Multiplier on daily vol to express "1.5-sigma-ish" range
VOL_MULTIPLIER = 1.5


@dataclass
class QuantifiedScenario:
    """Numeric enrichment of one scenario."""
    primary_symbol: str | None = None
    current_price: float | None = None
    vol_30d_pct: float | None = None
    expected_move_low_pct: float | None = None
    expected_move_high_pct: float | None = None
    confidence: str = "low"  # low | medium | high
    basis: str = ""
    notes: list[str] = field(default_factory=list)


def _direction_sign(direction: str | None) -> int:
    """+1 for up, -1 for down, 0 for mixed/uncertain."""
    if direction == "up":
        return 1
    if direction == "down":
        return -1
    return 0


def _confidence_from_vol(vol_pct: float | None) -> str:
    """High vol → low confidence. Low vol → high confidence."""
    if vol_pct is None:
        return "low"
    if vol_pct > 5.0:
        return "low"
    if vol_pct > 2.5:
        return "medium"
    return "high"


def _cap_move(value: float) -> tuple[float, bool]:
    """Apply the sanity cap. Returns (capped_value, was_capped)."""
    if abs(value) > MAX_EXPECTED_MOVE_PCT:
        return (
            MAX_EXPECTED_MOVE_PCT if value > 0 else -MAX_EXPECTED_MOVE_PCT,
            True,
        )
    return value, False


async def quantify_scenario(
    *,
    name: str,
    condition: str,
    reasoning: str,
    direction: str | None,
    candidate_symbols: list[str],
) -> QuantifiedScenario:
    """Attempt to attach numbers to a scenario.

    Strategy:
        1. Resolve the primary symbol: first candidate that returns a quote.
        2. Compute expected move range: ±VOL_MULTIPLIER × daily_vol_pct.
        3. Adjust confidence by volatility (B).
        4. Cap by MAX_EXPECTED_MOVE_PCT (C).

    If no symbol resolves, returns an unquantified scenario (all None).
    """
    result = QuantifiedScenario(basis=f"Scenario: {name}")

    if not candidate_symbols:
        result.notes.append("No candidate symbols provided — qualitative only")
        return result

    quote = None
    resolved_symbol = None
    for sym in candidate_symbols:
        try:
            q = await get_quote_cached(sym)
            if q is not None and q.latest_price is not None:
                quote = q
                resolved_symbol = sym
                break
        except Exception as exc:
            log.warning("quantify.symbol_failed", symbol=sym, error=str(exc)[:120])
            continue

    if quote is None:
        result.notes.append(
            f"No market data available for candidates: {', '.join(candidate_symbols)}"
        )
        return result

    result.primary_symbol = resolved_symbol
    result.current_price = quote.latest_price
    result.vol_30d_pct = quote.volatility_30d_pct

    if quote.volatility_30d_pct is None:
        result.notes.append("Insufficient price history to compute volatility")
        return result

    # Compute directional range (A)
    sign = _direction_sign(direction)
    magnitude = quote.volatility_30d_pct * VOL_MULTIPLIER

    if sign == 0:
        # Mixed/uncertain → symmetric range
        low = -magnitude
        high = +magnitude
        result.notes.append("Direction is mixed/uncertain — reported as a symmetric range")
    else:
        # Directional → asymmetric (favors the direction)
        if sign > 0:
            low = 0.25 * magnitude
            high = magnitude
        else:
            low = -magnitude
            high = -0.25 * magnitude

    # Apply sanity cap (C)
    low, capped_low = _cap_move(low)
    high, capped_high = _cap_move(high)
    if capped_low or capped_high:
        result.notes.append(
            f"Expected move capped at ±{MAX_EXPECTED_MOVE_PCT:.0f}% (raw range exceeded)"
        )

    result.expected_move_low_pct = round(low, 2)
    result.expected_move_high_pct = round(high, 2)

    # Adjust confidence by volatility (B)
    result.confidence = _confidence_from_vol(quote.volatility_30d_pct)

    # Human-readable basis
    result.basis = (
        f"Based on {resolved_symbol}'s 30-day realized volatility "
        f"({quote.volatility_30d_pct:.2f}% daily). Expected range is "
        f"{VOL_MULTIPLIER}× daily σ. Confidence: {result.confidence}."
    )

    return result


def _format_quantified(q: QuantifiedScenario) -> str:
    """Compact string for the LLM prompt."""
    if q.primary_symbol is None or q.expected_move_low_pct is None:
        return "(no market data available)"
    return (
        f"symbol={q.primary_symbol} price={q.current_price:.4f} "
        f"vol_30d_pct={q.vol_30d_pct:.2f} "
        f"expected_move_pct=[{q.expected_move_low_pct:+.2f},{q.expected_move_high_pct:+.2f}] "
        f"confidence={q.confidence}"
    )
