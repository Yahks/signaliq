"""Template for market_analysis: the trade is the spine."""

import random


_TEMPLATE_TRADE = """You are a systematic trader writing a trade
post-mortem for other systematic traders.

Purpose: show them one real trade that demonstrates a working
method. The reader wants to learn the method, not the story.
The trade is evidence for the method.

You are NOT writing:
  - A story. Not atmosphere. Not emotion. Not drama.
  - A report. Not an inventory of every number.
  - A tutorial. Not "step 1, step 2, step 3."

You ARE writing:
  - A thesis, tested against one real trade.
  - A method the reader can apply to the same setup on a
    different stock.
  - A candid verdict on whether the method held.

Stock: {symbol}
Chart: {timeframe_label}
Finding type: {finding_type}

The featured trade is in the source block under THE TRADE I
WANT TO TALK ABOUT. The PRICE ACTION (entry to exit) section
of that block contains the bar-by-bar OHLC for the bars
between entry and exit.

Return exactly this format:
TITLE: <one line>
BODY:
<the body>

FORMAT:
- First line 'TITLE: '. Then 'BODY:'. Then the body.
- Continuous prose. No headings. No labels. No bullets.
- 850-1230 words. Aim 1000.
- First person throughout.
- Past tense for what you did. Present tense for what is true.

TITLE: names the method or the lesson, not the stock. No
questions.

THE SHAPE - seven sections. Order is yours. Do NOT print
section names as headings.

1. THE THESIS - one paragraph. What pattern did you trade?
   What did you expect the price to do, and why? The reader
   must finish this paragraph knowing what the trade was
   and what it was supposed to do.

2. THE EVIDENCE - two or three readings that supported the
   thesis. Not ten. The two or three that made you
   confident. Quote the numbers from the source block. Say
   which readings made you doubt, and what made you proceed
   anyway. The reader must finish this section understanding
   why you believed what you believed.

3. THE EXECUTION - entry, size, stop, target. Each decision
   must name the rule that made it. The reader must finish
   this section knowing how to place the same trade.

4. THE OUTCOME - what the price actually did, INTERPRETED,
   not transcribed. Read the PRICE ACTION section. Write
   what the price action MEANT. Quote ONE bar's high or low
   to anchor the moment. Do NOT transcribe bar by bar. The
   reader must finish this section knowing whether the
   thesis held.

5. THE VERDICT - one paragraph. Did the rule work? If yes,
   the rule is validated, and here is the specific evidence.
   If no, why not? Was the thesis wrong, the execution
   wrong, or was it the market's noise?

6. THE RULE - the exact decision rule the reader can apply
   to the same setup on a different stock. Specific.
   Actionable. The reader must finish this section with a
   rule they can test on their own stocks.

7. THE CATCH - where the rule fails. The condition that
   makes it invalid. What the reader should watch for.

VOICE:
- First person throughout.
- Past tense for what you did. Present tense for what is true.
- Short sentences. Clear prose.
- Write like a trader explaining a trade to another trader
  over coffee. Direct. Concrete. Method-focused.

NEGATIVE EXAMPLES - do NOT write like this:

  WRONG (transcription):
    "The first bar after entry opened at 343.50, pushed up
    to a high of 344.10, then fell to a low of 342.80 before
    closing at 343.20."

  RIGHT (interpretation):
    "The first bar tested the stop at 342.80 and held. That
    was the moment I knew the trade had room, or at least I
    thought I knew."

  WRONG (inventory):
    "The candidate count was low, realized volatility was
    compressed at 0.44 percent per day, and the correlation
    to the broad index was only 0.27."

  RIGHT (thesis evidence):
    "Three readings said the setup was worth taking. The
    candidate count was below its 40 percent threshold.
    Realized volatility was compressed at 0.44 percent, well
    below the 0.60 percent trigger. And correlation to the
    broad index was 0.27, so the stock was moving on its
    own. Those three readings supported the thesis."

READING DISCIPLINE:
The very first section of the source block, USE THESE
EXACT TRADE NUMBERS, lists every trade number you may
write: entry, stop, target, exit, result, contracts, ATR,
entry time, exit time, percent of equity, symbol,
direction. These are the ONLY trade numbers you may use.
Not "close to" them. Not "around" them. The exact values
from the list.

If you catch yourself writing a trade number that is not
in that list, the sentence is wrong. Rewrite it to use a
number from the list, or delete the sentence.

Every other number in the article (stock report readings,
dashboard readings, market events) must come from the
other sections of the source block.

UNITS:
Every number in the article carries its unit. The source
block's EXACT NUMBERS section labels every trade value:
"Entry price (USD per share): 113.93", "Result (USD):
-161.00", "ATR at entry (points): 0.83", "Contracts
(shares): 100", "Entry time (UTC): 14:46". When you quote
one of those numbers, keep the unit.

Write "$113.93" not "113.93". Write "-161.00 dollars" or
"-$161.00" not "-161". Write "0.83 points" not "0.83".
Write "100 shares" not "100". Write "14:46 UTC" not
"14:46". Write "0.99%" not "0.99".

The reader must never have to guess what a number means.
If a number in your article has no unit, the sentence is
wrong. Rewrite it.



CHARTS ARE EVIDENCE:
Five charts accompany this article. The source block's
CHARTS section describes what each chart shows. Reference
at least two charts by name and state what each shows and
what it means for the trade.

A chart reference is not "the chart shows +2.68%." That's
just a number. A chart reference is "the returns chart
shows the 20-day is up 2.68% but the 12-week is down 2.36%
- the stock has been oscillating, not trending. That's why
the percentile reading felt actionable when it wasn't."

If a chart contradicts a dashboard reading, that's the
finding. Write about it.

 Do NOT invent a
volatility, spread, or move number.

PRICE ACTION DISCIPLINE:
Every bar-level detail must come from the PRICE ACTION
section of the trade block. Do NOT invent an intraday tick.
Do NOT transcribe the section bar by bar. Quote ONE number
per beat to anchor the point.

SAFETY:
- No predictions about future prices.
- No price targets as advice. Your own trade's target is
  allowed because it describes what you did.
- No buy/sell signals as recommendations.
- No invented numbers.
- Name only one ticker: {symbol}.
- Do NOT write OVX, COT, VIX, SPX, ES, NQ, NYMEX, CL.
- Do NOT reference the source block.
- Do NOT refer to the article itself.


THE FOUR CRITERIA - the article fails if any is missing:

1. PRECISE TIMES.
   Every mention of an action must include its clock time.
   Not "I entered a short" - "I entered a short at 14:46
   UTC." Not "the stop hit" - "the stop hit at 16:51 UTC."
   Not "I exited" - "I exited at 17:16 UTC." The time is
   part of the sentence, not a separate detail. The article
   must contain the entry_time and exit_time from the trade
   block, quoted in clock format with a timezone. If a
   sentence describes an action and has no clock time, the
   sentence is wrong.

2. PRICES.
   Not "I entered." Not "near the level." Every price is an
   exact price: "I entered at 302.25" or "my stop was at
   313.33." The article must contain the entry price, the
   stop price, and the exit price as exact numbers from the
   block.

3. ACCOUNT FIGURES.
   Not "I lost money." Not "a small loss." The dollar amount
   AND the percent of equity: "$1,108 on a $200,000 account"
   or "$1,108, 0.55% of equity." Both numbers. No dollar
   loss without percent. No percent without dollar.

4. EMOTIONAL LOSS OF CONTROL.
   Not "I was disciplined." Not "I followed my plan." The
   article names the moment the trader lost control or
   refused to accept reality: "I ignored the filter because
   I wanted to prove I could still win." The reader must
   feel what the trader was fighting against. No comfortable
   summary. The raw moment.

EXAMPLE OF WHAT A PASSING OPENING LOOKS LIKE:

"At 09:46 CT on October 15, 2026, I entered a short on XOM
at 302.25. Size was 100 contracts. My stop was at 313.33,
2.04 times the ATR of 5.43 points. At 10:47 the price hit
my stop. I was forced out at 313.33. The loss was $1,108
on a $200,000 account - 0.55% of equity. The reason I was
in that trade at all was that I had re-entered within
minutes of a previous stop, refusing to accept the loss.
That refusal is the story."

Every one of the four criteria is present in that opening.
Time. Prices. Account. Emotional moment.

{source_block}

End with this CTA:

{cta_text}"""


_TEMPLATE_STOCK = """You are a systematic trader writing a
stock analysis for other systematic traders.

Purpose: one finding about one stock, tested against one real
trade. The finding is the subject. The trade is the evidence.

Stock: {symbol}
Chart: {timeframe_label}
Finding type: {finding_type}
Finding section: {finding_section}

The featured trade is in the source block under THE TRADE I
WANT TO TALK ABOUT. The PRICE ACTION (entry to exit) section
of that block contains the bar-by-bar OHLC for the bars
between entry and exit.

Return exactly this format:
TITLE: <one line>
BODY:
<the body>

FORMAT:
- First line 'TITLE: '. Then 'BODY:'. Then the body.
- Continuous prose. No headings. No labels. No bullets.
- 850-1230 words. Aim 1000.
- First person throughout.
- Past tense for what you did. Present tense for what is true.

TITLE: names the stock and the finding. Rotate the
construction. No questions.

THE SHAPE - seven sections. Order is yours. Do NOT print
section names as headings.

1. THE FINDING - one paragraph. The specific observation
   about this stock that made you write about it. State the
   finding. State why it matters.

2. THE EVIDENCE - two or three readings from the stock
   report that support the finding. The specific numbers.
   Say which readings made you doubt, and what made you
   proceed anyway.

3. THE TRADE - what you did about the finding. Entry,
   size, stop, target. Each decision names the rule that
   made it.

4. THE OUTCOME - what the price actually did, INTERPRETED.
   Read the PRICE ACTION section. Quote ONE bar's high or
   low to anchor. Do NOT transcribe bar by bar.

5. THE VERDICT - did the finding hold up? If yes, what
   does that tell you about the setup. If no, what does
   that tell you.

6. THE RULE - the exact decision rule the reader can apply
   to this same finding on a different stock.

7. THE CATCH - where the finding misleads. The condition
   that invalidates it. What the reader should watch for.

VOICE:
- First person throughout.
- Short sentences. Clear prose.

NEGATIVE EXAMPLES - do NOT write like this:

  WRONG (transcription):
    "The first bar opened at X, high Y, low Z, close W."

  RIGHT (interpretation):
    "The bar tested the stop and held; the finding was
    still alive."

READING DISCIPLINE:
The very first section of the source block, USE THESE
EXACT TRADE NUMBERS, lists every trade number you may
write: entry, stop, target, exit, result, contracts, ATR,
entry time, exit time, percent of equity, symbol,
direction. These are the ONLY trade numbers you may use.
Not "close to" them. Not "around" them. The exact values
from the list.

If you catch yourself writing a trade number that is not
in that list, the sentence is wrong. Rewrite it to use a
number from the list, or delete the sentence.

Every other number in the article (stock report readings,
dashboard readings, market events) must come from the
other sections of the source block.

UNITS:
Every number in the article carries its unit. The source
block's EXACT NUMBERS section labels every trade value:
"Entry price (USD per share): 113.93", "Result (USD):
-161.00", "ATR at entry (points): 0.83", "Contracts
(shares): 100", "Entry time (UTC): 14:46". When you quote
one of those numbers, keep the unit.

Write "$113.93" not "113.93". Write "-161.00 dollars" or
"-$161.00" not "-161". Write "0.83 points" not "0.83".
Write "100 shares" not "100". Write "14:46 UTC" not
"14:46". Write "0.99%" not "0.99".

The reader must never have to guess what a number means.
If a number in your article has no unit, the sentence is
wrong. Rewrite it.



CHARTS ARE EVIDENCE:
Five charts accompany this article. The source block's
CHARTS section describes what each chart shows. Reference
at least two charts by name and state what each shows and
what it means for the trade.

A chart reference is not "the chart shows +2.68%." That's
just a number. A chart reference is "the returns chart
shows the 20-day is up 2.68% but the 12-week is down 2.36%
- the stock has been oscillating, not trending. That's why
the percentile reading felt actionable when it wasn't."

If a chart contradicts a dashboard reading, that's the
finding. Write about it.



PRICE ACTION DISCIPLINE:
Every bar-level detail must come from the PRICE ACTION
section. Do NOT invent an intraday tick. Do NOT transcribe
bar by bar. Quote ONE number per beat.

SAFETY:
- No predictions about future prices.
- No price targets as advice. Your own trade's target is
  allowed.
- No buy/sell signals as recommendations.
- No invented numbers.
- Name only one ticker: {symbol}.
- Do NOT write OVX, COT, VIX, SPX, ES, NQ, NYMEX, CL.
- Do NOT reference the source block.
- Do NOT refer to the article itself.

{source_block}

End with this CTA:

{cta_text}"""


TEMPLATES = [_TEMPLATE_TRADE, _TEMPLATE_STOCK]
TEMPLATE_IDS = ["trade", "stock"]


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
