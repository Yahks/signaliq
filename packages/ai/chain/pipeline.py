"""Full chain pipeline: select → order → link → narrate → verify.

Block-aware: reads block config to drive prompt overrides, target length,
and style. Uses shortlist selector to cap events sent to the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.ai.chain.chain_builder import SelectedChain, order_chain, select_chain
from packages.ai.chain.chain_linker import ChainLink, link_chain
from packages.ai.chain.narrator import NarratedPost, narrate
from packages.ai.chain.living import LivingEnrichment, run_living_enrichment
from packages.ai.chain.verifier import VerificationResult, verify
from packages.blocks.loader import BlockConfig
from packages.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class ChainPipelineResult:
    selected: SelectedChain | None = None
    sequence: list[str] = field(default_factory=list)
    links: list[ChainLink] = field(default_factory=list)
    post: NarratedPost | None = None
    verification: VerificationResult | None = None
    living: LivingEnrichment | None = None
    error: str | None = None
    block_slug: str | None = None
    subtopic_slug: str | None = None
    # S.6.1b.1b — verification outcome, independent of the editorial status
    # 'verified' | 'unverified' | 'error' | 'unknown'
    verification_status: str = "unknown"
    # True when a regen was attempted (useful for observability)
    regenerated: bool = False


async def run_chain_pipeline(
    *,
    events: list[dict],
    org_id: str,
    block: BlockConfig,
    subtopic_slug: str | None = None,
    target_words: int | None = None,
) -> ChainPipelineResult:
    """Run the full pipeline. Never raises — errors are captured in result.error."""
    result = ChainPipelineResult(block_slug=block.slug, subtopic_slug=subtopic_slug)

    # Sanitize all untrusted text fields in incoming events BEFORE they hit any LLM.
    # This strips prompt-injection patterns from user-supplied or fetched content.
    from packages.security.prompt_guard import sanitize_dict
    total_injections = 0
    sanitized_events = []
    for ev in events:
        cleaned, n = sanitize_dict(
            ev,
            text_fields=["headline", "summary", "location"],
            org_id=org_id,
        )
        # Also sanitize actors list
        if isinstance(cleaned.get("actors"), list):
            from packages.security.prompt_guard import sanitize
            cleaned["actors"] = [
                sanitize(str(a), org_id=org_id).text for a in cleaned["actors"]
            ]
        sanitized_events.append(cleaned)
        total_injections += n
    if total_injections:
        log.warning(
            "chain.pipeline.injections_stripped",
            org_id=org_id[:8],
            count=total_injections,
            events=len(events),
        )
    events = sanitized_events

    try:
        words = target_words or block.target_words()
        log.info(
            "chain.pipeline.start",
            events=len(events),
            org_id=org_id[:8],
            block=block.slug,
            subtopic=subtopic_slug,
            target_words=words,
        )

        # D3: select (uses block-specific prompt if present)
        selected = await select_chain(events, org_id=org_id, block_slug=block.slug)
        if not selected:
            result.error = "no_coherent_chain"
            return result
        result.selected = selected
        log.info(
            "chain.pipeline.selected",
            count=len(selected.event_ids),
            title=selected.title[:60],
        )

        # D4: order
        sequence = await order_chain(selected, events, org_id=org_id, block_slug=block.slug)
        result.sequence = sequence
        log.info("chain.pipeline.ordered", count=len(sequence))

        # D5: link
        links = await link_chain(sequence, events, org_id=org_id, block_slug=block.slug)
        result.links = links
        log.info("chain.pipeline.linked", count=len(links))

        # S.6.3.2 — coherence check.
        # If every link is 'precedes', the events are a timeline, not a
        # chain. A chain needs at least one structural relationship
        # (causes, escalates, responds_to, contradicts, corroborates,
        # contextualizes). A pure timeline reads as padding.
        STRUCTURAL = {"causes", "escalates", "responds_to",
                      "contradicts", "corroborates", "contextualizes"}
        structural_count = sum(1 for l in links if l.link_type in STRUCTURAL)
        if len(links) > 0 and structural_count == 0:
            log.info(
                "chain.pipeline.timeline_not_chain",
                events=len(sequence), links=len(links),
                link_types=[l.link_type for l in links],
            )
            result.error = "timeline_not_chain"
            return result

        # S.6.3.2 — short-chain word cap.
        # A chain with <= 2 distinct events can't sustain a full-length
        # article without padding. Cap the target.
        chain_words = words
        if len(sequence) <= 2:
            chain_words = min(words, 400)
            log.info(
                "chain.pipeline.short_chain_word_cap",
                events=len(sequence), target=chain_words, original=words,
            )

        # D6: narrate — passes block style_guide + example_output
        post = await narrate(
            thesis=selected.thesis,
            sequence=sequence,
            events=events,
            links=links,
            org_id=org_id,
            block=block,
            target_words=chain_words,
        )
        result.post = post

        # S.6.4.5 — belt-and-braces. If narrator.py's guard somehow
        # didn't fire, catch an empty post here before verify + persist.
        if post.word_count < 50:
            log.warning(
                "chain.pipeline.empty_narration",
                word_count=post.word_count,
                title=post.title[:60],
            )
            result.error = "empty_narration"
            return result

        # D7: verify + regenerate-on-unsupported (S.6.1b.1b)
        #
        # Policy (Reading A):
        #   verify clean          → verification_status='verified'
        #   1-3 unsupported       → regen once; if clean 'verified', else 'unverified'
        #   >3 unsupported        → 'unverified' (no regen — too far off)
        #   verify raised         → 'error' (post still persists)
        try:
            chain_event_ids = set(sequence)
            chain_events = [e for e in events if e["id"] in chain_event_ids]
            verification = await verify(
                body=post.body,
                events=chain_events,
                org_id=org_id,
                block_slug=block.slug,
            )
            result.verification = verification

            log.info(
                "chain.pipeline.verify_done",
                words=post.word_count,
                claims=len(verification.claims),
                unsupported=verification.unsupported_fact_count,
            )

            if verification.unsupported_fact_count == 0:
                result.verification_status = "verified"
            elif 1 <= verification.unsupported_fact_count <= 3:
                # One regeneration attempt with the flagged claims forbidden.
                forbidden = [
                    c.claim_text
                    for c in verification.claims
                    if c.unsupported and c.claim_type == "fact"
                ]
                log.info("chain.pipeline.regen_start", forbidden=len(forbidden))
                try:
                    post2 = await narrate(
                        thesis=selected.thesis,
                        sequence=sequence,
                        events=events,
                        links=links,
                        org_id=org_id,
                        block=block,
                        target_words=words,
                        forbidden_claims=forbidden,
                    )
                    verification2 = await verify(
                        body=post2.body,
                        events=chain_events,
                        org_id=org_id,
                        block_slug=block.slug,
                    )
                    # S.6.2.2 — keep the regen draft only if it's strictly
                    # better. On a tie or a loss, keep the original: the
                    # regen already cost ~2,000 tokens and produced no
                    # improvement, so the original is the honest choice.
                    before = verification.unsupported_fact_count
                    after = verification2.unsupported_fact_count
                    # S.6.4.5 — a regen that is shorter than 50 words is
                    # garbage, regardless of its unsupported count.
                    # Treat it as a loss: keep the original draft.
                    if post2.word_count < 50:
                        log.warning(
                            "chain.pipeline.regen_empty",
                            word_count=post2.word_count,
                        )
                        result.regenerated = False
                        result.verification_status = (
                            "verified"
                            if verification.unsupported_fact_count == 0
                            else "unverified"
                        )
                    elif after < before:
                        result.post = post2
                        result.verification = verification2
                        result.regenerated = True
                        log.info(
                            "chain.pipeline.regen_won",
                            before=before, after=after,
                        )
                    else:
                        # Keep the original post + verification.
                        result.regenerated = False
                        log.info(
                            "chain.pipeline.regen_lost",
                            before=before, after=after,
                        )
                    result.verification_status = (
                        "verified"
                        if result.verification.unsupported_fact_count == 0
                        else "unverified"
                    )
                except Exception as exc:
                    # Regen failed (LLM error, timeout). Keep the first draft
                    # and mark unverified so a human can review.
                    log.warning("chain.pipeline.regen_failed", error=str(exc)[:200])
                    result.verification_status = "unverified"
            else:
                # >3 unsupported — don't attempt regen
                log.warning(
                    "chain.pipeline.too_many_unsupported",
                    count=verification.unsupported_fact_count,
                )
                result.verification_status = "unverified"
        except Exception as exc:
            # Verify itself failed (LLM error, timeout, credentials).
            # Keep the post but tag it so callers know it was never checked.
            log.warning("chain.verify.failed", error=str(exc)[:200])
            result.verification = None
            result.verification_status = "error"
            result.error = f"verify_failed:{str(exc)[:100]}"

        # D8: living enrichment — impacts, scenarios, watchlist (best-effort)
        try:
            chain_event_ids = set(sequence)
            chain_events = [e for e in events if e["id"] in chain_event_ids]
            result.living = await run_living_enrichment(
                chain_events,
                org_id=org_id,
                block_slug=block.slug,
            )
            log.info(
                "chain.pipeline.living_done",
                impacts=len(result.living.impacts),
                scenarios=len(result.living.scenarios),
                watchlist=len(result.living.watchlist),
            )
        except Exception as exc:
            log.warning("chain.pipeline.living_failed", error=str(exc)[:200])

        return result

    except Exception as exc:
        log.exception("chain.pipeline.failed")
        result.error = f"pipeline_failed:{exc}"
        return result
