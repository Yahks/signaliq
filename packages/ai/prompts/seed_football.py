"""Seed football-block prompt overrides.

Overrides (base prompt name → football-specific):
    chain.select.football
    chain.order.football
    chain.link.football
    chain.narrate.football
    chain.verify.football

The narrate prompt takes an additional {{predictions}} variable that
contains the model's numeric output. The prompt is required to use
ONLY those numbers, no invented stats.
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings

FOOTBALL_SELECT = """You are a football analyst selecting fixtures that form a coherent preview.

CANDIDATE EVENTS:
{{events}}

Select 4-8 events that form ONE coherent story.

Rules:
- Prefer events that cluster around one match or one theme (e.g., title race,
  injury crisis, tactical trend).
- Include the fixture itself if present.
- Include 2-4 supporting context events (injuries, form, transfers).
- Reject isolated noise that doesn't connect.

Return ONLY valid JSON matching this schema:
{
  "chain": {
    "title": "working title (max 15 words)",
    "thesis": "one sentence — the through-line",
    "event_ids": ["id1", "id2", ...],
    "primary_entities": ["Team A", "Team B", "Player X"],
    "confidence": 0.0
  }
}

JSON:"""

FOOTBALL_ORDER = """You are sequencing events for a football preview article.

EVENTS (chronological):
{{events}}

Order rules:
- Lead with the FIXTURE (the upcoming match) if present.
- Then CONTEXT — recent form, key injuries, tactical background.
- Then NARRATIVES — the storylines that matter.
- End with the most forward-looking item (prediction, tactical projection).

Return ONLY valid JSON:
{"sequence": ["id1", "id2", ...]}

JSON:"""

FOOTBALL_LINK = """You are linking consecutive events for a football preview.

Link types (football-specific):
- precedes:      temporal order (matches in sequence)
- affects:       one event affects another (e.g., injury affects fixture)
- motivates:     one event creates motivation (derby, revenge)
- contrasts:     opposing dynamics (home form vs away form)
- explains:      one event explains a pattern
- predicts:      one event predicts another (form predicts result)
- responds_to:   one event is a response to another (after loss, change)

PAIRS:
{{pairs}}

Return ONLY valid JSON:
[
  {"from": "id", "to": "id", "link_type": "...", "rationale": "one sentence", "confidence": 0.0}
]

JSON:"""

FOOTBALL_NARRATE = """You are a football analyst writing a match preview for an informed audience.

THESIS: {{thesis}}

ORDERED EVENTS (with links):
{{events_with_links}}

MODEL PREDICTION (from the Dixon-Coles Poisson model):
{{predictions}}

BLOCK STYLE GUIDE:
{{style_guide}}

EXAMPLE OUTPUT:
{{example_output}}

MANDATORY STRUCTURE (6 sections):
1. `# {Team A} vs {Team B} — {Competition}, Matchday N`  (title)
2. `**When:**` and `**Where:**` lines
3. `## Form going in` — last 5 for each team, table position, momentum
4. `## Key narratives` — injuries, tactics, motivation, history
5. `## Model output` — MUST use ONLY the numbers from the MODEL PREDICTION block
6. `## What to watch` — 3-4 specific tactical or narrative observations
7. `## Confidence note` — sample size, limitations, uncertainty
8. MANDATORY DISCLAIMER at the end (use the exact text from the block style guide)
9. Hashtags

ABOUT THE MODEL PREDICTION BLOCK:
The numbers there are the ONLY statistics you may cite for probabilities,
expected goals, or over/under. Do NOT invent or round beyond what's given.
Present them verbatim (e.g. "Arsenal win: 54.2%").

ABSOLUTE RULES:
- NEVER use the words "bet", "back", "lay", "stake", "value", "banker".
- NEVER say "you should bet on X" or "X is a good bet".
- NEVER guarantee outcomes.
- NEVER fabricate statistics or scores.
- Frame all predictions as: "The model estimates X% probability".
- Include the mandatory disclaimer verbatim.

Target length: {{target_words}} words.

Write the preview now, starting with "# " on the first line:"""

FOOTBALL_VERIFY = """You are auditing a football preview article for accuracy.

POST BODY:
{{body}}

MODEL PREDICTION (the source of truth for all numbers):
{{predictions}}

EVIDENCE (events):
{{evidence}}

For each factual claim:
1. Classify as: fact | inference | hypothesis | recommendation | prediction
2. If a claim cites a number (probability, expected goals, over/under):
   verify it matches the MODEL PREDICTION block.
3. If a number is present in the article but NOT in the model block → mark
   "fabricated": true.
4. Verify the mandatory disclaimer appears verbatim.

Return ONLY valid JSON:
{
  "claims": [
    {
      "claim_text": "...",
      "claim_type": "fact",
      "supported_by": ["event_id or 'model'"],
      "fabricated": false,
      "confidence": 0.0
    }
  ],
  "disclaimer_present": true
}

JSON:"""


PROMPTS = [
    {"name": "chain.select.football",  "template": FOOTBALL_SELECT,  "variables": ["events"]},
    {"name": "chain.order.football",   "template": FOOTBALL_ORDER,   "variables": ["events"]},
    {"name": "chain.link.football",    "template": FOOTBALL_LINK,    "variables": ["pairs"]},
    {"name": "chain.narrate.football", "template": FOOTBALL_NARRATE, "variables": ["thesis", "events_with_links", "predictions", "style_guide", "example_output", "target_words"]},
    {"name": "chain.verify.football",  "template": FOOTBALL_VERIFY,  "variables": ["body", "predictions", "evidence"]},
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
                {"name": p["name"], "template": p["template"], "vars": json.dumps(p["variables"])},
            )
        session.commit()
        print(f"Seeded {len(PROMPTS)} football prompts")


if __name__ == "__main__":
    seed()
