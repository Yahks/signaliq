"""Seed the football prediction block.

Different from news/trading:
  - Output includes numeric probability predictions
  - Style is analytical sports journalism
  - Article always includes the disclaimer + model performance link
  - Event types are football-specific
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings

FOOTBALL_STYLE = """You are a football analyst writing a match preview for an informed audience.

Voice:
- Analytical sports journalist with statistical depth.
- Second-person for the reader ("you'll see", "what you should watch for").
- Uses football terminology naturally (xG, PPDA, high press, low block, transition)
  but explains it in context for readers who are newer.
- Opinionated but grounded — no hype, no "banker", no "sure thing".

MANDATORY DISCLAIMER (must appear verbatim in every article):
This article is statistical analysis, not betting advice. Football matches
are inherently unpredictable. Model accuracy is publicly tracked at
/performance. If you choose to bet, only stake what you can afford to lose.

Structure (mandatory):
  1. FIXTURE HEADER — teams, competition, kickoff, venue
  2. FORM & CONTEXT — last 5 results for each team, table position, recent form
  3. KEY NARRATIVES — injuries, tactics, motivation, history between clubs
  4. MODEL OUTPUT — the numbers, presented as probabilities with confidence
     * Home win / Draw / Away win probabilities
     * Expected total goals
     * Over/Under 2.5 goals probabilities
     * Never use the words "bet", "back", "lay", "stake"
     * Always frame as: "The model estimates X% probability"
  5. WHAT TO WATCH — 3-4 specific things a viewer should observe during the match
  6. CONFIDENCE NOTE — sample size, model limitations, uncertainty

Rules:
- Every number must come from the model output provided. Do NOT invent.
- Do NOT say "you should bet on X" or "X is a good bet".
- Do NOT predict specific scorelines with certainty.
- Never guarantee anything. Football is uncertain.
- Use ONLY numbers from the provided prediction data.
"""

FOOTBALL_EXAMPLE = """# Arsenal vs Chelsea — Premier League, Matchday 12

**When:** Sunday 22 September, 16:30 BST
**Where:** Emirates Stadium, London

## Form going in

Arsenal arrive on a 3-match winning run, having kept clean sheets in each.
Chelsea have been more volatile: 2 wins, 2 draws, 1 loss in their last 5,
with defensive lapses costing them points away from home.

## Key narratives

Arsenal's midfield press has been among the league's most effective this
season (PPDA 8.4). Chelsea's buildup under pressure has been less reliable.
The injury to Chelsea's first-choice centre-back changes the aerial matchup
significantly.

## Model output

The Dixon-Coles model estimates:

- **Arsenal win:** 54.2%
- **Draw:** 24.1%
- **Chelsea win:** 21.7%

Expected total goals: **2.7**
- **Over 2.5 goals:** 58.3%
- **Under 2.5 goals:** 41.7%

**Confidence:** medium. Based on 5 seasons of results, 38 head-to-heads,
and each club's last 12 matches. Data quality: 0.79.

## What to watch

1. Whether Chelsea's makeshift centre-back pairing can hold Arsenal's
   set-piece threat.
2. Arsenal's press trigger zones — where they choose to win the ball.
3. Chelsea's ability to break the first line through quick combinations.
4. Set-piece delivery quality from both sides.

## Confidence note

Football predictions carry inherent uncertainty. The model is well-calibrated
on historical data, but any single match can produce an outcome the model
didn't favor. Public performance record: /performance.

---

*This article is statistical analysis, not betting advice. Football matches
are inherently unpredictable. Model accuracy is publicly tracked at
/performance. If you choose to bet, only stake what you can afford to lose.*

#PremierLeague #Arsenal #Chelsea #FootballAnalytics
"""

FOOTBALL_CONFIG = {
    "link_types": [
        "precedes",       # matches in sequence
        "affects",        # injury affects fixture
        "motivates",      # derby, revenge, stakes
        "contrasts",      # home form vs away form
        "explains",       # context behind a run of results
        "predicts",       # a pattern that predicts an outcome
        "responds_to",    # a team responds to criticism, change
    ],
    "event_types": [
        "fixture",           # upcoming match
        "match_result",      # past match
        "transfer",          # signings / departures
        "injury",            # player injury / return
        "manager_change",
        "form_shift",        # win/loss streak
        "tactical_note",     # formation change, stylistic shift
        "disciplinary",      # suspension, red card
        "venue",             # stadium change, closed doors
        "weather",           # if significant (rain, wind)
    ],
    "status_types": ["fact", "forecast", "rumor", "opinion"],
    "default_target_words": 700,
    "default_tone": "analytical",
    "voice": "informed football analyst with statistical depth",
    "requires_disclaimer": True,
    "article_structure": [
        {"section": "fixture_header", "description": "Teams, competition, kickoff, venue"},
        {"section": "form_context", "description": "Last 5 for each team, table position"},
        {"section": "narratives", "description": "Injuries, tactics, motivation, history"},
        {"section": "model_output", "description": "Probabilities with confidence framing"},
        {"section": "what_to_watch", "description": "3-4 specific things viewers observe"},
        {"section": "confidence_note", "description": "Sample size, limitations, uncertainty"},
    ],
    "hashtags_style": "#PremierLeague #FootballAnalytics",
    "disclaimer_required": (
        "This article is statistical analysis, not betting advice. "
        "Football matches are inherently unpredictable. Model accuracy is "
        "publicly tracked at /performance. If you choose to bet, only stake "
        "what you can afford to lose."
    ),
}

FOOTBALL_SUBTOPICS = [
    {"slug": "epl", "name": "Premier League", "description": "English Premier League"},
    {"slug": "championship", "name": "EFL Championship", "description": "English second tier"},
    {"slug": "laliga", "name": "La Liga", "description": "Spanish La Liga"},
    {"slug": "serie_a", "name": "Serie A", "description": "Italian Serie A"},
    {"slug": "bundesliga", "name": "Bundesliga", "description": "German Bundesliga"},
    {"slug": "ligue_1", "name": "Ligue 1", "description": "French Ligue 1"},
    {"slug": "ucl", "name": "Champions League", "description": "UEFA Champions League"},
    {"slug": "afcon", "name": "AFCON", "description": "Africa Cup of Nations"},
]


def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        session.execute(sql_text("""
            INSERT INTO blocks (org_id, slug, name, description, style_guide,
                                example_output, config, subtopics, is_system, is_active)
            VALUES (NULL, 'football', 'Football',
                    'Statistical football match previews and predictions',
                    :style, :example,
                    CAST(:config AS jsonb), CAST(:subtopics AS jsonb),
                    true, true)
            ON CONFLICT (slug) WHERE org_id IS NULL DO UPDATE
            SET name = EXCLUDED.name,
                description = EXCLUDED.description,
                style_guide = EXCLUDED.style_guide,
                example_output = EXCLUDED.example_output,
                config = EXCLUDED.config,
                subtopics = EXCLUDED.subtopics,
                updated_at = now()
        """), {
            "style": FOOTBALL_STYLE,
            "example": FOOTBALL_EXAMPLE,
            "config": json.dumps(FOOTBALL_CONFIG),
            "subtopics": json.dumps(FOOTBALL_SUBTOPICS),
        })
        session.commit()
        print("Seeded football block")


if __name__ == "__main__":
    seed()
