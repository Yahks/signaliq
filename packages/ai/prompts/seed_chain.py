"""Seed the chain-related prompts. Run once."""
from __future__ import annotations

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings

PROMPTS = [
    {
        "name": "chain.select",
        "version": 1,
        "role": "reasoning",
        "template": """You are a news editor deciding which events form a single coherent story.

CANDIDATE EVENTS:
{{events}}

Select 4-8 events that form ONE coherent news story.

Rules:
- The story must have a through-line (recurring entity, place, or theme).
- Prefer events with narrative tension (contradiction, escalation, response).
- Reject events with only one source if better-sourced alternatives exist.
- If no coherent story exists, return {"chain": null, "reason": "..."}.

Return ONLY valid JSON matching this schema:
{
  "chain": {
    "title": "working title (max 15 words)",
    "thesis": "one sentence — the through-line",
    "event_ids": ["id1", "id2", ...],
    "primary_entities": ["...", "..."],
    "confidence": 0.0
  }
}

JSON:""",
        "variables": ["events"],
        "is_active": True,
    },
    {
        "name": "chain.order",
        "version": 1,
        "role": "reasoning",
        "template": """You are sequencing events for a news article.

EVENTS (chronological):
{{events}}

Choose narrative order. Rules:
- Lead with the event that best represents the thesis.
- If two events contradict, place them ADJACENT to highlight tension.
- Establish context before escalation.
- End with implications, not the smallest event.

Return ONLY valid JSON:
{"sequence": ["id1", "id2", ...]}

JSON:""",
        "variables": ["events"],
        "is_active": True,
    },
    {
        "name": "chain.link",
        "version": 1,
        "role": "reasoning",
        "template": """For each consecutive event pair, determine the relationship.

Link types:
- causes:      A directly caused B
- precedes:    A happened before B (temporal only)
- contradicts: A and B cannot both be fully true
- escalates:   B raises the stakes of A
- contextualizes: B explains or frames A
- responds_to: B is a response to A
- corroborates: B confirms A

PAIRS:
{{pairs}}

Return ONLY valid JSON:
[
  {"from": "id", "to": "id", "link_type": "...", "rationale": "one sentence", "confidence": 0.0}
]

JSON:""",
        "variables": ["pairs"],
        "is_active": True,
    },
    {
        "name": "chain.narrate",
        "version": 1,
        "role": "reasoning",
        "template": """You are a news presenter writing a long-form article.

THESIS: {{thesis}}

ORDERED EVENTS (with links):
{{events_with_links}}

STYLE GUIDE:
- Voice: news presenter / long-form journalist.
- Tense: past for events, present for implications.
- Attribution: preserve exact speaker — "According to X..." "Y said..."
- Epistemic honesty:
    * status=fact → state plainly
    * status=claim → "X claims..." / "According to X..."
    * status=rumor → "Unconfirmed reports suggest..."
    * status=disputed → "Sources conflict: X says A, Y says B"
- Numbers: use ONLY numbers provided. Do NOT invent statistics.
- Length: {{target_words}} words.
- Output: markdown with a title (# Title) and flowing prose (no bullet lists).

FORBIDDEN:
- Fabricated statistics
- Fabricated quotes
- Invented sources
- Speculation presented as fact
- Bullet-point summaries

REQUIRED:
- Open with the thesis tension.
- Use each link's rationale as a transition.
- End with implications, not summary.
- Every factual claim must be traceable to an event above.

Write the article now:""",
        "variables": ["thesis", "events_with_links", "target_words"],
        "is_active": True,
    },
    {
        "name": "chain.verify",
        "version": 1,
        "role": "reasoning",
        "template": """You are an evidence auditor.

POST BODY:
{{body}}

EVIDENCE (events and their sources):
{{evidence}}

For each sentence in the body:
1. Classify as: fact | inference | hypothesis | recommendation
2. If fact: cite the event id(s) that support it. If none, mark "unsupported".
3. If inference: list the event ids it is inferred FROM.

Return ONLY valid JSON:
[
  {
    "claim_text": "...",
    "claim_type": "fact",
    "supported_by_event_ids": ["..."],
    "confidence": 0.0
  }
]

If any claim of type "fact" has empty supported_by_event_ids, include "unsupported": true.

JSON:""",
        "variables": ["body", "evidence"],
        "is_active": True,
    },
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
                    VALUES (:name, :version, :role, :template, :variables, :is_active)
                    ON CONFLICT (name, version) DO UPDATE
                    SET template = EXCLUDED.template,
                        role = EXCLUDED.role,
                        variables = EXCLUDED.variables,
                        is_active = EXCLUDED.is_active
                """),
                {
                    "name": p["name"],
                    "version": p["version"],
                    "role": p["role"],
                    "template": p["template"],
                    "variables": __import__("json").dumps(p["variables"]),
                    "is_active": p["is_active"],
                },
            )
        session.commit()
        print(f"Seeded {len(PROMPTS)} chain prompts")


if __name__ == "__main__":
    seed()
