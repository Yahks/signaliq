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
2. THE CONTRAST: Contrast the retail view with the quant view, specifically regarding this FOCUS AREA. State what the retail trader sees and what you, as a quant, actually look at instead.
3. THE RULES: Write 3 to 4 specific rules, woven into one narrative paragraph. Each rule MUST be a conditional statement: "If [condition from the source block], then I [action]." The condition must name a number, a threshold, a percentile, or a statistic. Do NOT write platitudes like "Never trust a pattern" or "Always manage risk." A rule looks like this: "If the 5-day forward win rate is under 50% and the sample size is below 30, I skip the setup." Do NOT use enumeration (First, Second, Third). Do NOT use stock transition phrases. Write it as one continuous thought.
4. THE PROOF: Use the exact numbers from the source block (the trade log, the account balance, the VIX, the ATR) to prove the rule works.
5. THE FIX: What is the rule you now follow? Introduce it in your own words, as if you were explaining the change to another trader.
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
5. THE LESSON: What the setup's outcome taught me, in statistical terms. Did the edge validate? Did the sample prove too small? Every lesson must resolve into a conditional rule: "If [specific condition from the setup history], then I [specific action]." Do NOT write platitudes. A lesson is not "be patient." A lesson is "If the t-stat is under 1.96, the edge is noise, and I skip it."
6. THE FIX: What I now do when I see this setup. Introduce the change in your own words, as if you were explaining it to another trader.
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


_TEMPLATE_DIALOGUE = """You are writing a live case study of a chart setup for quantitative traders.

You have been given a stock report with a setup history, a distribution, and a broader market context. There may or may not be a featured trade. Your job is to analyze the SETUP AGAINST ITS OWN HISTORICAL DATA in real time, as if you were sitting at the screen right now, deciding whether to act.

You are not defending a trade you already took. You are analyzing a setup the market is offering and deciding what to do about it.

KEY FOCUS AREA: This article must focus specifically on **{focus}**, viewed through the lens of **{angle}**. Every step of the analysis must be about {focus}.

VOICE & STYLE
- Write in first person. ("I look at the chart", "I pull the setup history", "I check the distribution").
- Address the reader directly. Use phrases like "If you look carefully at the chart, you'll see...", "Here is the question I always ask first...", "You might think X, but look at Y."
- Sound like a quant walking another quant through a live analysis. Show your reasoning step by step. Do not skip to the answer.

BEAT SHAPE
1. THE OBSERVATION: What the chart is showing right now. "If you look carefully at the chart, you'll see..." State the current close, the percentile, the band.
2. THE FIRST QUESTION: The first question a careful quant asks about this setup. "You might think this is a buy signal. But the first thing I check is..."
3. THE EVIDENCE: The setup history. Pull the N, the win rate, the expectancy, the t-stat, the significance verdict. State the numbers exactly.
4. THE SKEPTICAL LAYER: The second question. "But what if the sample is too small? What if the regime is different?" Check the VIX, the correlation, the segment context. See whether the setup holds up.
5. THE DECISION: What I decided to do — take the setup, skip it, or wait. State the reasoning. If you took a trade, state the entry and stop. If you skipped, state why.
6. THE OUTCOME (if a trade exists): What happened. Quote the entry, exit, result. If no trade, quote what the market did after the setup appeared (from the forward returns in the setup history).
7. THE LESSON: What the case study taught me, in conditional rule form. "If [specific condition], then I [specific action]."
8. THE FINAL RULE: One sentence. A hard-won discipline.

HARD RULES (The Delivery Check will fail if you break these)
1. EVERY NUMBER MUST COME FROM THE SOURCE BLOCK. Do not invent a price, a date, an ATR, a percentile, a forward return, or a win rate.
2. YOU MUST STATE THE EXACT TIMES IF YOU TOOK A TRADE. If there is no trade, do not mention times.
3. NO ENUMERATED LISTS. No "First," "Second," "Third," "Next," "Finally."
4. NO CAUSAL LANGUAGE. No "signaled," "suggested," "indicated," "implied."
5. NO BLOCK-HEADER NAMES IN PROSE. Describe what the chart shows.
6. NO INVENTED DIALOGUE. Do not put any character in the article other than yourself. You are thinking out loud, not talking to a character.

SOURCE BLOCK:
{source_block}

Write the article now. Aim for 850 to 1230 words."""


TEMPLATES = [_TEMPLATE_TRADE, _TEMPLATE_SETUP, _TEMPLATE_DIALOGUE]
TEMPLATE_IDS = ["trade", "setup", "dialogue"]

def pick_template(article_count: int = 0) -> tuple[str, str]:
    """Return (template_string, template_id) for this article.

    Alternates: trade, setup, trade, setup, ...
    """
    if not TEMPLATES:
        return "", "unknown"
    idx = article_count % len(TEMPLATES)
    return TEMPLATES[idx], TEMPLATE_IDS[idx]
