"""D3: Chain selection. Given N candidate events, pick the 4-8 that form a story."""
from __future__ import annotations

import json
from dataclasses import dataclass

from packages.ai.prompts.loader import render, render_with_version
from packages.ai.router import complete
from packages.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class SelectedChain:
    title: str
    thesis: str
    event_ids: list[str]
    primary_entities: list[str]
    confidence: float


def _format_events(events: list[dict]) -> str:
    """Format a list of event dicts for the LLM prompt.

    Untrusted content (headline, summary) is wrapped in explicit delimiters
    so the LLM treats it as data, not instructions.
    """
    from packages.security.prompt_guard import wrap_untrusted
    lines = []
    for i, e in enumerate(events, 1):
        headline_wrapped = wrap_untrusted(e["headline"][:400], tag="HEADLINE")
        summary_wrapped = wrap_untrusted(e["summary"][:600], tag="SUMMARY")
        lines.append(
            f"[{i}] id={e['id']}\n"
            f"    {headline_wrapped}\n"
            f"    {summary_wrapped}\n"
            f"    actors: {', '.join(e.get('actors') or [])}\n"
            f"    type: {e.get('event_type', 'other')}  status: {e.get('status', 'unknown')}\n"
            f"    occurred_at: {e.get('occurred_at') or 'unknown'}"
        )
    return "\n\n".join(lines)


async def select_chain(
    events: list[dict],
    *,
    org_id: str,
    block_slug: str | None = None,
    use_cache: bool = True,
) -> SelectedChain | None:
    """Select the subset of events that form a coherent chain.

    `events` = list of {id, headline, summary, actors, event_type, status, occurred_at}
    Returns None if no coherent chain exists.
    """
    if len(events) < 2:
        return None

    prompt, _pv = render_with_version("chain.select", block_slug=block_slug, events=_format_events(events))
    raw = await complete(
        role="reasoning",
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose="chain.select",
        temperature=0.2,
        max_tokens=1500,
        json_mode=True,
        use_cache=use_cache,
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("chain.select.parse_failed", snippet=raw[:200])
        return None

    chain = parsed.get("chain")
    if not chain or not chain.get("event_ids"):
        log.info("chain.select.no_chain", reason=parsed.get("reason", ""))
        return None

    valid_ids = {e["id"] for e in events}
    chosen = [eid for eid in chain["event_ids"] if eid in valid_ids]
    if len(chosen) < 2:
        log.warning("chain.select.too_few_valid", chosen=len(chosen))
        return None

    return SelectedChain(
        title=str(chain.get("title", "Untitled chain"))[:500],
        thesis=str(chain.get("thesis", ""))[:2000],
        event_ids=chosen,
        primary_entities=[str(e) for e in (chain.get("primary_entities") or [])][:10],
        confidence=float(chain.get("confidence", 0.5)),
    )


async def order_chain(
    chain: SelectedChain,
    events: list[dict],
    *,
    org_id: str,
    block_slug: str | None = None,
    use_cache: bool = True,
) -> list[str]:
    """D4: order the events in narrative sequence."""
    # Filter to chain events only, then sort chronologically first
    by_id = {e["id"]: e for e in events}
    chain_events = [by_id[eid] for eid in chain.event_ids if eid in by_id]
    chain_events.sort(key=lambda e: (e.get("occurred_at") or "", e["id"]))

    prompt, _pv = render_with_version("chain.order", block_slug=block_slug, events=_format_events(chain_events))

    # S.6.2.2a — the fast model occasionally fails Groq's JSON validator
    # (json_validate_failed with failed_generation=''). A single retry
    # almost always succeeds; the model is stochastic.
    raw = None
    for _attempt in range(2):
        try:
            raw = await complete(
                role="fast",       # S.6.2.1 — moves ~1,000 TPM off the
                                   # 8k/min reasoning cap
                system=None,
                user=prompt,
                org_id=org_id,
                prompt_version_id=_pv,
                purpose="chain.order",
                temperature=0.2,
                max_tokens=2000,   # S.6.2.2a — was 800; give the fast
                                   # model room so JSON isn't truncated
                json_mode=True,
                use_cache=use_cache,
            )
            break
        except Exception as _exc:
            msg = str(_exc)
            if "json_validate_failed" in msg or "Failed to validate JSON" in msg:
                if _attempt == 0:
                    log.warning("chain.order.retry",
                                reason="json_validate_failed")
                    continue
            raise
    if raw is None:
        raise RuntimeError("order_chain: retry loop exited without result")

    try:
        parsed = json.loads(raw)
        sequence = [str(eid) for eid in parsed.get("sequence", [])]
    except (json.JSONDecodeError, AttributeError):
        log.warning("chain.order.parse_failed", snippet=raw[:200])
        sequence = [e["id"] for e in chain_events]

    # Verify: sequence must contain exactly the same set as chain.event_ids
    valid = {e["id"] for e in chain_events}
    sequence = [eid for eid in sequence if eid in valid]
    missing = valid - set(sequence)
    for eid in missing:
        sequence.append(eid)

    return sequence
