"""Editorial voice presets for trading subblocks.

Each org picks a voice in settings: orgs.settings->'editorial'->'voice'.
The voice directive is prepended to the subblock's system prompt, on
top of the shared block style guide.

The event is shared. The voice is per-org. Same market close, different
publication.

Voices:
  wire       — data-forward newswire (the pre-S.7.8b default)
  analyst    — sell-side, risk/reward framing, extended interpretation
  contrarian — states the consensus, points at a tension, names what to watch
  retail     — plain language, portfolio framing, defines jargon
  academic   — structural, hedged, long-view framing
"""
from __future__ import annotations

from sqlalchemy import text as sql_text


VOICES: dict[str, str] = {
    "wire": (
        "You write a tight, data-forward market wire. Report the numbers, "
        "name the rotation, state one implication. No narrative, no "
        "hypothesis about cause, no interpretation beyond what the numbers "
        "directly show."
    ),
    "analyst": (
        "You write as a sell-side strategist addressing institutional "
        "allocators. State the numbers, then spend most of the piece "
        "interpreting the pattern: what does the rotation imply about "
        "positioning, risk appetite, or duration sensitivity? Every "
        "interpretive sentence must cite a specific figure from the source "
        "data. Frame the analysis in terms of risk/reward - where is the "
        "market rewarding risk, where is it punishing it? No hype, no "
        "forecasts, no retail language. A good analyst writes half the "
        "piece on what happened and half on what it means."
    ),
    "contrarian": (
        "You write as a contrarian analyst. In the Interpretation section, "
        "execute three concrete moves:\n"
        "  1. State the consensus reading in one sentence. What would a "
        "typical analyst say this data means?\n"
        "  2. Point at ONE specific tension in the numbers. A number that "
        "does not quite fit the consensus reading.\n"
        "  3. End with what to watch: a specific observation that would "
        "confirm or refute the consensus reading.\n"
        "Do not contradict the numbers. Do not invent causes. Every "
        "sentence must be grounded in a figure from the source. Your job "
        "is to ask the question the consensus skips."
    ),
    "retail": (
        "You write for an individual investor who follows the market but "
        "is not a professional. Explain the numbers in plain language: "
        "what did the major indices do, which sectors led and lagged, and "
        "what does this mean for a portfolio that holds a mix of growth "
        "and defensive assets? Avoid jargon. When you use a term like "
        "'rotation' or 'duration', define it in one short phrase. No "
        "buy/sell advice - frame implications, not recommendations. The "
        "reader wants to understand what happened and what it might mean "
        "for them, in plain English."
    ),
    "academic": (
        "You write as a financial economist. Present the numbers, then "
        "place the move in a longer structural context: is this "
        "consistent with a rate-regime shift, a positioning unwind, or a "
        "sector rotation typical of a late cycle? Use cautious, hedged "
        "language - 'consistent with', 'suggests', 'may reflect'. "
        "Reference patterns rather than causes. Every structural claim "
        "must be consistent with at least one figure in the source data. "
        "The economist's job is not to predict but to locate the move "
        "within a framework the reader can hold onto."
    ),
}

VOICES_CONCEPT: dict[str, str] = {
    "wire": (
        "You write a tight, plain educational note. State the concept, "
        "explain why it matters in practice, give one concrete scenario. "
        "No narrative, no hypothesis about causes, no jargon."
    ),
    "analyst": (
        "You write as an experienced practitioner explaining a concept to "
        "a junior colleague. Be rigorous and precise: define terms, "
        "distinguish the concept from adjacent ones, explain where it "
        "applies and where it breaks down. Use scenarios, not numbers. "
        "No hype, no oversimplification. A good explanation names the "
        "underlying mechanism, not just the definition."
    ),
    "contrarian": (
        "You write as a contrarian teacher. State the standard explanation "
        "of the concept, then examine where it is commonly misunderstood "
        "or oversimplified. Point at the tension between how the concept "
        "is taught and how it actually behaves. Every alternative reading "
        "must be a defensible position, not a strawman. Your job is to "
        "add nuance the textbook skips."
    ),
    "retail": (
        "You write for an individual investor who is new to this topic. "
        "Use plain language. Define any term before using it. Explain "
        "with a scenario the reader can picture. Avoid jargon entirely. "
        "When you must use a technical term, define it in one short "
        "phrase. No advice - just understanding. The reader wants to walk "
        "away able to explain this concept to someone else."
    ),
    "academic": (
        "You write as a teacher-scholar. Place the concept in its larger "
        "framework: where did it come from, what problem does it solve, "
        "what are its assumptions, what are its limits. Use hedged, "
        "careful language - 'this is usually framed as', 'the standard "
        "account holds', 'in practice, however'. Reference patterns and "
        "frameworks, not specific numbers. The scholar's job is to locate "
        "the concept, not to advocate for it."
    ),
}

DEFAULT_VOICE = "wire"
VOICE_NAMES = tuple(VOICES.keys())

# Subblocks whose content is educational about a concept, not a report
# on today's data. For these, compose_system_prompt uses VOICES_CONCEPT
# and the concept overrides in TONES.
CONCEPT_SUBBLOCKS = frozenset({
    "class_room",
    "trading_psychology",
    "trading_styles",
    "investment_strategies",
    "trading_tools",
})


def get_voice_directive(name: str | None, category: str = "data") -> str:
    """Return the prose directive for a voice in the given category.

    category="data"    — subblocks reporting on market figures
    category="concept" — subblocks explaining a concept (class_room,
                         trading_psychology, trading_styles,
                         investment_strategies, trading_tools)
    """
    table = VOICES_CONCEPT if category == "concept" else VOICES
    if not name:
        return table[DEFAULT_VOICE]
    return table.get(name, table[DEFAULT_VOICE])


def compose_system_prompt(
    voice: str | None,
    block_guide: str | None,
    fallback: str,
    form: str | None = None,
    tone: str | None = None,
    slug: str | None = None,
    register: str | None = None,
    register_params: dict | None = None,
    tier: str | None = None,
) -> str:
    """Compose the system prompt from six layers:

      1. register directive — per-article, rotates (S.8.15)   [new]
      2. voice directive    — per-org, stable                  [legacy]
      3. form directive     — per-subblock, rotates (S.7.9)
      4. tone directive     — per-subblock, rotates (S.7.9)
      5. block style guide  — shared across the trading block
      6. subblock fallback  — only if the block guide is empty

    The register layer is added above voice. When a register is
    present, it carries the article's voice, and the legacy voice
    layer is omitted — including both would give the model two
    conflicting voice directives.

    When no register is passed, the function behaves exactly as
    before: the voice layer is used. This lets subblocks migrate
    one at a time. Once every subblock passes a register, the
    voice parameter and this legacy path can be removed.

    If `slug` is in CONCEPT_SUBBLOCKS, the concept voice table and
    concept tone overrides are used instead of the data tables.
    """
    from .forms import get_form_directive, get_tone_directive

    category = "concept" if slug in CONCEPT_SUBBLOCKS else "data"

    parts: list[str] = []

    if register:
        from .registers import register_fragment
        parts.append(
            register_fragment(register, **(register_params or {}))
        )
    else:
        parts.append(get_voice_directive(voice, category=category))

    # Registers that carry their own audience and length spec
    # must not also receive TIER_RULES — the tier text would
    # contradict them.
    _SELF_CONTAINED_REGISTERS = frozenset({
        "psychology-note",
    })
    if tier and register not in _SELF_CONTAINED_REGISTERS:
        from .registers import get_tier_directive
        tier_text = get_tier_directive(tier)
        if tier_text:
            parts.append(tier_text)

    # S.8.90.13 — the trader-voice registers (view registers)
    # specify the shape themselves. Form and tone would add a
    # second shape signal on top of the register, and the model
    # resolves the two inconsistently per run. Drop them.
    _drop_form_tone = False
    if register:
        try:
            from packages.trading.subblocks.base import _VIEW_REGISTERS
            if register in _VIEW_REGISTERS or register == "psychology-note":
                _drop_form_tone = True
        except Exception:
            pass
        if register == "psychology-note":
            _drop_form_tone = True
    if form and not _drop_form_tone:
        parts.append(get_form_directive(form))
    if tone and not _drop_form_tone:
        parts.append(get_tone_directive(tone, category=category))
    guide = (block_guide or "").strip()
    if guide:
        parts.append(guide)
    else:
        parts.append(fallback)
    # S.8.59 — trader rules. Applies to every register at generation
    # time. The delivery check catches invented trades and thresholds
    # as a second layer; this stops them at the prompt.
    # S.8.97 — the "no personal experience" rule was written for
    # the data registers (market wire, macro view) where the model
    # is a reporter, not a trader. For concept-explainer, the
    # process_excerpt IS the trader's history, and the piece is
    # written in the trader's first person — so the rule flips.
    # First-person registers — the model writes as the trader.
    # The process excerpt / skeleton IS the trader's history.
    _FIRST_PERSON_REGISTERS = frozenset({
        "concept-explainer",
        "psychology-note",
        "practitioner-risk",
        "practitioner-strategies",
        "practitioner-indicators",
        "practitioner-screen",
        "practitioner-process",
        "insider-log",
        "market-analysis",
        "market_analysis",
    })
    if register in _FIRST_PERSON_REGISTERS:
        parts.append(
            "RULES (absolute):\n"
            "1. You are the trader. The process excerpt above is your "
            "own history. Write as if you lived it. Every 'I' refers "
            "to you. Do not invent a figure, a trade, or an "
            "attribution that is not in the excerpt.\n"
            "2. Write in first person. 'I' and 'my', never 'you'. If "
            "you catch yourself writing 'you', stop and rewrite the "
            "sentence in first person.\n"
            "3. Continuous prose. No headings, no bold labels, no "
            "beat names, no bullets, no numbered lists.\n"
            "4. The register specifies the shape. Follow it exactly.\n"
            "5. End with the CTA. Nothing after it."
        )
    else:
        parts.append(
            "RULES (absolute):\n"
            "1. Write only from the data above. Do not state any "
            "figure, trade, threshold, or attribution not present in "
            "the source. Do not claim personal experience. You have "
            "no history.\n"
            "2. If a section cannot be filled from the data, omit it "
            "entirely. Do not write a sentence about the missing "
            "data.\n"
            "3. The register specifies the voice. Follow it exactly. "
            "Do not override the register's voice with a different "
            "one."
        )

    return "\n\n".join(parts)


async def get_org_voice(session, org_id: str) -> str:
    """Read the org's editorial voice from settings. Default: 'wire'."""
    row = (await session.execute(sql_text("""
        SELECT settings->'editorial'->>'voice' AS voice
        FROM orgs WHERE id = :oid
    """), {"oid": org_id})).first()
    return (row[0] if row and row[0] else DEFAULT_VOICE)
