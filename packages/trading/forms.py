"""Article forms and tones for trading subblock rotation.

Each generation picks a (form, tone) pair from the tables below. The
pair rotates: the subblock's last N posts are inspected, and the next
pair is the first one not recently used. This is the same LRU pattern
as topic rotation in _llm_shared.pick_next_topic.

Purpose: the event is the same, the voice is per-org, but the *shape*
and *manner* of the article should vary so consecutive posts do not
read as the same article with a different title. Rotating form also
changes the system prompt, which changes the cache key, which means
consecutive identical events produce genuinely different articles
instead of cache hits.

Forms (the article's shape):
  explainer, deep_dive, opinion, blog_post, case_study, versus,
  roundup, myth_busting, guide, tutorial, checklist, comparison,
  news_article, feature_story, editorial, essay, analytical,
  narrative, data_driven

Tones (the article's manner):
  neutral, analytical, conversational, warm, academic, contrarian,
  formal, reflective, motivational, objective, storytelling,
  practical, beginner_friendly, expert_view
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession


# ─────────────────────────────────────────────────────────────────────
# Form directives — the *shape* of the article
# ─────────────────────────────────────────────────────────────────────

FORMS: dict[str, str] = {
    "explainer": (
        "Form: explainer.\n"
        "  Title: pose the question the reader is implicitly asking "
        "('Why the Dow fell while the Nasdaq rallied'). Do NOT start "
        "the title with an index percentage.\n"
        "  Opening: do NOT open with the index table or the closing "
        "numbers. Open with the puzzle — what looks contradictory in "
        "this data. Then resolve it using the numbers.\n"
        "  Body: define any technical term in one short phrase. End "
        "with a plain-language 'so what does this mean?'"
    ),
    "deep_dive": (
        "Form: deep dive.\n"
        "  Title: name the mechanism you are unpacking "
        "('The breadth behind the tech rally').\n"
        "  Opening: do NOT open with the index percentages. Open with "
        "the single most non-obvious relationship in the data — the "
        "one a casual reader would miss.\n"
        "  Body: trace how one move reinforces or offsets another. "
        "Build a layered argument; do not summarise."
    ),
    "opinion": (
        "Form: opinion piece.\n"
        "  Title: state the position plainly "
        "('This is a narrow rally, not a broad one').\n"
        "  Opening: do NOT open with numbers. Open with the claim.\n"
        "  Body: defend the claim with figures. It is fine to disagree "
        "with the obvious reading — every claim must cite a number."
    ),
    "blog_post": (
        "Form: blog post.\n"
        "  Title: conversational and curiosity-driven "
        "('What today's split market is really telling us'). "
        "No percentages in the title.\n"
        "  Opening: do NOT open with the index table. Open with a "
        "short observation or question.\n"
        "  Body: short paragraphs, one idea each. Close with a line "
        "that invites the reader to think about what comes next."
    ),
    "case_study": (
        "Form: case study.\n"
        "  Title: name the pattern being studied "
        "('A 17-point tech-utilities gap: what it says').\n"
        "  Opening: do NOT open with the index summary. Open with the "
        "single most instructive pattern in the data.\n"
        "  Body: walk through that pattern step by step. Draw a "
        "transferable lesson at the end."
    ),
    "versus": (
        "Form: versus article.\n"
        "  Title: 'X vs Y' — name the two readings "
        "('Risk-on rotation vs one-sector rally').\n"
        "  Opening: do NOT open with the numbers. Open with the two "
        "opposing readings of the same data.\n"
        "  Body: lay out what each reading gets right, then conclude "
        "with which one the numbers currently favour."
    ),
    "roundup": (
        "Form: roundup.\n"
        "  Title: name the sweep "
        "('Five things today's market told us'). No percentages in "
        "the title.\n"
        "  Opening: do NOT open with a narrative paragraph. Open with "
        "the first bullet.\n"
        "  Body: move through the key figures one by one, briefly. "
        "Group them so the reader can scan."
    ),
    "myth_busting": (
        "Form: myth-busting.\n"
        "  Title: name the myth ('The 'risk-on' label doesn't fit "
        "today').\n"
        "  Opening: do NOT open with numbers. State the myth in one "
        "sentence.\n"
        "  Body: show what the numbers actually say. End with the "
        "corrected reading."
    ),
    "guide": (
        "Form: guide.\n"
        "  Title: name the goal ('How to read a split market day').\n"
        "  Opening: do NOT open with the index summary. Open with what "
        "the reader will be able to do after reading.\n"
        "  Body: walk through the figures in the order a practitioner "
        "would check them."
    ),
    "tutorial": (
        "Form: tutorial walkthrough.\n"
        "  Title: name the method ('Reading today's data in three "
        "steps').\n"
        "  Opening: do NOT open with numbers. Open with the first "
        "step.\n"
        "  Body: step the reader through reading the data — 'first "
        "look at X, then compare to Y, then notice Z'. Make the "
        "method explicit."
    ),
    "checklist": (
        "Form: checklist.\n"
        "  Title: name the list ('Five signals to check after a "
        "split market close').\n"
        "  Opening: do NOT open with a paragraph. Open with item 1.\n"
        "  Body: a short series of observations, each one a sentence, "
        "each grounded in a figure."
    ),
    "comparison": (
        "Form: comparison.\n"
        "  Title: name the two things being compared.\n"
        "  Opening: do NOT open with the index table. Open with the "
        "two items side by side.\n"
        "  Body: let the contrast do the interpretive work."
    ),
    "news_article": (
        "Form: news article.\n"
        "  Title: the most important fact, stated briefly.\n"
        "  Opening: inverted pyramid — the most newsworthy number "
        "first, then context, then the wider picture.\n"
        "  Body: objective, no first person. This is the form that "
        "legitimately opens with a number."
    ),
    "feature_story": (
        "Form: feature story.\n"
        "  Title: a hook, not a summary.\n"
        "  Opening: do NOT open with a summary paragraph. Open with "
        "the single most striking number and let the piece unfold "
        "around it.\n"
        "  Body: narrative arc matters more than enumeration."
    ),
    "editorial": (
        "Form: editorial.\n"
        "  Title: the stance.\n"
        "  Opening: do NOT open with the index summary. Open with the "
        "position.\n"
        "  Body: argue for the position. Every sentence grounded in a "
        "number from the source."
    ),
    "essay": (
        "Form: essay.\n"
        "  Title: a theme, not a summary.\n"
        "  Opening: do NOT open with numbers. Open with the central "
        "observation.\n"
        "  Body: two or three paragraphs developing that observation; "
        "the numbers serve the argument rather than driving it."
    ),
    "analytical": (
        "Form: analytical note.\n"
        "  Title: name the analysis.\n"
        "  Opening: do NOT open with the headline percentages. Open "
        "with the decomposition — the components you are about to "
        "break the move into.\n"
        "  Body: decompose, quantify, reassemble."
    ),
    "narrative": (
        "Form: narrative.\n"
        "  Title: name the story.\n"
        "  Opening: do NOT open with a table. Open with the sequence "
        "of moves — who led, who lagged, how the balance shifted.\n"
        "  Body: tell the day's session as a story. No first person."
    ),
    "data_driven": (
        "Form: data-driven piece.\n"
        "  Title: the key finding.\n"
        "  Opening: the strongest number, stated plainly.\n"
        "  Body: every sentence names a number from the source. "
        "Minimal prose between the figures."
    ),
}

# ─────────────────────────────────────────────────────────────────────
# Tone directives — the *manner* of the article
# ─────────────────────────────────────────────────────────────────────

TONES: dict[str, str] = {
    "neutral": "Tone: neutral and objective. No first person. No opinion.",
    "analytical": "Tone: analytical. Focus on relationships between numbers.",
    "conversational": (
        "Tone: conversational. Address the reader directly, as if "
        "walking them through the data."
    ),
    "warm": (
        "Tone: warm. Plain, patient, and considerate of a reader who "
        "is new to the subject."
    ),
    "academic": (
        "Tone: academic. Hedged language — 'consistent with', "
        "'suggests', 'may reflect'. No predictions."
    ),
    "contrarian": (
        "Tone: contrarian. Question the obvious reading. Every "
        "alternative must be supported by a number in the source."
    ),
    "formal": "Tone: formal. No contractions, no colloquialisms.",
    "reflective": (
        "Tone: reflective. Look back at what the numbers imply over a "
        "slightly longer horizon than the single session."
    ),
    "motivational": (
        "Tone: motivational, but sober. Focus on what a reader can do "
        "with the information, not on hype."
    ),
    "objective": "Tone: objective. Present the facts plainly. No stance.",
    "storytelling": (
        "Tone: storytelling. Give the numbers a sense of sequence and "
        "consequence."
    ),
    "practical": (
        "Tone: practical. Emphasize what a reader should watch next."
    ),
    "beginner_friendly": (
        "Tone: beginner-friendly. Define any term before using it. "
        "Short sentences."
    ),
    "expert_view": (
        "Tone: expert. Assume the reader knows the basics. Skip "
        "definitions. Go straight to the substantive point."
    ),
}


# S.7.8b-4.2 — concept-mode tone overrides. Applied when
# compose_system_prompt is called with slug in CONCEPT_SUBBLOCKS.
# Only the tones that reference "numbers" or "the data" need a variant;
# the rest work identically for data and concept subblocks.
TONES_CONCEPT_OVERRIDES: dict[str, str] = {
    "analytical": (
        "Tone: analytical. Focus on structure, distinctions, and where "
        "the concept breaks down."
    ),
    "conversational": (
        "Tone: conversational. Address the reader directly, as if "
        "explaining the idea over coffee."
    ),
    "contrarian": (
        "Tone: contrarian. Question the obvious reading. Every "
        "alternative must be a defensible position, not a strawman."
    ),
    "reflective": (
        "Tone: reflective. Look at the concept over a longer horizon: "
        "how has the understanding of it changed, and why."
    ),
    "storytelling": (
        "Tone: storytelling. Give the explanation a sense of sequence "
        "and consequence."
    ),
}


DEFAULT_FORM = "explainer"
DEFAULT_TONE = "neutral"
FORM_NAMES = tuple(FORMS.keys())
TONE_NAMES = tuple(TONES.keys())


def get_form_directive(name: str | None) -> str:
    if not name:
        return FORMS[DEFAULT_FORM]
    return FORMS.get(name, FORMS[DEFAULT_FORM])


def get_tone_directive(name: str | None, category: str = "data") -> str:
    """Return the tone directive. For concept subblocks, the
    TONES_CONCEPT_OVERRIDES table wins for the five tones that
    reference "numbers" or "the data"."""
    if category == "concept" and name in TONES_CONCEPT_OVERRIDES:
        return TONES_CONCEPT_OVERRIDES[name]
    if not name:
        return TONES[DEFAULT_TONE]
    return TONES.get(name, TONES[DEFAULT_TONE])


_FIXED_FORM_BY_SLUG: dict[str, tuple[str, str]] = {
    # trading_psychology is always an editorial feature story.
    # The form does not rotate; the article's shape is fixed
    # by the prompt's beats, and the tone is warm and reflective.
    "trading_psychology": ("editorial", "feature-story"),
}


async def pick_form_and_tone(
    session: AsyncSession,
    *,
    org_id: str,
    subblock_id: str,
    subblock_slug: str | None = None,
) -> tuple[str, str]:
    """Least-recently-used (form, tone) pair for this subblock.

    Subblocks in _FIXED_FORM_BY_SLUG do not rotate — they always
    return the pinned pair.

    Reads the last 20 posts' recorded (form, tone) pairs and returns
    the first pair in the cross-product that has not been used
    recently. Falls back to the least-recently used pair if every
    combination has been seen.

    Serialized via a transaction-scoped Postgres advisory lock so
    concurrent generations of the same subblock cannot pick the same
    pair. Without this, five workers started at once each read the
    same `used` list and each chose the same next pair (observed as
    four identical `opinion / conversational` rows in the S.7.9.3
    verification run).
    """
    if subblock_slug and subblock_slug in _FIXED_FORM_BY_SLUG:
        return _FIXED_FORM_BY_SLUG[subblock_slug]

    # pg_advisory_xact_lock holds until the caller's transaction
    # commits. The generation flow commits shortly after persist_post,
    # so the lock is held briefly. hashtext gives us a stable int key
    # from a composite string.
    await session.execute(
        sql_text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"pick_form_tone:{org_id}:{subblock_id}"},
    )

    rows = (await session.execute(sql_text("""
        SELECT source_payload->>'form' AS form,
               source_payload->>'tone' AS tone,
               created_at
        FROM trading_posts
        WHERE org_id = :org_id
          AND subblock_id = :subblock_id
          AND status IN ('published', 'draft')
        ORDER BY created_at DESC
        LIMIT 20
    """), {"org_id": org_id, "subblock_id": subblock_id})).all()

    used: list[tuple[str | None, str | None]] = [(r[0], r[1]) for r in rows]

    # Walk a diagonal through the cross-product so both form AND tone
    # advance on each step. A nested loop would fix the form until
    # every tone was exhausted (visible in the first S.7.9 test: four
    # consecutive `explainer` posts with four different tones). The
    # diagonal produces a different form every generation for the
    # first len(FORM_NAMES) steps, and a different tone every
    # generation for the first len(TONE_NAMES) steps.
    n_forms = len(FORM_NAMES)
    n_tones = len(TONE_NAMES)
    for i in range(n_forms * n_tones):
        form = FORM_NAMES[i % n_forms]
        tone = TONE_NAMES[i % n_tones]
        if (form, tone) not in used:
            return form, tone

    # Every pair seen recently — fall back to the least-recently used.
    last_form, last_tone = used[-1] if used else (None, None)
    return last_form or DEFAULT_FORM, last_tone or DEFAULT_TONE
