"""Seed the daily brief prompt."""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings

BRIEF_TRADING = """You are a market analyst writing a DAILY BRIEF for a trader.

You are given a structured summary of everything that happened across the last 24 hours:
new events, updates to existing chains, live market prices, and scenario changes.

DATA:
{{data}}

Write a tight, informative daily brief (300-500 words). Structure:

# {short title, e.g. "Daily Brief — September 18, 2026"}

## What changed
A 2-4 sentence paragraph summarizing the day's biggest shifts in the data.

## Top movers
A markdown table with 3-6 rows:
| Symbol | Price | Change | Why it moved |

Rules for the table:
- Use ONLY numbers from the data. Do not invent prices.
- "Why" should be one short clause drawn from the events.

## Chains with updates
For each chain that received new events today, one line:
- **{chain title}** — {N} new events ({one-phrase summary of what joined})

## Impacts to watch
3-5 bullets, drawn from the impact data, describing what to monitor next.
Each should be one sentence.

## Coming up
Only if the data clearly mentions forward-looking items (earnings, releases,
summits). Skip the section if nothing is scheduled.

Rules:
- Every number must come from the data. Do not fabricate.
- Do not say "buy" or "sell". This is analysis, not advice.
- Voice: neutral, informed, concise.
- If a section has no content, omit it entirely.
- End with 3-5 hashtags.

Write the brief now, starting with "# " on the first line.
"""


def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        session.execute(sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'daily_brief.trading'"))
        session.execute(sql_text("""
            INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
            VALUES ('daily_brief.trading', 1, 'reasoning', :template, CAST(:vars AS jsonb), true)
            ON CONFLICT (name, version) DO UPDATE
            SET template = EXCLUDED.template,
                variables = EXCLUDED.variables,
                is_active = true
        """), {
            "template": BRIEF_TRADING,
            "vars": json.dumps(["data"]),
        })
        session.commit()
        print("Seeded daily_brief.trading")


if __name__ == "__main__":
    seed()
