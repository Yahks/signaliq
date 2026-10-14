"""Seed prompts for the living chain: impacts, scenarios, watchlist, reattach.

These are block-aware: the trading block gets educative/actionable variants.
The news block uses neutral variants.
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings

# --- Impacts (trading) ---
IMPACTS_TRADING = """You are a market analyst. Given the events in this chain, identify what could be affected.

EVENTS:
{{events}}

Produce a ranked list of impacts. For each:
- label: a short name (e.g. "LNG prices", "US technology equities", "Credit spreads")
- category: sector | commodity | asset_class | currency | credit | region | company
- direction: up | down | mixed | uncertain
- strength: strong | moderate | weak | unknown
- reasoning: one sentence explaining the linkage

Rules:
- Only list impacts that plausibly follow from the events. Do not invent actors.
- If the direction is unclear, use "uncertain".
- Rank by importance: strongest/most certain first.
- Include 5–12 impacts. No fewer, no more.

Return ONLY valid JSON:
{
  "impacts": [
    {
      "label": "...",
      "category": "...",
      "direction": "...",
      "strength": "...",
      "reasoning": "..."
    }
  ]
}

JSON:"""

# --- Scenarios (trading) ---
SCENARIOS_TRADING = """You are a scenario analyst. Given the events and impacts, describe 2-4 conditional futures.

EVENTS:
{{events}}

IMPACTS:
{{impacts}}

For each scenario, produce:
- name: short, evocative (e.g. "Supply shock intensifies", "Mild normalization")
- condition: a concrete conditional statement (If X happens, then Y)
- reasoning: 1-2 sentences explaining the mechanism
- candidate_symbols: Yahoo Finance ticker symbols to quantify this scenario (0-3)
- direction: "up" | "down" | "mixed" | "uncertain" (what happens to the primary symbol)

About candidate_symbols:
- Use real Yahoo Finance symbols ONLY (they'll be validated by a fetch)
- Equities: AAPL, MSFT, NVDA
- Indices: ^GSPC (S&P 500), ^IXIC (Nasdaq), ^VIX (volatility)
- Commodities (futures): CL=F (crude), NG=F (natural gas), GC=F (gold), HG=F (copper)
- FX: EURUSD=X, USDCNY=X, USDJPY=X
- Crypto: BTC-USD, ETH-USD
- Energy/sector ETFs: XLE (energy), XLF (financials), XLK (tech)
- Leave empty [] if the scenario has no obvious symbol

Rules:
- Scenarios must be MUTUALLY EXCLUSIVE and cover the plausible space.
- Use concrete conditions, not vague ones.
- Do NOT invent statistics.
- End each reasoning with how a trader would know which scenario is materializing.
- 2–4 scenarios total.

Return ONLY valid JSON:
{
  "scenarios": [
    {
      "name": "...",
      "condition": "...",
      "reasoning": "...",
      "candidate_symbols": ["NG=F", "^GSPC"],
      "direction": "up"
    }
  ]
}

JSON:"""

# --- Watchlist (trading) ---
WATCHLIST_TRADING = """You are a research editor. Given the events and impacts, suggest what a reader should monitor to track this story over time.

EVENTS:
{{events}}

IMPACTS:
{{impacts}}

For each item:
- label: what to watch (e.g. "Natural gas futures", "VIX", "US energy sector")
- symbol: Yahoo Finance symbol (REQUIRED if a real one exists)
- reasoning: one sentence on why this is a leading indicator

Yahoo symbol format:
- Equities:    AAPL, MSFT, XOM, NVDA
- Indices:     ^GSPC (S&P 500), ^IXIC (Nasdaq), ^VIX (volatility)
- Commodities: CL=F (crude), NG=F (natural gas), GC=F (gold),
               HG=F (copper), ZW=F (wheat), ZC=F (corn)
- ETFs:        XLE (energy), XLF (financials), XLK (tech), HYG (high-yield credit), TLT (long bonds)
- FX:          EURUSD=X, USDCNY=X, USDJPY=X
- Crypto:      BTC-USD, ETH-USD

Rules:
- 4–8 items total.
- EVERY item MUST have a symbol if one exists. Do not leave symbol empty
  unless the indicator is genuinely unlisted (e.g. "Iranian crude exports").
- Prefer real, liquid, monitorable symbols over vague categories.
- Do not invent symbols that don't exist — they'll be validated by a fetch.

Return ONLY valid JSON:
{
  "watchlist": [
    {"label": "...", "symbol": "NG=F", "reasoning": "..."}
  ]
}

JSON:"""

# --- Reattach (decide if a new event belongs to an existing chain) ---
REATTACH_TRADING = """You are a news editor. A new event has arrived. Decide if it belongs to an existing chain.

EXISTING CHAIN:
  title: {{chain_title}}
  thesis: {{chain_thesis}}

CURRENT CHAIN EVENTS:
{{chain_events}}

NEW EVENT:
  headline: {{new_headline}}
  summary: {{new_summary}}
  actors: {{new_actors}}
  occurred_at: {{new_occurred_at}}

Does this new event belong to the chain above?

Consider:
- Does it share actors or themes?
- Does it extend the story (new development, new consequence, new angle)?
- Or is it unrelated noise?

Return ONLY valid JSON:
{
  "attach": true | false,
  "reasoning": "one sentence explaining the decision",
  "link_type": "one of: causes | correlates_with | contrasts | explains | compares | teaches | precedes | responds_to | warns",
  "confidence": 0.0 to 1.0
}

JSON:"""


PROMPTS = [
    {"name": "chain.impacts.trading",  "template": IMPACTS_TRADING,  "variables": ["events"]},
    {"name": "chain.scenarios.trading","template": SCENARIOS_TRADING,"variables": ["events", "impacts"]},
    {"name": "chain.watchlist.trading","template": WATCHLIST_TRADING,"variables": ["events", "impacts"]},
    {"name": "chain.reattach.trading", "template": REATTACH_TRADING, "variables": ["chain_title", "chain_thesis", "chain_events", "new_headline", "new_summary", "new_actors", "new_occurred_at"]},
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
        print(f"Seeded {len(PROMPTS)} living-chain prompts")


if __name__ == "__main__":
    seed()
