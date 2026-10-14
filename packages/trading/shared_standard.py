# config/shared_standard.py
#
# THE SHARED STANDARD
# ===================
# Every subblock imports this module. The rules below apply to
# every article, regardless of subblock.
#
# What is NOT here:
#   - The register (the voice). Per-subblock.
#   - The shape (the beat structure). Per-subblock.
#   - The source blocks. Per-subblock.
#
# What IS here:
#   - The floor. The rules no article can fall below.
"""THE SHARED STANDARD.

Every subblock imports this module. The seven components below
apply to every article, regardless of subblock. The voice, the
tense, the shape, the beat structure — those live in each
subblock's register. This is the floor.

Change a rule here once. Every subblock inherits it.
"""
from __future__ import annotations


# ---------------------------------------------------------------
# 1. EVIDENCE
# ---------------------------------------------------------------

EVIDENCE = """
EVIDENCE

Every NUMBER in the piece must appear in the source blocks
provided. Do NOT invent. Do NOT recycle a number from a prior
article.

Every factual sentence about the trader must trace to the source.
Do not invent a trade, a price, a win rate, a rule, or a blotter.

Every generalisation about behaviour must trace to the research
block. Name the source in the SAME sentence as the claim.

If the source names a trade, that trade is the ONLY trade you
may describe with numbers. Its date, instrument, size, hold
time, result, and exit reason are the numbers you may cite. Do
not invent another trade, another date, or another number. If
the source names no trade, do not describe one.
"""


# ---------------------------------------------------------------
# 2. CITATION FORMAT
# ---------------------------------------------------------------

CITATION_FORMAT = """
CITATION FORMAT

Cite sources as: Author YEAR, or Author and Author YEAR.

CORRECT:
  Kahneman and Tversky 1979
  Odean 1998
  Barber and Odean 2000
  Thaler 1980

WRONG:
  Kahneman & Tversky, 1979
  Odean (1998)
  (Barber and Odean, 2000)

Do NOT use an ampersand. Do NOT put a comma between the author
name and the year. One sentence, one claim, one source.
"""


# ---------------------------------------------------------------
# 3. LENGTH
# ---------------------------------------------------------------

LENGTH = """
LENGTH

The subblock's register states the target length. If the
register names no target, write 600-900 words and aim for 750.

The publish floor is 600. A draft under 600 words is a failed
draft. Do not return it.

The article's length must NOT depend on a cited trade. Length
comes from the mechanism, the diagnostics, and the fixes.

Do NOT pad the hook. Do NOT pad the CTA. Length comes from the
middle beats only.

Before returning:
  1. Count the words.
  2. If under 600, DO NOT RETURN YET.
  3. Rewrite the mechanism with more depth.
  4. Rewrite the fixes to name each change.
  5. Count again.
  6. Only return when the count is 600 or more.
"""


# ---------------------------------------------------------------
# 4. FORBIDDEN
# ---------------------------------------------------------------

FORBIDDEN = """
FORBIDDEN

Do not use:
  - textbook definitions of RSI, SMA, ATR, VIX, or any term the
    reader already knows
  - the phrases 'in this article', 'it is worth noting',
    'let us dive in', 'in conclusion'
  - invented statistics
  - forecasts
  - price targets
  - buy or sell advice
  - second person ('you', 'your', 'you should')
  - section headers
  - bold labels at line starts
  - numbered or bulleted lists (except the diagnostics, if the
    subblock requires one)
  - summary closes
  - questions as titles
"""


# ---------------------------------------------------------------
# 5. REFRAME RULE
# ---------------------------------------------------------------

REFRAME_RULE = """
REFRAME

One sentence. Standalone. Impersonal.

The reframe must correct a mistaken equivalence: the reader
believes X is Y; the article shows X is not Y, it is Z.

The reframe must make a distinction. NOT a definition. NOT a
restatement of the mechanism. NOT a platitude.

FORBIDDEN REFRAME SHAPES (used in previous articles — the
reader will recognize them):

  - "Discipline is not..." / "Discipline isn't..."
  - "The cooldown isn't..."
  - "The rule isn't..."
  - "The stop isn't..."
  - "X is not the absence of Y; it is Z"
  - "X isn't a [noun]. It's a [noun]."

Choose a shape that has not been used before.

The reframe must be new. If it repeats the shape of a prior
article, rewrite it.
"""


# ---------------------------------------------------------------
# 6. CTA RULE
# ---------------------------------------------------------------

CTA_RULE = """
CTA

One sentence. You write it. If the subblock's register supplies
a cta_text to use verbatim, use it. Otherwise, follow this rule.

The CTA must offer the reader a specific artifact — a thing
they could receive, named in the article's own words. The
artifact must fit THIS article's theme.

SHAPE (the words are yours; the shape is fixed):
  "I keep the [artifact] I now use every session — email me and
   I'll send it: https://signaliq.com"

The artifact may be a worksheet, a checklist, a log, a sheet,
a protocol, or a journal. Anything specific the reader can hold.

Hard rules:
  - MUST name a specific discipline artifact.
  - MUST end with https://signaliq.com and no trailing
    punctuation after it.
  - MUST NOT mention: free tier, subscription, upgrade, pricing,
    delisted names, market data, backtests, datasets, samples,
    or 'your current source'.
"""


# ---------------------------------------------------------------
# 7. PRE-RETURN CHECKS
# ---------------------------------------------------------------

PRE_RETURN_CHECKS = """
PRE-RETURN CHECKS

Before returning, verify each of the following. If any check
fails, rewrite the draft before returning.

1. LENGTH
   Count the words. The draft must be 600 words or more.

2. FIRST PERSON
   If the register requires first person, search the draft for
   the word 'you'. If it appears anywhere except inside a quoted
   source, rewrite that sentence in first person. If the register
   requires third person, this check does not apply.

3. TRADE TICKER
   If the source names a trade, its ticker must appear as a
   standalone token in the body. If the source names no trade,
   this check does not apply.

4. CITATIONS
   Every generalisation must name its source in the SAME
   sentence as the claim.

5. REFRAME
   The reframe must not use any of the forbidden shapes.

6. CTA
   The CTA must name a specific artifact. It must not mention
   the prohibited list. It must end with the URL. If the
   register supplies a verbatim cta_text, that takes priority.

7. NUMBERS
   Every number in the piece must trace to a source block.

If any check fails, rewrite the draft before returning.
"""


# ---------------------------------------------------------------
# THE ASSEMBLED STANDARD
# ---------------------------------------------------------------

STANDARD = "\n\n".join([
    EVIDENCE,
    CITATION_FORMAT,
    LENGTH,
    FORBIDDEN,
    REFRAME_RULE,
    CTA_RULE,
    PRE_RETURN_CHECKS,
])
