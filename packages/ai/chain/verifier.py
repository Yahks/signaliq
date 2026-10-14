"""D7: Verify each factual claim in the post links back to evidence."""
from __future__ import annotations

import json
from dataclasses import dataclass

from packages.ai.prompts.loader import render, render_with_version
from packages.ai.router import complete
from packages.core.logging import get_logger

log = get_logger(__name__)

VALID_CLAIM_TYPES = {"fact", "inference", "hypothesis", "recommendation"}


@dataclass
class ClaimEvidence:
    claim_text: str
    claim_type: str
    supported_by_event_ids: list[str]
    confidence: float
    unsupported: bool


@dataclass
class VerificationResult:
    claims: list[ClaimEvidence]
    unsupported_fact_count: int


def _format_evidence(events: list[dict]) -> str:
    lines = []
    for e in events:
        lines.append(
            f"event_id={e['id']}\n"
            f"  headline: {e['headline']}\n"
            f"  summary: {e['summary']}\n"
            f"  status: {e.get('status', 'unknown')}"
        )
    return "\n\n".join(lines)


async def verify(
    *,
    body: str,
    events: list[dict],
    org_id: str,
    block_slug: str | None = None,
    use_cache: bool = False,
) -> VerificationResult:
    """Audit the post body against the events."""
    # S.6.3.5a — verify prompt grew with the classification taxonomy.
    # Cap the body at 5000 chars (was 8000) so the combined prompt +
    # max_tokens fits the fast model's 8,192-token context window.
    body_capped = body[:5000] if body else ""
    prompt, _pv = render_with_version(
        "chain.verify",
        block_slug=block_slug,
        body=body_capped,
        evidence=_format_evidence(events),
    )

    # Verification is a bounded task — use the faster/cheaper model.
    # The reasoning model runs 4 calls back-to-back (select/order/link/narrate)
    # and would hit TPM limits if we added a 5th call. fast model handles this fine.
    # S.6.3.5a — cap output at 6000 so prompt (~1,200 tokens) + output
    # (max 6,000) = 7,200, under the fast model's 8,192 context. Still
    # enough for 20 claim objects (~40-60 tokens each = ~1,200 tokens).
    log.info(
        "chain.verify.call",
        body_chars=len(body_capped),
        prompt_chars=len(prompt),
        max_tokens=6000,
        block=block_slug,
    )

    raw = await complete(
        role="fast",
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose=f"chain.verify.{block_slug or 'default'}",
        temperature=0.1,
        max_tokens=6000,   # S.6.3.5a — was 8000; prompt grew, so this
                           # keeps prompt+output under the model's context
        json_mode=True,
        use_cache=use_cache,
    )

    log.info("chain.verify.result", output_chars=len(raw or ""))

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("expected array")
    except (json.JSONDecodeError, ValueError):
        log.warning("chain.verify.parse_failed", snippet=raw[:200])
        return VerificationResult(claims=[], unsupported_fact_count=0)

    # Hard ceiling — the prompt asks for ≤20; enforce it even if the model
    # produces more. Extra claims past #20 are dropped, not parsed.
    if len(parsed) > 20:
        log.info("chain.verify.parser_capped", produced=len(parsed), kept=20)
        parsed = parsed[:20]

    valid_ids = {e["id"] for e in events}
    claims: list[ClaimEvidence] = []
    unsupported_facts = 0

    for item in parsed:
        if not isinstance(item, dict):
            continue
        claim_type = str(item.get("claim_type", "inference")).lower()
        if claim_type not in VALID_CLAIM_TYPES:
            claim_type = "inference"
        supported = [str(eid) for eid in (item.get("supported_by_event_ids") or [])]
        supported = [eid for eid in supported if eid in valid_ids]
        is_unsupported = bool(item.get("unsupported")) or (
            claim_type == "fact" and len(supported) == 0
        )
        if is_unsupported and claim_type == "fact":
            unsupported_facts += 1
        claims.append(ClaimEvidence(
            claim_text=str(item.get("claim_text", ""))[:2000],
            claim_type=claim_type,
            supported_by_event_ids=supported,
            confidence=float(item.get("confidence", 0.5)),
            unsupported=is_unsupported,
        ))

    log.info("chain.verify.done", claims=len(claims), unsupported=unsupported_facts)
    return VerificationResult(claims=claims, unsupported_fact_count=unsupported_facts)
