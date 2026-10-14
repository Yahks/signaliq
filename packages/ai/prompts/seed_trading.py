"""Seed trading-block prompt overrides.

Overrides (base prompt name → trading-specific):
    chain.select.trading
    chain.order.trading
    chain.link.trading
    chain.narrate.trading
    chain.verify.trading

The prompts are written for the trading block's voice:
- Educative / informative / mentor tone
- Structure: hook → context → lesson → compare → call
- Trading-specific link types
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings

TRADING_SELECT = """You are a trading editor selecting events that form a coherent educative article.

CANDIDATE EVENTS:
{{events}}

Select 4-8 events that form ONE coherent teaching arc.

Rules:
- The story must have a clear teaching angle (a principle a trader can learn).
- Prefer events with narrative tension: cause → effect, contrast, comparison.
- Reject events that are pure noise or ads/CTA posts.
- Prioritize events that illustrate a market behavior worth explaining.
- If no coherent story exists, return {"chain": null, "reason": "..."}.

Return ONLY valid JSON matching this schema:
{
  "chain": {
    "title": "working title (max 15 words, educative, not clickbait)",
    "thesis": "one sentence — the teaching through-line",
    "event_ids": ["id1", "id2", ...],
    "primary_entities": ["...", "..."],
    "confidence": 0.0
  }
}

JSON:"""

TRADING_ORDER = """You are sequencing events for an educational trading article.

EVENTS (chronological):
{{events}}

Choose narrative order. Rules for the trading block:
- Lead with the HOOK — the most striking fact, number, or question.
- Then CONTEXT — what happened, why it matters.
- Then the LESSON — the event(s) that best illustrate the teaching point.
- Then COMPARE — if two events contrast (different sectors, times, or angles), place them adjacent.
- End with CALL — the soft action/invitation event if present.
- If contradicting data exists, place those events adjacent to highlight tension.

Return ONLY valid JSON:
{"sequence": ["id1", "id2", ...]}

JSON:"""

TRADING_LINK = """You are linking consecutive events for an educational trading article.

Link types (trading-specific):
- causes:            A directly caused B (e.g., Fed hike → bond selloff)
- correlates_with:   A and B move together
- contrasts:         A and B are opposing dynamics (e.g., tech up, energy down)
- explains:          B provides the WHY behind A
- compares:          explicit comparison (US vs Canada, now vs then)
- teaches:           A is the lesson illustrated by B (or vice versa)
- precedes:          A happened before B (temporal context)
- responds_to:       B is a market reaction to A
- warns:             B signals a risk or caveat

PAIRS:
{{pairs}}

Return ONLY valid JSON:
[
  {"from": "id", "to": "id", "link_type": "...", "rationale": "one sentence", "confidence": 0.0}
]

JSON:"""

TRADING_NARRATE = """You are an experienced trader writing an EDUCATIONAL article for a serious retail audience.

THESIS: {{thesis}}

ORDERED EVENTS (with links):
{{events_with_links}}

BLOCK STYLE GUIDE:
{{style_guide}}

EXAMPLE OUTPUT (match this voice and structure):
{{example_output}}

MANDATORY STRUCTURE — 5 sections:
1. HOOK (1-2 sentences): a striking fact, number, or question. Draw the reader in.
2. CONTEXT (2-3 short paragraphs): what happened. Explain the WHY before the WHAT.
   Bring 2-3 events from above. Give the reader enough to understand what's at stake.
3. LESSON (2-3 short paragraphs): the educative core. What trading principle does this
   situation demonstrate? How should a disciplined trader think about this?
   Include concrete examples or numbers from the events.
4. COMPARE (1-2 paragraphs): this vs that, now vs then, sector vs sector,
   safe-haven vs risk. Show the reader how to triangulate.
5. CALL (1 paragraph): soft closing. Invite the reader to learn more or reflect.
   Never pushy. Never promise returns.

RULES:
- Use second-person "you" and first-person "I" sparingly for mentor voice.
- Use ONLY numbers provided in the events. Never invent prices, percentages, or dates.
- NEVER promise profits. NEVER say "buy this" or "sell that."
- Acknowledge risk whenever discussing opportunity.
- Keep it tight. Target length: {{target_words}} words.
- Output: markdown with a `# Title` on the first line, then flowing prose.
- Add 3-6 hashtags at the very end (e.g., #SP500 #TradingEducation).
- No bullet lists in the final article. Everything is prose.

Write the article now:"""

TRADING_VERIFY = """You are auditing a trading-education article for factual accuracy.

POST BODY:
{{body}}

EVIDENCE (events and their sources):
{{evidence}}

For each factual sentence:
1. Classify as: fact | inference | hypothesis | recommendation | teaching
   - fact: verifiable claim about what happened
   - inference: conclusion drawn from facts
   - hypothesis: speculative forecast
   - recommendation: advice to the reader
   - teaching: general trading principle (not a factual claim)
2. For fact/inference: cite the event IDs that support it.
3. Mark "unsupported": true if a fact has no source.

Return ONLY valid JSON:
[
  {
    "claim_text": "...",
    "claim_type": "fact",
    "supported_by_event_ids": ["..."],
    "confidence": 0.0
  }
]

JSON:"""


PROMPTS = [
    {"name": "chain.select.trading",  "template": TRADING_SELECT,  "variables": ["events"]},
    {"name": "chain.order.trading",   "template": TRADING_ORDER,   "variables": ["events"]},
    {"name": "chain.link.trading",    "template": TRADING_LINK,    "variables": ["pairs"]},
    {"name": "chain.narrate.trading", "template": TRADING_NARRATE, "variables": ["thesis", "events_with_links", "style_guide", "example_output", "target_words"]},
    {"name": "chain.verify.trading",  "template": TRADING_VERIFY,  "variables": ["body", "evidence"]},
]


def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        for p in PROMPTS:
            session.execute(
                sql_text("UPDATE prompt_versions SET is_active = false WHERE name = :name"),
                {"name": p["name"]},
            )
            session.execute(
                sql_text("""
                    INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                    VALUES (:name, 1, 'reasoning', :template, CAST(:vars AS jsonb), true)
                    ON CONFLICT (name, version) DO UPDATE
                    SET template = EXCLUDED.template,
                        variables = EXCLUDED.variables,
                        is_active = true
                """),
                {
                    "name": p["name"],
                    "template": p["template"],
                    "vars": json.dumps(p["variables"]),
                },
            )
        session.commit()
        print(f"Seeded {len(PROMPTS)} trading prompts")


if __name__ == "__main__":
    seed()
