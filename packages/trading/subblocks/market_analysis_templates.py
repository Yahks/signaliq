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
3. THE RULES: Write 3 to 4 specific, hard-earned rules. Ground each rule in the data from the source block. The rules must directly address the FOCUS AREA and the ANGLE.
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

SOURCE BLOCK:
{source_block}

Write the article now. Aim for 850 to 1230 words."""


TEMPLATES = [_TEMPLATE_TRADE]
TEMPLATE_IDS = ["trade"]

def pick_template(article_count: int = 0) -> tuple[str, str]:
    """Return (template_string, template_id) for this article."""
    if not TEMPLATES:
        return "", "unknown"
    idx = article_count % len(TEMPLATES)
    return TEMPLATES[idx], TEMPLATE_IDS[idx]
