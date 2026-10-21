"""Templates for market_analysis.

Three templates rotate:
  _TEMPLATE_TRADE    — post-mortem of a specific trade
  _TEMPLATE_SETUP    — statistical analysis of a setup (no trade)
  _TEMPLATE_DIALOGUE — live case study of a setup vs its own history
"""

import random


# ─────────────────────────────────────────────────────────────────────
# Shared preamble for all three templates
# ─────────────────────────────────────────────────────────────────────
_PREAMBLE = """
CRITICAL RULE #1 — CHART PLACEMENT. Every time you describe what a chart shows, you MUST emit the corresponding tag ON ITS OWN LINE, with a blank line before and after. This is mandatory.

Available tags:
[CHART: daily]          — for the daily/primary price chart
[CHART: distribution]   — for the 90-day return histogram
[CHART: setup]          — for the setup history
[CHART: returns]        — for the returns by window
[CHART: correlation]    — for the rolling correlation

Example of CORRECT placement:
  The daily chart shows a grind from 340 to 351 over 40 bars, then a 2% gap up.
  [CHART: daily]
  The 90-day histogram has 48 up days vs 42 down, with a fat left tail.
  [CHART: distribution]

Example of WRONG placement (fails delivery check):
  ... all paragraphs ...
  ## Charts
  ![daily](...)
  ![distribution](...)

CRITICAL RULE #2 — NO CHECKLIST. Do NOT end the article with a list of questions like "Can I confirm...", "Do I verify...", "Have I checked...". The article ends with a RULE, not a checklist. Any "Do I", "Can I", or "Have I" question at the end fails the delivery check.

CRITICAL RULE #3 — NO PROCESS NARRATION. Do NOT write "the first step is...", "I apply the filter...", "next I look at...". Just do the analysis. Say "the percentile is at 0th" not "the first filter I apply is the percentile."

CRITICAL RULE #4 — LENGTH. Write at least 850 words. Each beat below specifies a MINIMUM word count. Total must reach 850 words. Articles under 630 words are rejected.
"""


# ─────────────────────────────────────────────────────────────────────
# TEMPLATE 1: TRADE (post-mortem of a specific trade)
# ─────────────────────────────────────────────────────────────────────
_TEMPLATE_TRADE = """You are writing a post-mortem of a specific trade for quantitative traders.

You have been given a stock report, a featured trade with its chart, and a comparison trade.

KEY FOCUS AREA: This article must focus specifically on **{focus}**, viewed through the lens of **{angle}**.

VOICE
- First person. ("I entered", "I watched", "The rule I now follow").
- Quant-to-quant. Sample size, forward returns, t-statistics, expectancy.
""" + _PREAMBLE + """

BEAT SHAPE (with minimum word counts)
1. THE CONFESSION (150 words minimum): The trade, what it cost or made. Entry, stop, exit, result.
2. THE CONTRAST (100 words minimum): Retail view vs. quant view.
3. THE RULES (200 words minimum): 3–4 conditional rules. Each rule = "If X, then I Y." Ground each in the source numbers.
4. THE PROOF (150 words minimum): Exact numbers, exact entry/exit timestamps.
5. THE FIX (100 words minimum): The change to my process. Start with a first-person commitment.
6. THE COMPARISON (150 words minimum): Featured trade vs. comparison trade. What was different?
7. THE FINAL RULE (40-60 words): The rule in its strongest form, standing on its own. State the condition, the action, and what violating it costs. Do NOT write 'The final rule:' as a label — write the rule directly. The reader knows it's the final rule from its position, not its prefix.


MULTI-TIMEFRAME CHECK (MANDATORY):
The source block contains a THE RETURNS section with 20-day,
12-week, 6-month, and 5-year returns. You MUST reference at least
two of these returns in the body. If the short-horizon return
disagrees with the long-horizon return (e.g., 20-day negative,
12-week positive), explain what the divergence means for this
setup. If they agree, state that the setup has consistent
support across timeframes.


MULTI-TIMEFRAME CHECK (MANDATORY):
The source block contains a THE RETURNS section with 20-day,
12-week, 6-month, and 5-year returns. You MUST reference at least
two of these returns in the body. If the short-horizon return
disagrees with the long-horizon return (e.g., 20-day negative,
12-week positive), explain what the divergence means for this
setup. If they agree, state that the setup has consistent
support across timeframes.

HARD RULES
1. EVERY NUMBER from the source block. No invented prices, dates, ATRs, or stops.
2. MUST STATE ENTRY AND EXIT TIMES (e.g., "14:46 UTC" and "17:16 UTC").
3. NO enumerated lists.
4. NO causal language ("signaled", "suggested").
5. NO meta-commentary.
6. NO block-header names in prose (say "the daily chart", not "THE DAILY VIEW").

SOURCE BLOCK:
{source_block}

Write the article now. Aim for 850 to 1230 words. Count as you go."""


# ─────────────────────────────────────────────────────────────────────
# TEMPLATE 2: SETUP (statistical analysis, no trade)
# ─────────────────────────────────────────────────────────────────────
_TEMPLATE_SETUP = """You are writing a statistical analysis of a chart setup for quantitative traders.

There is no featured trade. The subject is the setup.

KEY FOCUS AREA: This article must focus specifically on **{focus}**, viewed through the lens of **{angle}**.

VOICE
- First person. ("I measured", "My model", "The rule I now follow").
- Quant-to-quant. Sample size, forward returns, t-statistics.
""" + _PREAMBLE + """

BEAT SHAPE (with minimum word counts)
1. THE SETUP (120 words minimum): Chart state — close, percentile, band.
2. THE STATISTICS (180 words minimum): N, win rate, expectancy, t-statistic, significance. Quote exact numbers.
3. THE DECISION (100 words minimum): Take, skip, or wait. State the reasoning.
4. THE OUTCOME (100 words minimum): Forward returns from the setup history.
5. THE LESSON (150 words minimum): What the outcome taught me, in statistical terms. Resolve into a conditional rule.
6. THE FIX (100 words minimum): The change to my process.
7. THE FINAL RULE (40-60 words): The rule in its strongest form, standing on its own. State the condition, the action, and what violating it costs. Do NOT write 'The final rule:' as a label — write the rule directly. The reader knows it's the final rule from its position, not its prefix.

HARD RULES
1. EVERY NUMBER from the source block.
2. NO enumerated lists.
3. NO causal language.
4. NO meta-commentary.
5. NO block-header names in prose.

SOURCE BLOCK:
{source_block}

Write the article now. Aim for 850 to 1230 words. Count as you go."""


# ─────────────────────────────────────────────────────────────────────
# TEMPLATE 3: DIALOGUE (live case study)
# ─────────────────────────────────────────────────────────────────────
_TEMPLATE_DIALOGUE = """You are writing a live case study of a chart setup for quantitative traders.

You are analyzing a setup the market is offering and deciding what to do.

KEY FOCUS AREA: This article must focus specifically on **{focus}**, viewed through the lens of **{angle}**.

VOICE
- First person. ("I look at the chart", "I pull the setup history").
- Address the reader. "If you look carefully at the chart, you'll see..."
""" + _PREAMBLE + """

BEAT SHAPE (with minimum word counts)
1. THE STAKE (100 words minimum): The decision you're trying to make. Frame as tension. Do NOT open with "If I stare..." or "If you look carefully...".
2. THE FIRST QUESTION (100 words minimum): The first question a careful quant asks.
3. THE EVIDENCE (150 words minimum): Setup history. N, win rate, expectancy, t-statistic, significance.
4. THE SKEPTICAL LAYER (120 words minimum): "But what if the sample is too small?" Check VIX, correlation, segment.
5. THE DECISION (100 words minimum): Take, skip, or wait. If trade, state entry and stop.
6. THE COMPARISON (120 words minimum): Featured trade vs. comparison trade.
7. THE OUTCOME (100 words minimum): What happened. Entry, exit, result. Or forward returns.
8. THE LESSON (100 words minimum): The conditional rule that emerged.
9. THE FINAL RULE (40-60 words): The rule in its strongest form, standing on its own. State the condition, the action, and what violating it costs. Do NOT write 'The final rule:' as a label — write the rule directly. The reader knows it's the final rule from its position, not its prefix.

HARD RULES
1. EVERY NUMBER from the source block.
2. IF YOU TOOK A TRADE: MUST STATE ENTRY AND EXIT TIMES (e.g., "14:46 UTC", "17:16 UTC").
3. NO enumerated lists.
4. NO causal language.
5. NO meta-commentary.
6. NO block-header names in prose.
7. NO invented dialogue. You are thinking out loud.

SOURCE BLOCK:
{source_block}

Write the article now. Aim for 850 to 1230 words. Count as you go."""


# ─────────────────────────────────────────────────────────────────────
# TEMPLATE 4: PROCESS (trader's behavior across many trades)
# ─────────────────────────────────────────────────────────────────────
_TEMPLATE_PROCESS = """You are writing a process analysis for quantitative traders.

You have been given the trader's full trade log (aggregated statistics across all trades) and the account equity curve. There is no featured trade. The subject is the PROCESS — how the trader sizes, manages risk, and responds to regimes.

KEY FOCUS AREA: This article must focus specifically on **{focus}**, viewed through the lens of **{angle}**.

VOICE
- First person. ("I measured", "My model", "The rule I now follow").
- Quant-to-quant. Sample size, forward returns, t-statistics, expectancy.

CRITICAL RULE #1 — NO CHECKLIST. Do NOT end the article with a list of questions like "Can I confirm...", "Do I verify...". The article ends with a RULE.
CRITICAL RULE #2 — NO PROCESS NARRATION. Do NOT write "the first step is..." or "I apply the filter..." — just do the analysis.
CRITICAL RULE #3 — LENGTH. Write at least 850 words. Each beat below specifies a minimum.

BEAT SHAPE (with minimum word counts)
1. THE OBSERVATION (120 words minimum): What the aggregated trade log revealed. A specific pattern across the last N trades. e.g., "I measured 900 trades and found that my worst drawdowns correlate with position size above the 90th percentile."
2. THE STATISTICS (180 words minimum): The numbers. Win rate, expectancy, average win, average loss, max drawdown, position size distribution. Quote the exact statistics from the trade log summary.
3. THE DIAGNOSIS (150 words minimum): What is causing the pattern. Is it regime-specific? Is it correlated with volatility? Is it a sizing bias?
4. THE PROOF (150 words minimum): Specific trades or windows that illustrate the pattern. Reference specific dates and results from the trade log.
5. THE FIX (150 words minimum): The process change. Written as a conditional rule: "If [condition from the trade log], then I [action]." The condition MUST use a number from the trade-log summary (median size, win rate, event-day penalty, slippage, etc.). Do NOT invent a threshold like "30-day realized volatility" if it isn't in the summary. If the summary doesn't name it, don't say it.
6. THE LESSON (100 words minimum): What the pattern taught me, stated as a general principle.
7. THE FINAL RULE (40-60 words): The rule in its strongest form, standing on its own. State the condition, the action, and what violating it costs. Do NOT write 'The final rule:' as a label — write the rule directly. The reader knows it's the final rule from its position, not its prefix.


STRATEGY COMPARISON (MANDATORY):
The source block contains a STRATEGY BREAKDOWN section with win
rate and expectancy by setup type. You MUST reference at least
two strategy types. Compare their win rates and expectancies. If
one strategy is outperforming in the current regime, say which
and why.

MULTI-TIMEFRAME CHECK (MANDATORY):
The source block contains a THE RETURNS section with 20-day,
12-week, 6-month, and 5-year returns. You MUST reference at least
two of these returns in the body. If the short-horizon return
disagrees with the long-horizon return (e.g., 20-day negative,
12-week positive), explain what the divergence means for this
setup. If they agree, state that the setup has consistent
support across timeframes.

HARD RULES
1. EVERY NUMBER from the trade log summary or the account block. Do not invent. Do NOT invent a threshold that isn't in the summary (e.g., "30-day realized volatility above the 70th percentile" — if the summary doesn't have a 30-day volatility or a 70th percentile, do not write it). If you need a number to make a rule conditional, use the numbers in the summary (median size, win rate, event-day penalty, slippage) — not numbers from your imagination.
2. NO enumerated lists.
3. NO causal language.
4. NO meta-commentary.
5. NO block-header names in prose.
6. NO invented trades. Only reference trades from the summary.

SOURCE BLOCK:
{source_block}

Write the article now. Aim for 850 to 1230 words. Count as you go."""


TEMPLATES = [_TEMPLATE_TRADE, _TEMPLATE_SETUP, _TEMPLATE_DIALOGUE, _TEMPLATE_PROCESS]
TEMPLATE_IDS = ["trade", "setup", "dialogue", "process"]


def pick_template(article_count: int = 0) -> tuple[str, str]:
    """Return (template_string, template_id) for this article."""
    if not TEMPLATES:
        return "", "unknown"
    idx = article_count % len(TEMPLATES)
    return TEMPLATES[idx], TEMPLATE_IDS[idx]
