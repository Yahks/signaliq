"""Seed the stock-event extraction prompt."""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings

PROMPT = """You are a stock-market analyst distilling a news item or SEC filing into ONE structured event.

SOURCE DATA:
  source: {{source_name}}
  source_url: {{source_url}}
  ticker: {{ticker}}
  published_at: {{published_at}}
  title: {{title}}
  summary: {{summary}}
  matched_keywords: {{matched_keywords}}
  full_content: {{content}}

Produce a single JSON object describing the ONE event this source describes.

Schema:
{
  "headline": "concise, action-oriented headline (max 150 chars)",
  "summary": "2-4 sentence factual summary of what happened",
  "actors": ["companies, tickers, people mentioned"],
  "event_type": "price_move|earnings|macro_release|regulatory|analyst_call|sentiment_shift|teaching_post|geopolitical|report",
  "status": "fact|forecast|rumor|opinion",
  "confidence": 0.0 to 1.0,
  "location": "optional, e.g. 'New York, USA' or null"
}

Rules:
- headline must be specific and factual, not clickbait.
- Do NOT invent numbers. Use only figures present in the source.
- If a filing, describe what the filing is about (e.g. "Apple files 10-Q showing $X revenue").
- If unclear, prefer event_type=report and status=fact with lower confidence.
- Return ONLY the JSON object, nothing else.

JSON:"""


def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'extract.stock_event.v1'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('extract.stock_event.v1', 1, 'fast', :template, CAST(:vars AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    is_active = true
            """),
            {
                "template": PROMPT,
                "vars": json.dumps([
                    "source_name", "source_url", "ticker", "published_at",
                    "title", "summary", "matched_keywords", "content",
                ]),
            },
        )
        session.commit()
        print("Seeded extract.stock_event.v1")


if __name__ == "__main__":
    seed()
