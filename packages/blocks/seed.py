"""Seed the system blocks: news + trading.

Idempotent — updates existing blocks by slug.
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings

NEWS_STYLE = """You are a news presenter writing a long-form article for an informed audience.
- Voice: neutral, journalist.
- Tense: past for events, present for implications.
- Attribution: preserve the exact speaker — "According to X...", "Y said..."
- Epistemic honesty:
    * status=fact → state plainly
    * status=claim → "X claims..." / "According to X..."
    * status=rumor → "Unconfirmed reports suggest..."
    * status=disputed → "Sources conflict: X says A, Y says B"
- Numbers: use ONLY numbers provided in the events. Do NOT invent statistics.
- No hype, no emotional language, no speculation presented as fact.
- Open with the thesis tension. Use each link rationale as a transition.
- End with implications, not summary.
"""

TRADING_STYLE = """You are an experienced trader who TEACHES through market events.
The reader is a serious beginner or retail trader. Your job is to be educative,
informative, and honest — not hype, not a pump. You are a mentor.

Voice:
- Warm, direct, second-person ("you"), first-person for experience ("I've seen...").
- "Here's what this means for a trader like you."
- Plain language, but not dumbed down. Respect the reader's intelligence.
- Occasionally use trading-specific vocabulary (R-multiples, expectancy, support,
  resistance, structure, correlation, drawdown), always explained in context.

Structure (mandatory):
  1. HOOK — a striking fact, number, or question. Draw the reader in.
  2. CONTEXT — what happened; bring 2-3 events; explain the WHY.
  3. LESSON — the educative core. What principle does this demonstrate?
     How does a disciplined trader think about this? Include concrete examples.
  4. COMPARE — this vs that, now vs then, US vs global, safe-haven vs risk.
     OR: "here's what most people miss".
  5. CALL — soft closing. Invite the reader to learn more, join a community,
     or reflect on their own approach. Never pushy.

Tone rules:
- Educative and informative, never hype or "guaranteed profit" language.
- Never promise returns. Never say "this is a buy/sell signal".
- If the data is unclear, say so. Teach how to think, not what to think.
- Use numbers when provided. Never invent prices, percentages, or dates.
- Responsible: acknowledge risk whenever discussing opportunity.
"""

TRADING_EXAMPLE = """📉 WHY ARE US STOCKS FALLING? HERE'S WHAT INVESTORS NEED TO KNOW

When the S&P 500 drops $1 trillion in a single week, retail traders panic. But here's the thing — the market didn't wake up this morning and decide to fall.

It started weeks ago.

**What actually happened**

The sell-off began with a rotation out of AI-linked tech names as earnings guidance cooled. At the same time, oil prices spiked on renewed Middle East tensions, hitting shipping-sensitive sectors like airlines and logistics. Two separate stories — but they feed the same fire: uncertainty.

**The lesson every trader needs**

The market moves on **positioning**, not news. By the time you read "stocks fall" on a headline, the professional money has already repositioned. This is why chasing headlines almost always means buying someone else's exit.

What a disciplined trader does instead: watches the S&P vs the VIX, watches rotation between sectors, and asks — "Is this a structural shift or just noise?"

**Now vs then**

In March 2020, the drop was panic — no earnings, no earnings narrative, just pure fear. Today's drop? Earnings are slowing, but they're still growing. That's a different story. Different playbook.

**What to actually do**

If you're a beginner: this is not the moment to "buy the dip" because someone on TikTok said so. This is the moment to learn how to read structure — where is support, where is resistance, where is your risk.

If you're more experienced: check your position sizing. A 2% drawdown should never feel like a 20% one. If it does, your risk is too big — not the market.

I've seen more accounts blown by bad sizing than by bad picks. Every time.

Join my community if you want a framework that survives weeks like this — not a signal that only works on green days.

#SP500 #USStocks #RiskManagement #TradingEducation #MarketCycle
"""

NEWS_CONFIG = {
    "link_types": [
        "contradicts", "escalates", "responds_to",
        "contextualizes", "corroborates", "causes", "precedes",
    ],
    "event_types": [
        "statement", "military", "diplomatic", "economic",
        "protest", "report", "legal", "other",
    ],
    "status_types": ["fact", "claim", "rumor", "disputed"],
    "default_target_words": 1200,
    "default_tone": "neutral",
    "voice": "journalist, neutral, attribution-heavy",
    "article_structure": [
        {"section": "thesis", "description": "Open with the central tension"},
        {"section": "chain", "description": "Chronological causal narrative"},
        {"section": "implications", "description": "What this means going forward"},
    ],
}

NEWS_SUBTOPICS = [
    {"slug": "geopolitics", "name": "Geopolitics", "description": "Wars, diplomacy, sanctions, territorial conflict"},
    {"slug": "politics", "name": "Politics", "description": "Elections, policy, governance"},
    {"slug": "economy", "name": "Economy", "description": "Macro, trade, inflation, central banks"},
    {"slug": "society", "name": "Society", "description": "Protests, human rights, culture"},
    {"slug": "security", "name": "Security", "description": "Military action, terrorism, intelligence"},
]

TRADING_CONFIG = {
    "link_types": [
        "causes", "correlates_with", "contrasts", "explains",
        "compares", "teaches", "precedes", "responds_to", "warns",
    ],
    "event_types": [
        "price_move", "earnings", "macro_release", "regulatory",
        "analyst_call", "sentiment_shift", "teaching_post", "geopolitical",
    ],
    "status_types": ["fact", "forecast", "rumor", "opinion"],
    "default_target_words": 600,
    "default_tone": "educative",
    "voice": "experienced trader explaining market dynamics to a serious beginner",
    "article_structure": [
        {"section": "hook", "description": "Punchy opening — number, event, or question"},
        {"section": "context", "description": "What happened; 2-3 events; the WHY"},
        {"section": "lesson", "description": "The educative core — trading principle"},
        {"section": "compare", "description": "This vs that, now vs then, them vs us"},
        {"section": "call", "description": "Soft CTA — reflect, learn, join"},
    ],
    "teaching_goals": [
        "Which US stock to buy with $1000?",
        "What is the prospect of the US market?",
        "How does war affect the economy and stock market?",
        "What stock to buy on a particular day?",
        "Should we go for US stock or Canada stock?",
    ],
    "hashtags_style": "#SP500 #USStocks #TradingEducation #RiskManagement",
}

TRADING_SUBTOPICS = [
    {"slug": "us_stocks", "name": "US Stocks", "description": "S&P 500, Nasdaq, individual US equities"},
    {"slug": "canada_stocks", "name": "Canadian Stocks", "description": "TSX, Canadian equities"},
    {"slug": "gold", "name": "Gold / XAUUSD", "description": "Precious metals trading"},
    {"slug": "crypto", "name": "Crypto", "description": "BTC, ETH, altcoins"},
    {"slug": "forex", "name": "Forex", "description": "Major currency pairs"},
    {"slug": "macro", "name": "Macro", "description": "Interest rates, inflation, war, geopolitics as they affect markets"},
    {"slug": "beginners", "name": "Beginners", "description": "Fundamentals of trading for new entrants"},
]


BLOCKS = [
    {
        "slug": "news",
        "name": "News",
        "description": "World news chains — geopolitics, diplomacy, economy, society",
        "style_guide": NEWS_STYLE,
        "example_output": None,
        "config": NEWS_CONFIG,
        "subtopics": NEWS_SUBTOPICS,
    },
    {
        "slug": "trading",
        "name": "Trading",
        "description": "Educational trading articles that inform, teach, and connect events to market action",
        "style_guide": TRADING_STYLE,
        "example_output": TRADING_EXAMPLE,
        "config": TRADING_CONFIG,
        "subtopics": TRADING_SUBTOPICS,
    },
]


def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        for b in BLOCKS:
            session.execute(
                sql_text("""
                    INSERT INTO blocks (org_id, slug, name, description, style_guide,
                                        example_output, config, subtopics, is_system, is_active)
                    VALUES (NULL, :slug, :name, :description, :style_guide,
                            :example_output, CAST(:config AS jsonb), CAST(:subtopics AS jsonb),
                            true, true)
                    ON CONFLICT (slug) WHERE org_id IS NULL DO UPDATE
                    SET name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        style_guide = EXCLUDED.style_guide,
                        example_output = EXCLUDED.example_output,
                        config = EXCLUDED.config,
                        subtopics = EXCLUDED.subtopics,
                        updated_at = now()
                """),
                {
                    "slug": b["slug"],
                    "name": b["name"],
                    "description": b["description"],
                    "style_guide": b["style_guide"],
                    "example_output": b["example_output"],
                    "config": json.dumps(b["config"]),
                    "subtopics": json.dumps(b["subtopics"]),
                },
            )
        session.commit()
        print(f"Seeded {len(BLOCKS)} blocks")


if __name__ == "__main__":
    seed()
