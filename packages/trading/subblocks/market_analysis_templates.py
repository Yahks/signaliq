"""Template for market_analysis: the survival guide."""

import random


_TEMPLATE_TRADE = """You are writing a survival guide for quantitative traders.

KEY FOCUS AREA: This article must focus specifically on **{focus}**, viewed through the lens of **{angle}**. Do not write generic trading advice. Every rule must be about how to handle {focus}.

You have been given two sets of reports: 
1. THE MARKET CONTEXT (the VIX, the asset class moves, the broad tape).
2. THE STOCK REPORT (the specific trade you executed and its chart).
You must connect them. Do not invent VIX numbers. Use the numbers in the MARKET CONTEXT block. Do not invent stock prices. Use the numbers in the STOCK REPORT block.

Your job is to write a professional quant essay that distills statistically grounded rules from your experience. You are not just telling a story about one trade; you are teaching a lesson. 

VOICE & STYLE
- Write in first person. ("I measured", "My model", "I paid for this in real money").
- Sound like a quant talking to another quant. Direct, humble, and brutally honest. Use terms like 'forward returns', 'sample size', 'overlay', and 'risk-adjusted'.
- Do NOT write "the trader". Write "I".

BEAT SHAPE
1. THE CONFESSION: Start with a failure related to this article's specific FOCUS AREA. Frame it as a quant: 'My sample size was too small' or 'I didn't account for the volatility regime'. 
2. THE CONTRAST: Contrast the retail trader with the institutions, specifically regarding this FOCUS AREA. (e.g., 'Retail sees a pattern. A quant sees a probability distribution and a sample size.')
3. THE RULES: Write 3 to 4 specific rules, but weave them into a single narrative paragraph. Do NOT use "First," "Second," "Third," or any enumeration. Instead, use transitions that build: "The first lesson was that...", "That insight led to a second constraint:...", "The final piece of the puzzle was..." The paragraph should read like one continuous thought, not a numbered list.
4. THE PROOF: Use the exact numbers from the source block (the trade log, the account balance, the VIX, the ATR) to prove the rule works.
5. THE FIX: What is the rule you now follow? You MUST start this paragraph with the exact phrase "I now" or "Now I". (e.g., "I now write down three lines before every trade...").
6. THE FINAL RULE: At the very end of the text (before the charts), give one final hard-earned rule. It should be a psychological trigger or a strict discipline (e.g., "Write it down, or it doesn't count" or "Never add to a loser"). Do NOT write a list of questions. If the model writes a checklist, the delivery check will fail.

HARD RULES (The Delivery Check will fail if you break these)
1. EVERY NUMBER MUST COME FROM THE SOURCE BLOCK. Do not invent a price, a date, an ATR value, or a stop level. 
   - If you need a VIX number or a market segment return, pull it from THE MARKET CONTEXT block.
   - If you need an ATR, a stock price, or a chart pattern, pull it from THE STOCK REPORT block.
   - If a number is not in either block, do not write it.
   - DO NOT INVENT DATES FOR CHART HIGHS AND LOWS. If the source block gives you a price high (e.g. 301.76) but no date, just write the price. Do NOT invent a date like "April 20".
2. YOU MUST STATE THE EXACT TIMES. In the proof section, you must explicitly write the exact timestamp of when you traded and when you exited. For example: "At 14:46 UTC on October 16, I entered..." and "I exited at 16:26 UTC." Do not say "in the morning".
3. NO CAUSAL LANGUAGE. You are strictly forbidden from using words like "signaled", "suggested", "indicated", or "implied". State what the number is, and state what you did.
4. NO ENUMERATED LISTS. Do NOT write "First", "Second", "Third", "One", "Two", "Three", "Next", or "Finally". You are writing an essay, not a bulleted list. Use paragraph breaks and transitions.
5. NO META-COMMENTARY. Do NOT write "If you found this post insightful", "Follow my blog", "In this article", "Let me explain", or "Here is what I learned." Just tell the story.
6. NO BLOCK-HEADER NAMES IN PROSE. Do NOT write "the DAILY VIEW", "the DISTRIBUTION", "the CORRELATION", or any other uppercase block header from the source block. Describe what the chart shows. Instead of "the DAILY VIEW showed", write "the daily chart showed". Instead of "the DISTRIBUTION showed", write "the 90-day histogram showed". Instead of "the CORRELATION read", write "the 20-day rolling correlation read".

SOURCE BLOCK:
{source_block}

Write the article now. Aim for 850 to 1230 words."""


_TEMPLATE_SETUP = """You are writing a setup analysis for quantitative traders.

You have been given a stock report with a setup history, a distribution histogram, and a broader market context. There is no featured trade. Your job is to write about the SETUP ITSELF — what the chart is offering today, what the history says about it, and what you decided to do.

Do not invent a trade. Do not talk about entries, stops, or exits. The subject is the setup.

KEY FOCUS AREA: This article must focus specifically on **{focus}**, viewed through the lens of **{angle}**. Every rule must be about how to read {focus} through {angle}.

VOICE & STYLE
- Write in first person. ("I measured", "My model", "I now require").
- Sound like a quant talking to another quant. Use terms like sample size, forward returns, t-statistic, expectancy, significance.
- Do NOT write a retail survival guide. Do NOT write generic advice.

BEAT SHAPE
1. THE SETUP: What the chart shows today. State the current close, the percentile, the band. Be specific.
2. THE STATISTICS: What the setup history says. Quote the N, the win rate, the expectancy, the t-statistic, the significance verdict. Do NOT invent a sample. If the source block gives you "N=3, win rate=33%, t-stat=-1.13, significant=NO", quote those exact numbers.
3. THE DECISION: What I did about it. Did I take the setup, skip it, or wait for confirmation? State the reasoning in terms of the statistics.
4. THE OUTCOME: What the price did over the following days. Quote the forward returns from the setup history (e.g., "the next 5 days were -2.15%, -1.43%, and -2.26%").
5. THE LESSON: What the setup's outcome taught me, in statistical terms. Did the edge validate? Did the sample prove too small?
6. THE FIX: What I now do when I see this setup. You MUST start this paragraph with the exact phrase "I now" or "Now I".
7. THE FINAL RULE: One sentence. A hard-won discipline. Not a question.

HARD RULES (The Delivery Check will fail if you break these)
1. EVERY NUMBER MUST COME FROM THE SOURCE BLOCK. Do not invent a price, a date, an ATR, a percentile, or a forward return.
2. NO BLOCK-HEADER NAMES IN PROSE. Do NOT write "the SETUP HISTORY" or "the DISTRIBUTION". Describe what the chart shows.
3. NO ENUMERATED LISTS. No "First," "Second," "Third," "Next," "Finally."
4. NO CAUSAL LANGUAGE. No "signaled," "suggested," "indicated," "implied."
5. NO META-COMMENTARY. No "in this article," "let me explain," "stay tuned."
6. NO QUESTIONS AT THE END. The final beat is a rule, not a checklist.

SOURCE BLOCK:
{source_block}

Write the article now. Aim for 850 to 1230 words."""


TEMPLATES = [_TEMPLATE_TRADE, _TEMPLATE_SETUP]
TEMPLATE_IDS = ["trade", "setup"]

def pick_template(article_count: int = 0) -> tuple[str, str]:
    """Return (template_string, template_id) for this article.

    Alternates: trade, setup, trade, setup, ...
    """
    if not TEMPLATES:
        return "", "unknown"
    idx = article_count % len(TEMPLATES)
    return TEMPLATES[idx], TEMPLATE_IDS[idx]
