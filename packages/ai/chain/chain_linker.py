"""D5: Infer link types between consecutive events."""
from __future__ import annotations

import json
from dataclasses import dataclass

from packages.ai.prompts.loader import render, render_with_version
from packages.ai.router import complete
from packages.core.logging import get_logger

log = get_logger(__name__)

VALID_LINK_TYPES = {
    "causes", "precedes", "contradicts", "escalates",
    "contextualizes", "responds_to", "corroborates",
}


@dataclass
class ChainLink:
    from_event_id: str
    to_event_id: str
    link_type: str
    rationale: str
    confidence: float
    sequence: int


def _format_pairs(sequence: list[str], by_id: dict[str, dict]) -> str:
    lines = []
    for i in range(len(sequence) - 1):
        a = by_id[sequence[i]]
        b = by_id[sequence[i + 1]]
        lines.append(
            f"PAIR {i+1}:\n"
            f"  from: id={a['id']}  headline={a['headline']}  status={a.get('status')}\n"
            f"  to:   id={b['id']}  headline={b['headline']}  status={b.get('status')}\n"
            f"  from_summary: {a['summary'][:300]}\n"
            f"  to_summary: {b['summary'][:300]}"
        )
    return "\n\n".join(lines)


async def link_chain(
    sequence: list[str],
    events: list[dict],
    *,
    org_id: str,
    block_slug: str | None = None,
    use_cache: bool = True,
) -> list[ChainLink]:
    """Infer one link per consecutive pair in the sequence."""
    by_id = {e["id"]: e for e in events}
    if len(sequence) < 2:
        return []

    prompt, _pv = render_with_version("chain.link", block_slug=block_slug, pairs=_format_pairs(sequence, by_id))

    # S.6.2.2a — same retry discipline as order_chain.
    raw = None
    for _attempt in range(2):
        try:
            raw = await complete(
                role="fast",       # S.6.2.1 — moves ~1,300 TPM off the
                                   # reasoning cap
                system=None,
                user=prompt,
                org_id=org_id,
                prompt_version_id=_pv,
                purpose="chain.link",
                temperature=0.2,
                max_tokens=2500,   # S.6.2.2a — was 2000; more headroom
                json_mode=True,
                use_cache=use_cache,
            )
            break
        except Exception as _exc:
            msg = str(_exc)
            if "json_validate_failed" in msg or "Failed to validate JSON" in msg:
                if _attempt == 0:
                    log.warning("chain.link.retry",
                                reason="json_validate_failed")
                    continue
            raise
    if raw is None:
        raise RuntimeError("link_chain: retry loop exited without result")

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("expected array")

        # S.6.2.3a — defensive normalisation. The fast model sometimes
        # returns "id=<uuid>" or "uuid:<uuid>" for `from`/`to` instead
        # of the bare uuid. Strip those prefixes; keep the string.
        def _clean_id(v):
            s = str(v or "").strip()
            for prefix in ("id=", "uuid:", "event_id=", "id:", "id "):
                if s.lower().startswith(prefix):
                    s = s[len(prefix):].strip()
                    break
            return s

        for item in parsed:
            if isinstance(item, dict):
                item["from"] = _clean_id(item.get("from"))
                item["to"] = _clean_id(item.get("to"))
    except (json.JSONDecodeError, ValueError):
        log.warning("chain.link.parse_failed", snippet=raw[:200])
        # Fallback: link everything as "precedes" with low confidence
        return [
            ChainLink(
                from_event_id=sequence[i],
                to_event_id=sequence[i + 1],
                link_type="precedes",
                rationale="(fallback — LLM parse failed)",
                confidence=0.3,
                sequence=i,
            )
            for i in range(len(sequence) - 1)
        ]

    links: list[ChainLink] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        link_type = str(item.get("link_type", "precedes")).lower()
        if link_type not in VALID_LINK_TYPES:
            link_type = "precedes"
        links.append(ChainLink(
            from_event_id=str(item.get("from", sequence[i] if i < len(sequence) else "")),
            to_event_id=str(item.get("to", sequence[i + 1] if i + 1 < len(sequence) else "")),
            link_type=link_type,
            rationale=str(item.get("rationale", ""))[:1000],
            confidence=float(item.get("confidence", 0.5)),
            sequence=i,
        ))

    # Ensure every consecutive pair has a link (fill gaps)
    covered = {(l.from_event_id, l.to_event_id) for l in links}
    for i in range(len(sequence) - 1):
        key = (sequence[i], sequence[i + 1])
        if key not in covered:
            links.append(ChainLink(
                from_event_id=sequence[i],
                to_event_id=sequence[i + 1],
                link_type="precedes",
                rationale="(inferred temporal order)",
                confidence=0.3,
                sequence=i,
            ))
    links.sort(key=lambda l: l.sequence)
    return links
