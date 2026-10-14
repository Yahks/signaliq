"""D6: Narrate the chain into a long-form article.

Block-aware: uses block style_guide and example_output if provided.
For trading block, uses the 5-section structure.
"""
from __future__ import annotations

from dataclasses import dataclass

from packages.ai.prompts.loader import render, render_with_version
from packages.ai.router import complete
from packages.blocks.loader import BlockConfig
from packages.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class NarratedPost:
    title: str
    body: str
    word_count: int


def _format_events_with_links(
    sequence: list[str],
    by_id: dict[str, dict],
    links: list,
) -> str:
    link_by_seq = {l.sequence: l for l in links}
    parts = []
    for i, eid in enumerate(sequence):
        ev = by_id[eid]
        block = [
            f"EVENT {i+1}: {ev['headline']}",
            f"  summary: {ev['summary']}",
            f"  actors: {', '.join(ev.get('actors') or [])}",
            f"  occurred_at: {ev.get('occurred_at') or 'unknown'}",
            f"  status: {ev.get('status', 'unknown')}",
            f"  event_type: {ev.get('event_type', 'other')}",
        ]
        if ev.get("location"):
            block.append(f"  location: {ev['location']}")
        if ev.get("sources"):
            sources_str = ", ".join(
                f"{s.get('name', '?')} ({s.get('type', 'primary')})"
                for s in ev["sources"][:5]
            )
            block.append(f"  sources: {sources_str}")
        if i > 0:
            prev_link = link_by_seq.get(i - 1)
            if prev_link:
                block.append(
                    f"  ← relationship to previous: {prev_link.link_type.upper()}"
                    f"  ({prev_link.rationale})"
                )
        parts.append("\n".join(block))
    return "\n\n".join(parts)


async def narrate(
    *,
    thesis: str,
    sequence: list[str],
    events: list[dict],
    links: list,
    org_id: str,
    block: BlockConfig,
    target_words: int | None = None,
    use_cache: bool = False,
    forbidden_claims: list[str] | None = None,
) -> NarratedPost:
    """Generate the long-form article using the block's config.

    forbidden_claims: if provided, the prompt is extended with an explicit
    block forbidding these claims. Used by the regen-on-unsupported path.
    """
    by_id = {e["id"]: e for e in events}
    events_with_links = _format_events_with_links(sequence, by_id, links)

    words = target_words or block.target_words()

    # Build render kwargs. Some prompts need style_guide/example_output; the base
    # news narrate doesn't — extra kwargs are just ignored by the simple renderer.
    prompt, _pv = render_with_version(
        "chain.narrate",
        block_slug=block.slug,
        thesis=thesis or "(no explicit thesis)",
        events_with_links=events_with_links,
        target_words=str(words),
        style_guide=block.style_guide or "(no style guide)",
        example_output=block.example_output or "(no example)",
    )

    if forbidden_claims:
        bullets = "\n".join(f"- {c}" for c in forbidden_claims)
        prompt = (
            prompt
            + "\n\nIMPORTANT — a previous draft contained these claims that"
              " were NOT supported by the evidence. Do NOT restate any of"
              " them, in any form. If a sentence cannot be written without"
              " one of them, omit the sentence entirely.\n"
            + bullets
            + "\n\nWrite the article now, avoiding the claims above:"
        )

    raw = await complete(
        role="reasoning",
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose=f"chain.narrate.{block.slug}",
        temperature=0.4,
        max_tokens=3000,
        json_mode=False,
        use_cache=use_cache,
    )

    body = raw.strip()
    title = "Untitled"
    if body.startswith("# "):
        lines = body.split("\n", 1)
        title = lines[0][2:].strip()[:500] or "Untitled"
        body = lines[1].strip() if len(lines) > 1 else body
    word_count = len(body.split())

    # S.6.4.5 — refuse to return an empty post. The pipeline downstream
    # would otherwise persist a 0-word draft to the review queue.
    if word_count < 50:
        raise RuntimeError(
            f"narrate produced an unusably short body: "
            f"{word_count} words, {len(body or '')} chars. "
            f"block={block.slug} title={title[:60]!r}"
        )

    log.info("chain.narrate.done", block=block.slug, title=title[:60], words=word_count)
    return NarratedPost(title=title, body=body, word_count=word_count)
