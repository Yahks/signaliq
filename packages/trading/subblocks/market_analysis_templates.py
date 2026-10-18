"""Template for market_analysis: the trade is the spine."""

import random


_TEMPLATE_NOTEBOOK = """You are a systematic trader. You write
a personal notebook entry about one trade.

your trade:
  - The trade: entry, stop, target, exit, result.
you research:
  - The chart data: the daily view, the distribution, the
    returns, the setup history, the correlation.
  - Your dashboard: volatility, correlation, candidate count.
  - The pattern in your own trade history, if there is one.

Write the entry as a story. Not a report. Not a set of
sections. Not "The finding is" or "The verdict is" or "The
rule I extracted is." The reader follows you through what
you saw, what you thought, what you did, what happened,
and what you'll do differently now.
you have done intensive researches, the chart data are research data, 
readers would like to know what to learn from you from the market you have analyse as you placed those trade

No headings. No section names. No bullet points. No
numbered lists. Continuous prose. Let the entry move.

Every number has a unit. Prices are in dollars ($173.57).
ATR is in points (5.98 points). Contracts are shares (100
shares). Percentages have % (0.82%). Times have UTC or CT
(14:46 UTC). If you write a number, keep its unit.


850 to 1230 words. Aim for 1000.

{source_block}

End with this CTA:

{cta_text}"""


TEMPLATES = [_TEMPLATE_NOTEBOOK]
TEMPLATE_IDS = ["notebook"]


def pick_template(article_count: int = 0) -> tuple[str, str]:
    """Return (template_string, template_id) for this article.

    Cycle: TRADE, STOCK, METHOD. Every third article uses the
    same template as one three articles ago; the others differ.
    The id is stored in source_payload so the template that
    produced each article is auditable.
    """
    if not TEMPLATES:
        return "", "unknown"
    idx = article_count % len(TEMPLATES)
    return TEMPLATES[idx], TEMPLATE_IDS[idx]
