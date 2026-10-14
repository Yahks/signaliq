"""Rewrite structured, verified articles as human prose.

The generation pipeline produces a structured draft (markdown tables,
headers, bullets, numbered sections). It's factually correct and
verified. It also reads like a data dump.

`humanize()` takes that verified draft and rewrites it as prose. Same
facts, same numbers, same verification trail — but written for a human
reader who is following markets without being a professional.

Rules the directive enforces:

  1. No markdown. No # headers, no 1. 2. 3. numbered lists, no -
     bullets. Paragraphs only.
  2. Plain-language lede. 1-2 sentences a retail investor reads and
     understands. No jargon in the opening.
  3. Mainstream middle. Every technical term defined inline in one
     phrase. "Rotation, meaning money is moving from one sector to
     another."
  4. Credible close. The professional layer — spreads, positioning,
     what to watch. Dense with numbers, no jargon.
  5. No AI tells. No "firstly", "in conclusion", "it's worth noting",
     "let's dive in", "in today's market", "as we can see".
  6. Human cadence. Varying sentence length. Direct address where it
     fits ("if you own tech, this is what today's move means for
     you").
  7. No new facts. Every number in the output must come from the
     source. Restyle, do not extend.
  8. Target length: within 15% of the source's word count. Do not
     pad, do not cut.

Failures return None. The caller keeps the structured draft if
humanize fails — better a data dump than nothing.
"""
from __future__ import annotations

from packages.core.logging import get_logger

log = get_logger(__name__)


_DIRECTIVE_CORE = """\
You are a writer, not an analyst. You will receive a structured
article that a fact-checker has already verified. Your only job is to
rewrite it as human prose.

Rules, in order of importance:

1. NO MARKDOWN. No `#` headers, no `##` subheaders, no `1. 2. 3.`
   numbered lists, no `-` or `*` bullet points, no bold (`**`), no
   tables. Only paragraphs. Separate paragraphs with one blank line.

2. Open with a plain-language lede: one or two sentences that a
   regular investor would understand without knowing any trading
   jargon. State what happened. Do not open with a number, a ticker,
   or a phrase like "In today's market" or "The S&P 500 opened at".

3. Every technical term, when first used, gets a one-phrase inline
   definition. Examples:
     "the VIX, a measure of how much investors expect prices to swing"
     "rotation, meaning money is moving from one sector to another"
     "duration sensitivity, or how much a stock's price reacts to
      interest rates"
   The reader is a mainstream retail investor. They follow markets,
   they are not a professional trader.

4. End with a professional close. The last 2-3 paragraphs should be
   dense with figures — spreads, comparisons, positioning — because
   that is what earns credibility with the sophisticated reader. No
   jargon there either, just real numbers.

5. Ban every AI cliche:
     "firstly", "secondly", "finally"
     "in conclusion", "to sum up", "in summary"
     "it's worth noting", "it's important to note"
     "let's dive in", "let's explore", "let's unpack"
     "in today's market", "in the current climate"
     "as we can see", "one thing is clear"
     "delve into", "navigate the landscape", "landscape"
     "unlock the potential", "the world of"
   If any of these appear, rewrite that sentence.

6. Vary sentence length. A short sentence (three or four words) after
   a long one carries weight. Occasional direct address — "if you
   hold tech, this is what today's move means for you" — breaks the
   analytic monotone.

7. Do NOT introduce new facts. Every number, every company name,
   every ticker, every claim must appear in the source you were
   given. You may rephrase, reorder, and restyle. You may not add.

8. Target length: within 15% of the source's word count. Do not pad.
   Do not cut.

Write only the article. No preamble. No meta-commentary. No notes to
the reader.
"""


_VOICE_OVERLAY = {
    "wire": (
        "Voice: a data-forward newswire. Direct, spare, no narrative. "
        "The analyst's presence is felt in what is chosen to be said, "
        "not in personality."
    ),
    "analyst": (
        "Voice: a sell-side strategist writing to institutional "
        "allocators. Measured, confident, and comfortable with "
        "risk/reward framing. Write as someone who has seen this "
        "before."
    ),
    "contrarian": (
        "Voice: a contrarian. The standard reading is stated, then "
        "questioned. Every challenge is grounded in a specific number "
        "from the source."
    ),
    "retail": (
        "Voice: a plain-language guide for ordinary investors. Assume "
        "the reader has money in the market but is not a professional. "
        "Warm, patient, never condescending."
    ),
    "academic": (
        "Voice: a financial economist. Hedged, structured, comfortable "
        "with uncertainty. 'Consistent with', 'suggests', 'may "
        "reflect' rather than assertions."
    ),
}


def _compose_system(voice: str | None) -> str:
    parts = [_DIRECTIVE_CORE]
    if voice and voice in _VOICE_OVERLAY:
        parts.append(_VOICE_OVERLAY[voice])
    return "\n\n".join(parts)


async def humanize(
    session,
    *,
    org_id: str,
    structured_body: str,
    voice: str | None = None,
) -> str | None:
    """Rewrite a structured, verified article as human prose.

    Returns the humanized body on success. Returns None if the call
    fails or produces an empty response — the caller is expected to
    fall back to the structured body.
    """
    if not structured_body or not structured_body.strip():
        return None

    from packages.ai.router import complete

    system = _compose_system(voice)
    user = (
        "Rewrite the following verified article as human prose. "
        "Apply every rule in your instructions.\n\n"
        "---\n\n"
        + structured_body
    )

    try:
        text = await complete(
            role="reasoning",
            system=system,
            user=user,
            org_id=org_id,
            purpose="trading.humanize",
            temperature=0.7,
            max_tokens=6000,
            json_mode=False,
            use_cache=False,
        )
    except Exception as exc:
        log.warning(
            "humanize.failed",
            error=str(exc)[:200],
            error_type=type(exc).__name__,
        )
        return None

    text = (text or "").strip()
    if not text:
        log.warning("humanize.empty")
        return None

    # Quick sanity check: if the output still has markdown headers
    # or bullets, the model didn't follow the directive. Return None
    # so the caller keeps the structured draft.
    lower_first_200 = text[:200]
    if any(lower_first_200.startswith("#") for _ in [1]):
        log.warning("humanize.still_has_markdown", preview=text[:120])
        # Don't return None for a single stray markdown — the body
        # may still be prose. Just warn.
    log.info("humanize.ok", chars=len(text))
    return text
