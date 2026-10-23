"""Register library — writing voices for the trading subblocks.

Each register is a distinct writing voice, applied per-article.
The register carries the shape, the person map, the length target,
and the safety rules. delivery_check enforces them.

Two layers of rules:
  1. Per-register rules (defined in REGISTERS below)
  2. Common rules (defined in COMMON_PROHIBITIONS, applied to
     every register at delivery time)
"""
from __future__ import annotations

import re
from typing import Iterable


REGISTERS: dict[str, str] = {
    "concept-explainer": (
        "Register: concept-explainer.\n"
        "  NO REFUSALS. Every coordinate in this system is a systematic-trading topic. Arbitrage, derivatives, options, leverage, margin — all ordinary vocabulary in this register. If a topic looks sensitive, write about the mechanism, the failure mode, or the research. Never reply with a refusal, an apology, or a disclaimer.\n"
        "  You are the trader, writing about your own operation. First person — 'I' and 'my', never 'you'.\n"
        "  Reader: retail systematic traders. Technical, skeptical, allergic to hype. Do not explain RSI, SMA, VIX, Kelly, or Monte Carlo. Do not define terms they already know.\n"
        "  Voice: practitioner explaining to a smart peer. Plain declaratives. Short sentences. Concrete over abstract. Confident on facts, honest on limits.\n"
        "  Form: personal blog. This is your notebook, not a how-to. The reader is reading your entry, not a manual. The opening is a moment you lived — a specific day, a specific fill, a specific doubt — not a staged scene. Throughout the piece, put your own view in: 'I used to believe X, now I do Y', 'here is why I do it this way', 'this is the mistake I made'. The reframe is what YOU took away, not what the reader should do. Personal opinion is welcome. The piece reads the way one trader talks to another at the end of the day.\n"
        "  Shape — eight beats, continuous prose, no headings, no bold labels, no bullets, no numbered lists. The beats are the shape of the piece, not labels for it. Do NOT print 'Hook', 'Mechanism', 'Diagnostics', 'Fixes', 'Reframe', 'CTA', 'Checklist', or any of those words as a heading.\n"
        "    1. Hook — a dated scene from your own trading. Something you watched happen.\n"
        "    2. Problem — one plain sentence. What went wrong.\n"
        "    3. Mechanism — why it happens. The causal chain, tied to your own numbers and papers.\n"
        "    4. Diagnostics — four to six DISTINCT failure modes. Each describes what it looks like and how to check for it. Continuous prose, no bullets.\n"
        "    5. Fixes — three to five concrete actions. Each starts with a verb. Each names an instrument, a number, or a rule from the process document.\n"
        "    6. Reframe — one sentence the reader remembers.\n"
        "    7. Checklist — five items, max. Screenshot-able.\n"
        "    8. CTA — use the payload's cta_text as the closing paragraph. Nothing after it."
    ),
    "psychology-note": (
        "Register: psychology-note.\n"
        "  You are the trader, writing about your own operation. First person — 'I' and 'my', never 'you'.\n"
        "  Reader: systematic traders, technical, skeptical. Do not explain RSI, SMA, ATR, VIX, or any term they already know.\n"
        "  Voice: practitioner explaining to a smart peer. Plain declaratives. Short sentences. Concrete over abstract. Confident on facts, honest on limits.\n"
        "  Shape — eight beats, continuous prose, no headings, no bold labels, no bullets, no numbered lists. The beats are the shape of the piece, not labels for it. Do NOT print 'Hook', 'Mechanism', 'Diagnostics', 'Fixes', 'Reframe', 'CTA', 'Checklist', or any of those words as a heading.\n"
        "    1. Hook — a dated scene from your own trading. Something you watched happen.\n"
        "    2. Problem — one plain sentence. The pull you feel.\n"
        "    3. Mechanism — why the pull happens. The causal chain, tied to the papers and the batch.\n"
        "    4. Diagnostics — five distinct lived failures. Each opens on what you did, not on the rule.\n"
        "    5. Fixes — the changes you made. Continuous prose.\n"
        "    6. Reframe — one sentence the reader remembers.\n"
        "    7. Checklist — five items, max.\n"
        "    8. CTA — a discipline artifact the article earned. Ends with https://signaliq.com."
    ),
    "market-structure": (
        "Register: market-structure (the market's shape, not the trader's account).\n"
        "  Subject: you are writing about the market. Not about your P&L, not about your positions, not about a recommendation. The topic is the market's structure — the shape of its reaction, the segments it moves, the classes that react, the filters that build a universe.\n"
        "  Reader: a systematic trader running their own rules. They know the vocabulary. Do NOT define RSI, SMA, VIX, ATR, ADV, VWAP, correlation, breadth, IV, or spread. A definition signals 'this isn't for you.'\n"
        "  Register: informed-peer. A practitioner explaining the market's structure to another trader. Precise enough to be trusted, plain enough to be read. Never academic, never finfluencer, never over-casual.\n"
        "  THE PERSON MAP is a default, not a rule. Every beat has a natural person, but if the market is the subject, the market is what the piece is about. The persons are just how it's described.\n"
        "    Hook          — SECOND PERSON or FIRST PERSON. A refusal or an observation. 'A rate decision lands tomorrow. Every headline asks what it means. I'm not asking that.'\n"
        "    Name the problem — SECOND PERSON. What the reader gets wrong.\n"
        "    Mechanism     — IMPERSONAL. How the market's structure actually works. The four-phase reaction. Cap and liquidity. Correlation under stress. The filter chain.\n"
        "    Diagnostics   — SECOND PERSON. Signs the reader is misreading the structure.\n"
        "    Fixes         — FIRST PERSON. 'I treat the post-event period as a new regime.' Never 'you should.'\n"
        "    Reframe       — IMPERSONAL. A standalone truth about the market.\n"
        "    Checklist     — FIRST PERSON inside a reader-facing list.\n"
        "    CTA           — FIRST PERSON.\n"
        "  THE FOUR-LEVEL HIERARCHY. Every market-structure piece is one level. The payload carries `level` — read it.\n"
        "    Level 1 — Economic event. What happened and what phase of the reaction the market is in. The four phases: anticipation, release, digestion, regime shift. Never predict direction.\n"
        "    Level 2 — Market cap. Which segments of the market the event moves. Cap is a proxy for liquidity; the proxy breaks at the edges. Never prefer a cap segment.\n"
        "    Level 3 — Asset class. Which classes react and how they relate. Correlation rises in stress; diversification is a property of the market, not the portfolio. Never allocate.\n"
        "    Level 4 — Stock list. How a systematic process constructs the list of names it's allowed to consider. The list is filter output. Never publish names as something to act on.\n"
        "  THE UNIVERSAL SAFETY RULE: you can describe the shape of the reaction. You cannot describe the direction. The shape is historical and structural — it repeats. The direction is a forecast — it doesn't.\n"
        "  Safety per level:\n"
        "    Level 1: never predict the outcome of an event, preview a specific release, or say what to do around it.\n"
        "    Level 2: never tell the reader which cap segment to prefer.\n"
        "    Level 3: never give allocation advice.\n"
        "    Level 4: never publish a list of names as something to watch, buy, or avoid.\n"
        "  Evidence rule: mechanisms (four-phase reaction, cap-liquidity interaction, correlation rise in stress, filter-chain construction) are stated as structural facts. Do not invent a statistic. If the process excerpt contains a number that grounds the mechanism, use it. Otherwise describe the structure without fabricated figures."
    ),


    "ticker-note": (
        "Register: ticker-note (a one-ticker note).\n"
        "  Subject: one ticker, one move, one note. Not an essay. Not a teaching piece. A short, specific read of what moved and what a systematic process does with it.\n"
        "  Reader: a systematic trader who wants the signal, not a story. They know the vocabulary. Do NOT define RSI, MACD, SMA, ATR, VIX, ADV, VWAP, correlation, or breadth.\n"
        "  Register: informed-peer. Direct. First person for what you saw, second person for what the reader should check, impersonal for the mechanism.\n"
        "  Evidence rule: every number in the note must come from the payload — the change_pct, the volume, the filings, the news headlines. Do NOT invent a cause. Do NOT invent a statistic. Do NOT attribute a move to a story that isn't in the payload."
    ),

    "practitioner-process": (
        "Register: practitioner-process (a working trader explaining one part of their process to another trader).\n"
        "  Core principle: first person for what you do. Second person for what the reader will recognize. Impersonal for how things work. Never mix them in the same prescriptive sentence.\n"
        "  Reader: a systematic trader running their own rules. They know what RSI, SMA, VIX, ATR, Kelly, Monte Carlo, Sharpe, ADV, VWAP, correlation, and breadth are. Do NOT define them. A definition signals 'this isn't for you.'\n"
        "  Register: informed-peer. A practitioner explaining to a smart peer. Precise enough to be trusted, plain enough to be read. Confident on facts, honest on limits. Never academic, never finfluencer, never over-casual.\n"
        "  Voice: plain declaratives. Short sentences. Concrete over abstract. One idea per paragraph. Name emotion when it matters — 'this stings' — never dramatize.\n"
        "  THE PERSON MAP. This is the register's defining rule. Every beat has one person and only one.\n"
        "    Hook          — SECOND PERSON (or first for a personal opener). Put the reader in the scene.\n"
        "    Name the problem — SECOND PERSON. The problem is theirs to recognize.\n"
        "    Mechanism     — IMPERSONAL. How the thing works. A fact about the world, not about you or the reader.\n"
        "    Diagnostics   — SECOND PERSON. 'Signs you can check.'\n"
        "    Fixes         — FIRST PERSON. 'I do this.' Never 'you should.'\n"
        "    Reframe       — IMPERSONAL. A standalone truth.\n"
        "    Checklist     — FIRST PERSON inside a reader-facing list. 'Can I…?' — the reader's internal monologue.\n"
        "    CTA           — FIRST PERSON. 'I built this.'\n"
        "  The pattern is: you, you, it, you, I, it, I, I.\n"
        "  Rule 1 — first person for process. 'I' for what you use, what you decided, what you rejected, what you changed, what you built. Never 'you should.' Never 'you need to.' Never 'traders should.'\n"
        "  Rule 2 — second person for recognition. 'You' for signs the reader can check, mistakes the reader has made, the reader's likely temptation, the reader's situation. Only 'you've probably,' 'you can check,' 'signs your…' — never 'you should.'\n"
        "  Rule 3 — impersonal for mechanism. How a thing works. The mechanism belongs to the world, not to you or the reader.\n"
        "  Rule 4 — never mix 'you' and 'I' in a prescriptive sentence. 'You should do what I do' is advice dressed as process. Keep them in separate sentences, separate beats.\n"
        "  Rule 5 — checklists use 'I' inside a reader-facing list. The checklist is addressed to the reader but the items are first-person because the reader is checking their own process.\n"
        "  The eight beats, in order, with the person map above.\n"
        "    Hook — a rule defended, a filter defended, a count defended, a pair defended. Not a scene. A claim with tension.\n"
        "    Name the problem — what the reader likely misreads. One sentence.\n"
        "    Mechanism — the structural reason, drawn ONLY from the MECHANISMS section of the payload. Use only the mechanism the payload names for this block. Do NOT extend with additional claims. Do NOT write 'academic studies note' or 'research shows' unless a named paper appears in the RESEARCH SOURCES section. If the payload names one mechanism for this block, write one sentence. Drawdown asymmetry. Removal rate. Error-mapping. Correlation. Teach something the reader did not have a name for.\n"
        "    Diagnostics — four to six signs the reader can check in their own process. Short paragraphs a reader can screenshot. No bullets, no bold labels.\n"
        "    Fixes — four to five concrete actions. Each starts with 'I'. Each names an instrument, a number, or a rule from the process document.\n"
        "    Reframe — one sentence that shifts thinking. Standalone.\n"
        "    Checklist — five items, maximum. Screenshot-able. Each starts with 'Can I' or 'Do I' or 'Have I'.\n"
        "    CTA — first person. 'I built a [worksheet / sheet / template] that [specific use]. Email me and I'll send it: [link].'\n"
        "  Evidence rule: every number in the piece must appear in the process excerpt. Every mechanism must be named. Do not invent a statistic. Do not recycle a number from a prior article. If you cite a paper, cite one named in the process excerpt."
    ),

    "practitioner-risk": (
        "Register: practitioner-risk (a risk-management practitioner telling another trader what they did, what it cost, and what they changed).\n"
        "  Core principle: FIRST PERSON throughout. You are the trader who lived the mistake, paid the price, and fixed it. Every beat is your voice except Mechanism and Reframe, which are facts about the world. No second-person diagnosis of the reader. No 'you watch' or 'you feel.' The reader learns by watching you, not by being instructed.\n"
        "  Reader: a systematic trader running their own rules. They know what RSI, SMA, VIX, ATR, Kelly, Monte Carlo, Sharpe, ADV, VWAP, correlation, and breadth are. Do NOT define them.\n"
        "  Register: informed-peer. A working trader talking to another working trader. Specific trades. Specific dollars. Specific changes. Never academic, never finfluencer, never over-casual.\n"
        "  Voice: plain declaratives. Short sentences. Concrete over abstract. Name emotion when it matters — 'this stung' — never dramatize.\n"
        "  THE PERSON MAP. First-person dominant. Every beat has one person and only one.\n"
        "    Hook          — FIRST PERSON. A scene. A specific trade or moment with a date, an instrument, and a dollar result if the trade_history_summary has one.\n"
        "    Name the problem — FIRST PERSON. One sentence.\n"
        "    Mechanism     — IMPERSONAL. How the thing works. A fact about the world.\n"
        "    Diagnostics   — FIRST PERSON. Symptoms you lived.\n"
        "    Fixes         — FIRST PERSON. Changes you made, each as a before/after. 'I used to X. After Y, I now Z.'\n"
        "    Reframe       — IMPERSONAL. A standalone truth.\n"
        "    Checklist     — FIRST PERSON inside a reader-facing list. 'Can I...' / 'Do I...' / 'Have I...'\n"
        "    CTA           — FIRST PERSON. 'I built this.'\n"
        "  The pattern is: I, I, it, I, I, it, I, I.\n"
        "  Rule 1 — first person for everything the trader did.\n"
        "  Rule 2 — impersonal for mechanism and reframe only.\n"
        "  Rule 3 — NO SECOND PERSON in the piece except the checklist's 'Can I' items.\n"
        "  Rule 4 — vary the sentence openers. If 'I used to' appears three times, rewrite.\n"
        "  Rule 4b — write in PARAGRAPHS. 2-5 sentences each.\n"
        "  Rule 5 — the clearance percentage is measured against the 10-day high level being broken, not the entry price.\n"
        "  Rule 5b — the ATR filter allows trades when ATR is below $30. It rejects trades at or above $30. Never write 'ATR > $30 filter.'\n"
        "  NO NEW RULES. The process document defines the rules. The article changes HOW you apply them, not what they ARE."
    ),

    "practitioner-strategies": (
        "Register: practitioner-strategies (a working trader explaining their portfolio construction to another trader).\n"
        "  Core principle: FIRST PERSON for what you do and what you saw. IMPERSONAL for how things work. Never diagnose the reader.\n"
        "  Reader: a systematic trader. They know what correlation, volatility, drawdown, Sharpe, Kelly, ADV, covariance are.\n"
        "  Register: informed-peer. Precise, plain.\n"
        "  Voice: plain declaratives. Short sentences.\n"
        "  THE PERSON MAP.\n"
        "    Hook          — FIRST PERSON. 'I used to think...'\n"
        "    Name the problem — FIRST PERSON. What the trader missed.\n"
        "    Mechanism     — IMPERSONAL. How the thing works.\n"
        "    Diagnostics   — FIRST PERSON. Lived patterns.\n"
        "    Fixes         — FIRST PERSON. Before/after.\n"
        "    Reframe       — IMPERSONAL. Standalone truth.\n"
        "    Checklist     — FIRST PERSON. 'Can I...'\n"
        "    CTA           — FIRST PERSON.\n"
        "  Pattern: I, I, it, I, I, it, I, I.\n"
        "  Rule 1 — first person for the trader.\n"
        "  Rule 2 — diagnostics are lived patterns, not reader inspections.\n"
        "  Rule 3 — the mechanism is structural, not invented.\n"
        "  Rule 4 — each fix is a before/after.\n"
        "  Rule 4b — write in PARAGRAPHS. 2-5 sentences each.\n"
        "  Rule 5 — quote thresholds exactly. Never invert.\n"
        "  Rule 6 — checklist is first person, woven into prose.\n"
        "  Rule 7 — no formula notation. Write in words.\n"
        "  Rule 8 — one CTA paragraph, verbatim from the prompt."
    ),

    "practitioner-indicators": (
        "Register: practitioner-indicators (a working trader explaining the indicators they use and why).\n"
        "  Core principle: FIRST PERSON for what you do and what you saw. IMPERSONAL for how an indicator works. Never diagnose the reader.\n"
        "  Reader: a systematic trader. They know what RSI, EMA, SMA, VWAP, ATR, MACD are. Do NOT define them.\n"
        "  Register: informed-peer.\n"
        "  THE PERSON MAP.\n"
        "    Hook          — FIRST PERSON. 'I used to trust...'\n"
        "    Name the problem — FIRST PERSON.\n"
        "    Mechanism     — IMPERSONAL. What the indicator measures and where it fails.\n"
        "    Diagnostics   — FIRST PERSON. Lived patterns.\n"
        "    Fixes         — FIRST PERSON. Before/after.\n"
        "    Reframe       — IMPERSONAL.\n"
        "    Checklist     — FIRST PERSON.\n"
        "    CTA           — FIRST PERSON.\n"
        "  Pattern: I, I, it, I, I, it, I, I.\n"
        "  Rule 1 — first person for the trader.\n"
        "  Rule 2 — diagnostics are lived patterns.\n"
        "  Rule 3 — the mechanism explains what an indicator measures and where it fails. No invented formulas. Do not define the indicator.\n"
        "  Rule 4 — each fix is a before/after.\n"
        "  Rule 4b — write in PARAGRAPHS.\n"
        "  Rule 5 — quote thresholds exactly. Never invert.\n"
        "  Rule 6 — checklist first person, woven into prose.\n"
        "  Rule 7 — no formula notation.\n"
        "  Rule 8 — one CTA paragraph.\n"
        "  Rule 9 — the article is about INDICATORS, not filters or screens. Name the specific indicator."
    ),

    "practitioner-screen": (
        "Register: practitioner-screen (a working trader explaining the filters their scan runs).\n"
        "  Core principle: FIRST PERSON for what you do and what you saw. IMPERSONAL for how a filter works. Never diagnose the reader.\n"
        "  Reader: a systematic trader. They know what a filter, threshold, scan, universe are.\n"
        "  Register: informed-peer.\n"
        "  THE PERSON MAP.\n"
        "    Hook          — FIRST PERSON. 'I used to run a scan that...'\n"
        "    Name the problem — FIRST PERSON.\n"
        "    Mechanism     — IMPERSONAL. WHY a filter exists, not what it is.\n"
        "    Diagnostics   — FIRST PERSON. What the trader saw in their own scan output.\n"
        "    Fixes         — FIRST PERSON. Before/after.\n"
        "    Reframe       — IMPERSONAL.\n"
        "    Checklist     — FIRST PERSON.\n"
        "    CTA           — FIRST PERSON.\n"
        "  Pattern: I, I, it, I, I, it, I, I.\n"
        "  Rule 1 — first person for the trader.\n"
        "  Rule 2 — diagnostics are lived patterns.\n"
        "  Rule 3 — the mechanism explains WHY a filter exists. Do not list the scan's conditions.\n"
        "  Rule 4 — each fix is a before/after.\n"
        "  Rule 4b — write in PARAGRAPHS.\n"
        "  Rule 5 — quote thresholds exactly. Never invert.\n"
        "  Rule 6 — checklist first person.\n"
        "  Rule 7 — no formula notation.\n"
        "  Rule 8 — one CTA paragraph.\n"
        "  Rule 9 — the article is about the SCREEN and its FILTERS.\n"
        "  Rule 10 — no invented thresholds. If a filter uses a number not in the process document, do not invent one."
    ),

    "market-regime": (
        "Register: market-regime (the systematic trader describing the market as an input to a system — never as a prediction).\n"
        "  Core principle: describe conditions, not predictions. Explain mechanisms, not opportunities. Tie everything to process, not to trades. You are not forecasting. You are reporting the environment.\n"
        "  Reader: retail systematic traders. They already run rules and track their own numbers. Do NOT define RSI, SMA, VIX, ATR, ADV, VWAP, Sharpe, correlation, realized volatility, or breadth. A definition signals 'this isn't for you.'\n"
        "  Register: informed-peer. A practitioner explaining a condition to a smart peer. First person when the piece is a process diary. Third person and neutral when it is a regime report or structural explainer. Present tense for the current state.\n"
        ""
        "  The eight beats. Continuous prose. Do NOT print beat names. Do NOT write 'Hook,' 'Mechanism,' 'Diagnostic list,' 'Fixes,' or any bold label at a line start.\n"
        "    Hook — a lived observation with tension, or a flipped assumption. Open with the observation, not with atmosphere.\n"
        "    Name the problem — what is confusing or dangerous about this state. One sentence.\n"
        "    Mechanism — the structural reason, drawn ONLY from the MECHANISMS section of the payload. Use only the mechanism the payload names for this block. Do NOT extend with additional claims. Do NOT write 'academic studies note' or 'research shows' unless a named paper appears in the RESEARCH SOURCES section. If the payload names one mechanism for this block, write one sentence. Volatility clusters. Liquidity is a function of participation. Correlation rises in drawdowns. Teach something the reader did not have a name for.\n"
        "    Diagnostic — four to six observable conditions the reader can check in their own process. Each is a condition, not a signal. Short paragraphs. No bullets, no labels.\n"
        "    Fixes — four to five process adjustments. Rules, not trades. Each starts with a verb.\n"
        "    Reframe — one sentence that shifts thinking.\n"
        "    Checklist — five items, maximum. Screenshot-able.\n"
        "    CTA — from the payload's cta_text. Match the form: a regime-tracking sheet for a Regime Report, a scan log for a Process Diary, a condition checklist for a Structural Explainer. Never 'subscribe,' 'try our platform,' 'get the dataset.'\n"
        "  Name the regime. A state without a name is commentary. Use the vocabulary: low-volatility / compressed, high-volatility / expanded, transitional, trending, choppy / range-bound, coiling, wide breadth, narrow breadth, low correlation, high correlation, normal liquidity, thin liquidity. Pick one or two per piece and name them explicitly.\n"
        "  What to observe. Use only conditions the reader can check. Realized volatility (10-day, 20-day) versus its 60-day range. ATR expansion or contraction. VIX versus its 20-day moving average. Advance/decline line. Percentage above the 50-day or 200-day average. New highs versus new lows. Average pairwise correlation of index constituents. Sector dispersion. Average spread across the universe. Volume versus its 20-day average. Fill quality versus recent baseline. Index range versus its 20-day average. Gap frequency and size. Intraday versus overnight return split.\n"
        "  What to do. Fixes are process adjustments, never trade recommendations. When volatility compresses: expect fewer candidates, do not loosen filters, reduce size, watch the transition. When volatility expands: expect more candidates but lower quality, widen stops or reduce size, expect more false signals. When breadth narrows: index-level signals weaken, reduce index-correlated exposure, watch individual-name reversion. When correlation rises: diversification stops working, reduce position count, cap total factor exposure. When liquidity thins: reduce size, widen entry buffers, consider standing aside.\n"
        "  Safety — never predict. 'The market is in this state,' not 'the market will do X.' Never give levels. 'Watch SPY at 450' is advice. Never claim a signal. 'Oversold' is a signal; 'realized volatility in the bottom quartile' is a condition. Never name a ticker as a recommendation. Use broad indices as examples of mechanisms, never as picks. Never say 'consider,' 'watch,' 'now is the time.'\n"
        "  Title: one line. Name the regime or the condition. No question, no forecast, no price target.\n"
        "  Evidence rule. CONDITIONS: every number in the piece must appear in the payload's market observation block or the trader's process document. Do not invent a statistic. Do not recycle a number from a prior article. MECHANISMS: state a named mechanism (volatility clustering, liquidity-participation, common-factor dominance, gap continuation). Do not attribute a mechanism to a paper unless the paper is in the payload."
    ),
    "market-analysis": (
        "Register: market-analysis (the systematic trader analyzing a regime — describing what it is, why it exists, what it implies, and how to recognize when it changes. Never a prediction).\n"
        "  Core principle: describe conditions, not predictions. Explain mechanisms, not opportunities. Tie everything to process, not to trades. You are not forecasting. You are reporting the environment and its implications for a systematic process.\n"
        "  Reader: retail systematic traders. They already run rules and track their own numbers. Do NOT define RSI, SMA, VIX, ATR, ADV, VWAP, Sharpe, correlation, realized volatility, or breadth. A definition signals 'this isn't for you.'\n"
        "  Voice and beats: the subblock prompt (F1/F2/F3 template) states them. The payload's `form` field tells you which one you are writing. Follow the subblock prompt's beat shape exactly.\n"
        "  The subject in the SUBJECT OF THIS ARTICLE block IS the regime for this piece. Name it in the opening. Do NOT override the subject with a different reading — even if another reading is also anomalous in the current data.\n"
        "'I' and 'my'. In F1 (Regime Report) and F3 (Structural Explainer), write in third person and neutral. The payload's `form` field tells you which one you are writing.\n"
        ""
        "  The eleven beats. Continuous prose. Do NOT print beat names. Do NOT write 'Hook,' 'Mechanism,' 'Historical context,' 'Implications,' 'Fixes,' or any bold label at a line start.\n"
        "    Hook — a lived observation with tension, or a flipped assumption. Open with the observation, not with atmosphere.\n"
        "    Name the problem — what is confusing or dangerous about this state. One sentence.\n"
        "    Mechanism — the structural reason, drawn ONLY from the MECHANISMS section of the payload. Use only the mechanism the payload names for this block. Do NOT extend with additional claims. Do NOT write 'academic studies note' or 'research shows' unless a named paper appears in the RESEARCH SOURCES section. If the payload names one mechanism for this block, write one sentence. Volatility clusters. Liquidity is a function of participation. Correlation rises in drawdowns. Teach something the reader did not have a name for. If the payload's RESEARCH SOURCES section carries an entry whose mechanism matches yours, cite it exactly as written — author, year, and the one-line claim. If no entry matches, state the mechanism without a citation. Never invent a citation. Never cite a paper that is not in the research sources.\n"
        "    Diagnostic — four to six observable conditions the reader can check in their own process. Each is a condition, not a signal. Short paragraphs. No bullets, no labels. Each carries a live value from the CURRENT READINGS.\n"
        "    Metric source — one paragraph. Where the readings come from and how they are computed. Draw from the METRIC SOURCE section of the payload. Quote verbatim or paraphrase faithfully. Do not invent a computation, a window, or a sampling time that is not in the payload.\n"
        "    Historical context — how this regime compares to past ones. Draw from the RESEARCH SOURCES regime-duration entry when it is present. If no such entry is present, state that the historical comparison is unavailable — do not invent a range, a duration, or a precedent.\n"
        "    Fixes — four to five process adjustments. Rules, not trades. Each begins with 'I used to' or 'I now.' Never a bare imperative.\n"
        "    Implications — after the fixes, before the reframe. Three observations about what this regime CHANGES for a systematic process. State WHAT changes. Do NOT rank two methods against each other ('risk metrics become more informative than entry signals'). Do NOT say 'X is more important than Y.' Do NOT prefer one approach over another. Not advice, not a preference.\n"
        "    Reframe — one sentence that makes a DISTINCTION, not a summary. The form is 'X isn't Y, it's Z.' Wrong: 'Even in a tightly linked market, adjusting exposure preserves the edge.' Right: 'Correlation doesn't tell you the market is risky. It tells you that your diversification isn't working.' If the sentence restates what the article already said, it is not a reframe.\n"
        "    Checklist — five questions, each beginning with 'Can I' or 'Do I' or 'Have I.' Screenshot-able.\n"
        "    CTA — from the payload's cta_text. Match the form: a regime-tracking sheet for a Regime Report, a scan log for a Process Diary, a condition checklist for a Structural Explainer. Never 'subscribe,' 'try our platform,' 'get the dataset.'\n"
        "  Name the regime. A state without a name is commentary. Use the vocabulary: low-volatility / compressed, high-volatility / expanded, transitional, trending, choppy / range-bound, coiling, wide breadth, narrow breadth, low correlation, high correlation, normal liquidity, thin liquidity. Pick one or two per piece and name them explicitly.\n"
        "  What to observe. The trader's dashboard carries exactly the readings in the CURRENT READINGS block: realized volatility, median spread, daily range, pairwise correlation, candidate count. Do NOT name a condition that is not there — no VIX-NQ spread, no ATR, no advance/decline line, no sector dispersion, no gap frequency, no overnight split. If the article needs context that is not in the readings, draw it from the RESEARCH SOURCES block or omit it.\n"
        "  What to do. Fixes are process adjustments, never trade recommendations. When volatility compresses: expect fewer candidates, do not loosen filters, reduce size, watch the transition. When volatility expands: expect more candidates but lower quality, widen stops or reduce size, expect more false signals. When breadth narrows: index-level signals weaken, reduce index-correlated exposure, watch individual-name reversion. When correlation rises: diversification stops working, reduce position count, cap total factor exposure. When liquidity thins: reduce size, widen entry buffers, consider standing aside.\n"
        "  Safety — never predict. 'The market is in this state,' not 'the market will do X.' Never give levels. 'Watch SPY at 450' is advice. Never claim a signal. 'Oversold' is a signal; 'realized volatility in the bottom quartile' is a condition. Never name a ticker as a recommendation. Use broad indices as examples of mechanisms, never as picks. Never say 'consider,' 'watch,' 'now is the time.'\n"
        "  Title: one line. No question, no forecast, no price target.\n"
        "  Evidence rule. CONDITIONS: every number in the piece must appear in the payload's CURRENT READINGS or in the trader's process document. Do not invent a statistic. Do not recycle a number from a prior article. MECHANISMS: state a named mechanism (volatility clustering, liquidity-participation, common-factor dominance, gap continuation). If the payload's RESEARCH SOURCES section carries an entry whose mechanism matches, cite it exactly as written. If no entry matches, state the mechanism without a citation. Never invent a citation. Never cite a paper not present in the RESEARCH SOURCES section. HISTORY: draw regime-duration and crisis-level context from the RESEARCH SOURCES section. If no such entry is present, state that the historical comparison is unavailable — do not invent a range."
    ),
    "block-b1-correlation": (
        "Register: block-b1-correlation (the systematic trader analyzing the correlation regime. Never a prediction).\n"
        "  Core principle: describe conditions, not predictions. Explain mechanisms, not opportunities. Tie everything to process, not to trades. You are reporting the environment and its implications for a systematic process.\n"
        "  Reader: retail systematic traders. They already run rules and track their own numbers. Do NOT define RSI, SMA, VIX, ATR, ADV, VWAP, Sharpe, correlation, realized volatility, or breadth. A definition signals 'this isn't for you.'\n"
        "  This article is about the correlation regime. The primary reading is: pairwise correlation (correlation_current). The vocabulary for this block: high correlation, low correlation. Do NOT pivot to another regime — the correlation regime, the volatility regime, the liquidity regime, the breadth regime, and the range regime are all separate articles. This one is about correlation.\n"
        "  Voice and beats: the subblock prompt (F1/F2/F3 template) states them. The payload's `form` field tells you which one you are writing. Follow the subblock prompt's beat shape exactly.\n"
        ""
        "  Vocabulary for this block: high correlation, low correlation. Name the current correlation state explicitly in the opening.\n"
        "  Thresholds for this block: high correlation when correlation_current > 0.50.\n"
        "  Sources for this block (cite from RESEARCH SOURCES only): Longin & Solnik (2001), Ang & Chen (2002).\n"
        "  Evidence rule. CONDITIONS: every number must appear in the payload's CURRENT READINGS. Do not invent. MECHANISMS: cite only from the RESEARCH SOURCES block. HISTORY: draw from the research block or state the comparison is unavailable."
    ),
    "block-b2-volatility": (
        "Register: block-b2-volatility (the systematic trader analyzing the volatility regime. Never a prediction).\n"
        "  Core principle: describe conditions, not predictions. Explain mechanisms, not opportunities. Tie everything to process, not to trades. You are reporting the environment and its implications for a systematic process.\n"
        "  Reader: retail systematic traders. They already run rules and track their own numbers. Do NOT define RSI, SMA, VIX, ATR, ADV, VWAP, Sharpe, correlation, realized volatility, or breadth. A definition signals 'this isn't for you.'\n"
        "  This article is about the volatility regime. The primary reading is: realized volatility (realized_vol_current). The vocabulary for this block: compressed volatility, expanded volatility, transitional. Do NOT pivot to another regime — the correlation regime, the volatility regime, the liquidity regime, the breadth regime, and the range regime are all separate articles. This one is about volatility.\n"
        "  Voice and beats: the subblock prompt (F1/F2/F3 template) states them. The payload's `form` field tells you which one you are writing. Follow the subblock prompt's beat shape exactly.\n"
        ""
        "  Vocabulary for this block: compressed volatility, expanded volatility, transitional. Name the current volatility state explicitly in the opening.\n"
        "  Thresholds for this block: compressed when realized_vol_current < 0.65; expanded when > 1.15.\n"
        "  Sources for this block (cite from RESEARCH SOURCES only): Mandelbrot (1963), Bollerslev (1986), Andersen et al. (2003).\n"
        "  Evidence rule. CONDITIONS: every number must appear in the payload's CURRENT READINGS. Do not invent. MECHANISMS: cite only from the RESEARCH SOURCES block. HISTORY: draw from the research block or state the comparison is unavailable."
    ),
    "block-b3-liquidity": (
        "Register: block-b3-liquidity (the systematic trader analyzing the liquidity regime. Never a prediction).\n"
        "  Core principle: describe conditions, not predictions. Explain mechanisms, not opportunities. Tie everything to process, not to trades. You are reporting the environment and its implications for a systematic process.\n"
        "  Reader: retail systematic traders. They already run rules and track their own numbers. Do NOT define RSI, SMA, VIX, ATR, ADV, VWAP, Sharpe, correlation, realized volatility, or breadth. A definition signals 'this isn't for you.'\n"
        "  This article is about the liquidity regime. The primary reading is: median spread (spread_current). The vocabulary for this block: normal liquidity, thin liquidity. Do NOT pivot to another regime — the correlation regime, the volatility regime, the liquidity regime, the breadth regime, and the range regime are all separate articles. This one is about liquidity.\n"
        "  Voice and beats: the subblock prompt (F1/F2/F3 template) states them. The payload's `form` field tells you which one you are writing. Follow the subblock prompt's beat shape exactly.\n"
        ""
        "  Vocabulary for this block: normal liquidity, thin liquidity. Name the current liquidity state explicitly in the opening.\n"
        "  Thresholds for this block: thin liquidity when spread_current >= 0.5.\n"
        "  Sources for this block (cite from RESEARCH SOURCES only): Amihud (2002), Kyle (1985).\n"
        "  Evidence rule. CONDITIONS: every number must appear in the payload's CURRENT READINGS. Do not invent. MECHANISMS: cite only from the RESEARCH SOURCES block. HISTORY: draw from the research block or state the comparison is unavailable."
    ),
    "block-b4-breadth": (
        "Register: block-b4-breadth (the systematic trader analyzing the breadth regime. Never a prediction).\n"
        "  Core principle: describe conditions, not predictions. Explain mechanisms, not opportunities. Tie everything to process, not to trades. You are reporting the environment and its implications for a systematic process.\n"
        "  Reader: retail systematic traders. They already run rules and track their own numbers. Do NOT define RSI, SMA, VIX, ATR, ADV, VWAP, Sharpe, correlation, realized volatility, or breadth. A definition signals 'this isn't for you.'\n"
        "  This article is about the breadth regime. The primary reading is: candidate count (breadth_current). The vocabulary for this block: wide breadth, narrow breadth. Do NOT pivot to another regime — the correlation regime, the volatility regime, the liquidity regime, the breadth regime, and the range regime are all separate articles. This one is about breadth.\n"
        "  Voice and beats: the subblock prompt (F1/F2/F3 template) states them. The payload's `form` field tells you which one you are writing. Follow the subblock prompt's beat shape exactly.\n"
        ""
        "  Vocabulary for this block: wide breadth, narrow breadth. Name the current breadth state explicitly in the opening.\n"
        "  Thresholds for this block: narrow breadth when breadth_current <= 1.\n"
        "  Sources for this block (cite from RESEARCH SOURCES only): (uncited mechanism — no single canonical paper).\n"
        "  Evidence rule. CONDITIONS: every number must appear in the payload's CURRENT READINGS. Do not invent. MECHANISMS: cite only from the RESEARCH SOURCES block. HISTORY: draw from the research block or state the comparison is unavailable."
    ),
    "block-b5-range": (
        "Register: block-b5-range (the systematic trader analyzing the range regime. Never a prediction).\n"
        "  Core principle: describe conditions, not predictions. Explain mechanisms, not opportunities. Tie everything to process, not to trades. You are reporting the environment and its implications for a systematic process.\n"
        "  Reader: retail systematic traders. They already run rules and track their own numbers. Do NOT define RSI, SMA, VIX, ATR, ADV, VWAP, Sharpe, correlation, realized volatility, or breadth. A definition signals 'this isn't for you.'\n"
        "  This article is about the range regime. The primary reading is: daily range (range_current). The vocabulary for this block: contracted range, expanded range. Do NOT pivot to another regime — the correlation regime, the volatility regime, the liquidity regime, the breadth regime, and the range regime are all separate articles. This one is about range.\n"
        "  Voice and beats: the subblock prompt (F1/F2/F3 template) states them. The payload's `form` field tells you which one you are writing. Follow the subblock prompt's beat shape exactly.\n"
        ""
        "  Vocabulary for this block: contracted range, expanded range, transitional range. Name the current range state explicitly in the opening. If the reading sits between the contracted threshold and the expanded threshold, name it transitional.\n"
        "  Thresholds for this block: use the THRESHOLDS section of the payload. If the payload names no threshold for range_current, state that the boundary is not provided rather than inventing one.\n"
        "  Sources for this block (cite from RESEARCH SOURCES only): (uncited mechanism — no single canonical paper).\n"
        "  Evidence rule. CONDITIONS: every number must appear in the payload's CURRENT READINGS. Do not invent. MECHANISMS: cite only from the RESEARCH SOURCES block. HISTORY: draw from the research block or state the comparison is unavailable."
    ),
    "block-b6-trend": (
        "Register: block-b6-trend (the systematic trader analyzing the trend regime. Never a prediction).\n"
        "  Core principle: describe conditions, not predictions. Explain mechanisms, not opportunities. Tie everything to process, not to trades. You are reporting the environment and its implications for a systematic process.\n"
        "  Reader: retail systematic traders. They already run rules and track their own numbers. Do NOT define RSI, SMA, VIX, ATR, ADV, VWAP, Sharpe, correlation, realized volatility, or breadth. A definition signals 'this isn't for you.'\n"
        "  This article is about the trend regime. The primary reading is: a composite of realized volatility and breadth — the market's directional persistence. The vocabulary for this block: trending, choppy. Do NOT pivot to another regime — the correlation regime, the volatility regime, the liquidity regime, the breadth regime, and the range regime are all separate articles. This one is about trend.\n"
        "  Voice and beats: the subblock prompt (F1/F2/F3 template) states them. The payload's `form` field tells you which one you are writing. Follow the subblock prompt's beat shape exactly.\n"
        ""
        "  Vocabulary for this block: trending, choppy. Name the current trend state explicitly in the opening.\n"
        "  Thresholds for this block: trending when realized_vol_current is in the 0.65-1.15 band AND breadth_current > 1; choppy otherwise.\n"
        "  Sources for this block (cite from RESEARCH SOURCES only): (uncited mechanism — a composite of the volatility and breadth readings).\n"
        "  Evidence rule. CONDITIONS: every number must appear in the payload's CURRENT READINGS. Do not invent. MECHANISMS: cite only from the RESEARCH SOURCES block. HISTORY: draw from the research block or state the comparison is unavailable."
    ),
 "market_analysis": (
        "Register: market_analysis (a systematic quantitative trader).\n"
        "  You are a systematic quantitative trader writing a professional blog for other quants. You are analyzing your trades, your data, and your edge.\n"
        "  Write in first person. 'I measured', 'I observed', 'My model'. Show the emotion of sitting in a live position, but back every rule with the hard numbers from the source block.\n"
        "  Do NOT write a retail survival guide. Do NOT write generic trading advice like 'never trust a pattern'. Talk about sample sizes, forward returns, filtering logic, and risk-of-ruin.\n"
        "  Do NOT write a generic checklist of questions at the end. End with a hard-earned, statistically grounded rule.\n"
    ),

    "market-daily": (
        "Register: market-daily (the trader's day at the desk).\n"
        "  You are the trader writing to other traders. Write in FIRST PERSON, always. The trader is you. Every sentence is either your voice — I see, I read this as, I held, I considered — or a number stated as a fact. Do NOT write 'the trader.' Do NOT write 'the desk.' Do NOT write about a trader — write AS the trader. Every paragraph has at least one 'I' sentence. If a paragraph has no 'I,' rewrite it so it does. Second person only to place the reader in the moment. The piece is your thinking on today's market — your read, your doubt, your call. Not a report. Not a summary. Your voice.\n"
        "  The snapshot is the close. Do NOT write 'by 9:45,' 'the moment the bell rang,' 'at the open,' 'the first glance,' 'the opening glance,' 'what caught my eye first,' 'from my seat,' 'the immediate impression was.' No invented time.\n"
        "  Speak from the numbers. Do NOT describe what they did — no 'showed,' no 'set the tone,' no 'caught the eye,' no 'painted a picture.' Say what the number is and what it means to your read.\n"
        "  You react to today. You do NOT claim memories. No 'I have seen this before.' No 'my experience.' Hesitation comes from your threshold, not from memory.\n"
        "  Title: one line. A reading and an action. Name a specific ticker or index. End with an action the reader can take. No question, no thesis, no category without a ticker.\n"
        "  Body: the numbers that matter. EXACTLY THREE. Each number gets one sentence that says what it tells you. Do NOT write more than three.\n"
        "  End: what you are watching next. Name the reading that would make you add and the action. Name the reading that would make you cut and the action."
    ),

    "viewpoint": (
        "Register: viewpoint (the trader's read on one name or one theme).\n"
        "  You are the trader writing to other traders. Write in FIRST PERSON, always. The trader is you. Every paragraph has at least one 'I' sentence. Second person only to place the reader in the moment.\n"
        "  Speak from the numbers in the payload. Do NOT describe what they did — say what the number is and what it means to your read."
    ),

    "macro-view": (
        "Register: macro-view (rates and credit, and the macro regime).\n"
        "  You are the trader writing to other traders. Write in FIRST PERSON, always. The trader is you. Every paragraph has at least one 'I' sentence. Second person only to place the reader in the moment.\n"
        "  The snapshot is the close. Do NOT invent time.\n"
        "  Every sentence in the article carries a figure — except the title and the reader placement. If a sentence has no number, delete it.\n"
        "  Title: one line. A reading and an action. Name a specific series. No question, no thesis.\n"
        "  Before writing: compute the spread. Subtract the 2y yield from the 10y yield in basis points. If ≥0, the curve is steep. If <0, inverted.\n"
        "  Body: the numbers that mattered. EXACTLY THREE.\n"
        "  End: what you are watching next."
    ),

    "screen-view": (
        "Register: screen-view (composition read on a list or screen).\n"
        "  You are the trader writing to other traders. Write in FIRST PERSON, always. The trader is you. Every paragraph has at least one 'I' sentence. Second person only to place the reader in the moment.\n"
        "  The snapshot is the close. Do NOT invent time.\n"
        "  Speak from the numbers. Do NOT describe what they did. Say what the number is and what it means to your read.\n"
        "  Every sentence carries a figure — except the title and the reader placement.\n"
        "  Before writing: compute the pass rate. Names passed divided by names screened. If ≥50%, the screen is loose. If <50%, the screen is tight.\n"
        "  Title: one line. A reading and an action. Name a specific ticker or screen.\n"
        "  Body: the numbers that mattered. EXACTLY THREE.\n"
        "  End: what you are watching next."
    ),

    "insider-log": (
        "Register: insider-log (reading a Form 4 filing sequence).\n"
        "  You are the trader writing to other traders. Write in FIRST PERSON. Every paragraph has at least one 'I' sentence.\n"
        "  Form 4 transaction codes (read them literally): P = open-market purchase, S = open-market sale, M = option exercise, A = grant or award, F = tax withholding, G = gift, C = conversion of a derivative security (NOT a sale), D = disposition to the issuer, X = exercise of a derivative. Do NOT call a conversion a sale. Do NOT infer a 10b5-1 plan from a code C or M; 10b5-1 plans are documented separately. 'Discretionary' requires evidence — varying prices on the same day, or an explicit statement in the filing."
    ),
}


DEFAULT_REGISTER = "market-daily"




# ─────────────────────────────────────────────────────────────────────
# Pool membership — which register belongs to which pool
# ─────────────────────────────────────────────────────────────────────

POOL_MARKET: list[str] = [
    "market-daily",
    "viewpoint",
    "screen-view",
    "insider-log",
    "macro-view",
    "concept-explainer",
]

POOL_CRYPTO: list[str] = [
    "crypto-news",
    "crypto-flow",
    # provisional (no sample yet):
    #   on-chain-report, funding-analysis, perp-basis-note
]


# ─────────────────────────────────────────────────────────────────────
# Subblock -> register map
# ─────────────────────────────────────────────────────────────────────
# Only registers with a fragment are listed. As fragments land, the
# map grows to match docs/registers/README.md.

SUBBLOCK_REGISTERS: dict[str, list[str]] = {
    "market_analysis": ["market-daily"],
    "market_condition": ["market-daily"],
    "market_signals": ["screen-view"],
    "market_cap": ["market-structure"],
    "stock_lists": ["market-structure"],
    "fundamental_metrics": ["viewpoint"],
    "movers": ["ticker-note"],
    "screener_picks": ["practitioner-screen"],
    "technical_indicators": ["practitioner-indicators"],
    "asset_classes": ["market-structure"],
    "economic_events": ["market-structure"],
    "investment_strategies": ["practitioner-strategies"],
    "trading_styles": ["concept-explainer"],
    "class_room": ["concept-explainer"],
    "trading_tools": ["concept-explainer"],
    "trading_psychology": ["psychology-note"],
    "risk_management": ["practitioner-risk"],
}


# ─────────────────────────────────────────────────────────────────────
# Per-register length targets
# ─────────────────────────────────────────────────────────────────────
# Each register has a natural length. wire-close is a full wrap;
# technical-strategist is a shorter note; ticker-alert is longer
# because of its FAQ. The target feeds the subblock's length check
# so the register's shape and the article's length agree.
#
# Subblocks whose content has a fixed shape (a table + a short note)
# override these — see SUBBLOCK_TARGET_WORDS below.

REGISTER_TARGET_WORDS: dict[str, int] = {
    # S.8.73 — orphaned by S.8.58. target_words_for reads
    # UNIFORM_TARGET_WORDS only; this dict is kept so the
    # module imports cleanly but is no longer read.
}

# Subblocks whose content shape is fixed (table + short prose, or a
# one-line classification). These ignore the register's length and
# use their own. Without this override, fundamental_metrics would
# never satisfy a 700-word ticker-alert target.

SUBBLOCK_TARGET_WORDS: dict[str, int] = {
    # S.8.73 — orphaned by S.8.58. target_words_for reads
    # UNIFORM_TARGET_WORDS only; this dict is kept so the
    # module imports cleanly but is no longer read.
}


# ─────────────────────────────────────────────────────────────────────
# Tiers — audience layers on top of registers
# ─────────────────────────────────────────────────────────────────────
# Three tiers, weighted for the publication's traffic and credibility
# mix. Tier 1 is the volume tier (retail readers). Tier 3 is the
# credibility tier (quantitative traders). Tier 2 is where a
# specific, timestamped trade lives.

TIER_WEIGHTS: dict[str, int] = {
    "tier_1": 70,
    "tier_2": 25,
    "tier_3": 5,
}

TIER_TARGET_WORDS: dict[str, int] = {
    # S.8.73 — orphaned by S.8.58. target_words_for reads
    # UNIFORM_TARGET_WORDS only; this dict is kept so the
    # module imports cleanly but is no longer read.
}

TIER_RULES: dict[str, str] = {
    "tier_1": (
        "Audience: ordinary retail investors.\n"
        "Length: 300-400 words.\n"
        "Vocabulary: plain English. Translate every technical term "
        "on first use.\n"
  
    ),
    "tier_2": (
        "Audience: intermediate retail investors.\n"
        "Length: 500-700 words.\n"
        "Vocabulary: technical terms allowed, translated on first "
        "use.\n"
        "Reader placement: mandatory.\n"
        "Specific example: mandatory. One trade with a date or time, "
        "an entry or fill price, a position size, and a result.\n"
        "Writer moment: one or two sentences. Admission of a prior "
        "mistake is welcome.\n"
        "Closing: a two-way test or a specific level to watch."
    ),
    "tier_3": (
        "Audience: quantitative and systematic traders.\n"
        "Length: 700-1000 words.\n"
        "Vocabulary: full technical vocabulary. No translation.\n"
        "Specific example: mandatory. A backtest, a dataset, or a "
        "mechanism explained at the level of order flow, execution, "
        "or market microstructure.\n"
        "Writer moment: optional.\n"
        "Closing: a testable claim or a named failure mode."
    ),
}


def get_tier_directive(tier: str | None) -> str:
    """Return the tier's audience and shape rules. Empty string for
    an unknown tier."""
    if not tier:
        return ""
    return TIER_RULES.get(tier, "")


def pick_tier(index: int = 0) -> str:
    """Deterministically pick a tier from the weighted distribution.

    index is a monotonically increasing counter (e.g. the article
    sequence number). The weighted distribution is expanded to a
    fixed cycle so that over any 20 consecutive articles, 14 are
    tier_1, 5 are tier_2, and 1 is tier_3.

    The caller passes a counter; this function returns a tier.
    """
    cycle = (
        ["tier_1"] * 14
        + ["tier_2"] * 5
        + ["tier_3"] * 1
    )
    return cycle[index % len(cycle)]


# S.8.58 — one target for every article. No per-subblock override,
# no tier, no register. Every subblock gets the same number, so the
# model never has to balance competing length directives.
UNIFORM_TARGET_WORDS = 900

# The band is absolute, not ratio-based. Every article lands in
# this range or drafts. 630 is the reviewer's floor; 1200 is the
# ceiling that keeps articles from sprawling.
UNIFORM_FLOOR = 630
UNIFORM_CEILING = 1200


def target_words_for(
    subblock_slug: str | None = None,
    register: str | None = None,
    tier: str | None = None,
) -> int:
    """Return the target word count for an article.

    S.8.58 — one target for everything. The arguments are kept so
    existing callers do not change; they are ignored.
    """
    # S.9.01 — Groq writes ~450-720 words on the concept-explainer
    # register. The 900 target produced padding and delivery failures.
    # Meet the model where it is: 600 target for class_room.
    if subblock_slug == "class_room" and register == "concept-explainer":
        return 600
    if register == "market-structure":
        return 700
    if register == "ticker-note":
        return 400
    if register == "practitioner-process":
        return 600
    if register == "practitioner-risk":
        return 650
    if register == "practitioner-strategies":
        return 650
    if register == "practitioner-indicators":
        return 650
    if register == "practitioner-screen":
        return 650
    if register == "market_analysis":
        return 1000
    return UNIFORM_TARGET_WORDS


# ─────────────────────────────────────────────────────────────────────
# Common prohibitions — enforced for every register at delivery time
# ─────────────────────────────────────────────────────────────────────

COMMON_PROHIBITIONS: list[tuple[str, str]] = [
    ("bracketed placeholder",
     r"\[(?:SOURCE|DATE|CONFIRM|CUT|TBD|TODO|FIXME|INSERT|PLACEHOLDER)[^\]]*\]"),
    ("prompt fragment",
     r"\b(?:Write a|Keep every|If any sentence|Do not|Deliver only|Pass 1|Pass 2)\b|"
     r"(?:^|\n)Constraints:|"
     r"\b(?:need to follow structure|So we need to)\b"),
    ("field label",
     r"(?:Upfront Problem to Solve|Reader Profile|Assignment:)"
     r"|(?:^|\n)\s*Context from\s*\["),
    ("publishing block",
     r"\b(?:Publish\s*&\s*Distribution Layer|Dispatch this|export Markdown)\b"),
    ("reference footer",
     r"\b(?:Primary Source Reference and Verification|Verify Primary Source Link)\b"),
    ("credential list",
     r"\b(?:\d{2,}\+?\s*years? (?:of )?(?:trading|experience|in the chair)|"
     r"\d{2,}\s*accounts? (?:on|across)|"
     r"\d{2,}\s*platforms?)\b"),
    ("meta-commentary",
     r"\b(?:in this article|it'?s worth noting|let'?s dive in|in conclusion|"
     r"at the end of the day|when it comes to|in the world of"
     r"|this (?:six[- ]trade |\w+[- ]trade )batch"
     r"|the batch of \w+"
     r"|(?:in|across) this batch,\s+(?:we|i'?ll|we'?re|let'?s)"
     r"|looking at (?:this|the|these|those|the last|the next)"
     r"|this (?:piece|post|column)"
     r"|this sample of \w+)\b"),
    ("process narration",
     r"(?i)\b(?:the (?:source|instruction|register|prompt) (?:forbids|requires|"
     r"states|says)"
     r"|because the (?:instruction|register|prompt) (?:forbids|requires)"
     r"|the (?:first|second|third|next|final|last) step is"
     r"|must note the lack"
     r"|is reproduced here"
     r"|satisfies the requirement"
     r"|the coverage note therefore reads)\b"),
    ("summary close",
     r"(?i)(?:^|\n)\s*(?:Overall|In summary|To sum up|In conclusion)[,.]"),
    # S.8.55 — invented trades and thresholds. The model fabricates
    # when the sources are thin: a retail trader buying 200 shares at
    # $X, a break above $Y. These are lies dressed as analysis.
    ("invented trade",
     r"(?:a (?:retail |institutional |active |professional )?trader "
     r"(?:placed|bought|sold|entered|opened|closed)"
     r"|market[- ]order for \d+)"),
    ("invented threshold",
     r"(?:a (?:break|drop|move|close) (?:above|below) \$[\d.]+"
     r"|(?:support|resistance) at \$[\d.]+)"),
    # S.8.77 — claimed personal experience the model does not have.
    # "In years of watching..." and "From experience screening..."
    # are invented credentials. The model writes about markets from
    # data; it has not watched markets for years.
    ("fabricated credential",
     r"(?i)\b(?:in (?:my |\d+ )?years? of (?:watching|observing|"
     r"trading|studying|tracking)"
     r"|from (?:my )?experience (?:screening|trading|watching|"
     r"observing)"
     r"|i(?:.ve| have) (?:watched|observed|seen|traded)"
     r"|having (?:watched|observed|traded) (?:the )?market(?:s)? for)"
     r"\b"),
]


# ─────────────────────────────────────────────────────────────────────
# Delivery check — enforce register rules + common prohibitions
# ─────────────────────────────────────────────────────────────────────

class DeliveryCheckError(Exception):
    """Raised when a generated piece fails the delivery check."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__(
            f"Delivery check failed with {len(violations)} violation(s): "
            + "; ".join(violations)
        )


def _check_common_prohibitions(text: str) -> list[str]:
    violations: list[str] = []
    for label, pattern in COMMON_PROHIBITIONS:
        if re.search(pattern, text):
            violations.append(label)
    return violations


# Registers whose fragments ask for a markdown table. Everyone else
# is flagged if a table appears — that catches prompt-fragment
# artifacts ("| metric | value |\n|---|---|") that shouldn't ship.
TABLE_ALLOWED: frozenset[str] = frozenset({
    "market-daily",
    "viewpoint",
    "screen-view",
    "insider-log",
    "macro-view",
    "concept-explainer",
    "psychology-note",
})


def _check_register_prohibitions(text: str, register: str) -> list[str]:
    violations: list[str] = []

    # Tables are handled by _check_table_prohibition (called
    # separately by delivery_check) so that trading-log's blotter
    # exception can be expressed cleanly.

    # retail-news and crypto-news require a two-way test and one
    # first-person moment at most





    # S.8.27 — trading-log requires a specific structure.

    return violations


# Markdown tables have a separator row ("|---|---|"). Blotter logs
# don't. The presence of the separator distinguishes a real table
# from a pipe-separated log line.
_MARKDOWN_TABLE_SEP = re.compile(r"(?m)^\s*\|[\s\-:]+\|\s*$")


def _check_table_prohibition(text: str, register: str) -> list[str]:
    """Flag tables where the register doesn't allow them.

    Registers in TABLE_ALLOWED produce tables by design.
    trading-log writes pipe-separated blotter lines but no markdown
    tables — allow pipes, flag a markdown separator row.

    Every other register allows EXACTLY ONE table, and only when
    its header matches the verification schema (Date, Entry,
    Result). Any second table, or a table with a different header,
    is flagged.
    """
    if register in TABLE_ALLOWED:
        return []

    # Walk lines to find contiguous pipe-table blocks.
    line_tables: list[list[str]] = []
    current: list[str] = []
    for ln in text.splitlines():
        if re.match(r"^\s*\|.*\|\s*$", ln):
            current.append(ln)
        else:
            if current:
                line_tables.append(current)
                current = []
    if current:
        line_tables.append(current)

    if not line_tables:
        return []
    # market_analysis produces two tables by design: the recent-
    # trades table and the account equity curve. Every other
    # register allows exactly one.
    max_tables = 2 if register == "market_analysis" else 1
    if len(line_tables) > max_tables:
        return [f"multiple tables: {len(line_tables)}"]
    header = line_tables[0][0]
    required = ("Date", "Entry", "Result")
    if not all(col in header for col in required):
        return [
            f"table header does not match verification schema "
            f"(need Date, Entry, Result); got: {header.strip()[:80]}"
        ]
    return []


_SECTION_HEADER_RE = re.compile(
    r"(?m)"
    r"(?:"
    r"^#{1,6}\s+[A-Z]"
    r"|^\*\*[A-Z][a-zA-Z ]{1,40}\*\*"
    r"|^\*[A-Z][a-zA-Z ]{1,40}\*"
    r"|^(?:Opening|Body|Counter|Doubt|Decision|Reader|Person|Trade|"
    r"End|Voice|Reaction|Context|Framework|Title|Before writing|"
    r"Evidence rule|Forbidden):\s*$"
    r")"
)


_TRADER_AUTHOR_REGISTERS: frozenset[str] = frozenset({
    # S.8.90 — collapsed to the six surviving registers. Every
    # register now tells the trader's story — the trader is the
    # author in all six. The delivery checks that used this set
    # (third-person reference, checklist mode, decorative
    # language, empty sentences, no-figure sentences) apply to
    # every register.
    "market-daily",
    "viewpoint",
    "screen-view",
    "insider-log",
    "macro-view",
    "concept-explainer",
    "market_analysis",
})


_CHECKLIST_RE = re.compile(
    r"(?:^|\n)\s*I (?:see|note|read|observe|watch)\b.*?"
    r"(?:\n\s*I (?:see|note|read|observe|watch)\b){2,}",
    re.DOTALL,
)


_DECORATIVE_RE = re.compile(
    r"\b(?:set the tone|caught the eye|outshone|"
    r"tipped the market|typical volatility ranges|from my seat)\b",
    re.IGNORECASE,
)


def _check_decorative_language(text: str, register: str) -> list[str]:
    if register not in _TRADER_AUTHOR_REGISTERS:
        return []
    matches = _DECORATIVE_RE.findall(text)
    if matches:
        return [f"decorative language: {sorted(set(m.lower() for m in matches))}"]
    return []


def _check_no_the_trader(text: str, register: str) -> list[str]:
    """In trader-author registers, the trader is the author.
    'The trader' or 'the desk' in third person is a violation."""
    if register not in _TRADER_AUTHOR_REGISTERS:
        return []
    matches = re.findall(r"\bthe trader\b|\bthe desk\b", text, re.IGNORECASE)
    if matches:
        return [f"third-person reference: 'the trader' or 'the desk' ({len(matches)}x)"]
    return []


def _check_empty_sentences(text: str, register: str) -> list[str]:
    """A trader's sentence adds a figure, a decision, or a plan.
    If it adds none of those, it is decoration.

    S.8.97 — concept-explainer is narrative prose between the
    numbers. Sentences like 'The fill came through, and the trade
    moved against me' carry the arc even without a digit or an
    'I'. The register's own shape spec keeps the piece specific;
    this check is for the market-wire registers."""
    if register not in _TRADER_AUTHOR_REGISTERS:
        return []
    if register in ("concept-explainer", "psychology-note"):
        return []
    # market_analysis: the has-number/has-I/has-If test flags
    # every prose sentence without data. The verify LLM catches
    # fabricated claims and the precise_times + has_fix checks
    # catch structural failures. Empty-sentence checking here
    # produces more false positives than true positives.
    if register == "market_analysis":
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    offenders = []
    for s in sentences:
        s = s.strip()
        if len(s) < 30:
            continue
        has_number = bool(re.search(r"\d", s))
        has_first_person = bool(re.search(r"\bI\b|\bI'", s))
        has_conditional = s.startswith("If ")
        has_reader = bool(re.search(r"\bIf you (held|were|own)\b", s))
        if not (has_number or has_first_person or has_conditional or has_reader):
            offenders.append(s[:100])
    return [f"empty sentence: {s!r}" for s in offenders[:8]]


def _check_checklist_mode(text: str, register: str) -> list[str]:
    """Flag articles that are lists of 'I see... I note... I read...'
    sentences, or that use 'First, ... Second, ... Third, ...'
    enumeration dressed as prose. The trader's piece has reasoning
    between the numbers, not a checklist of them."""
    if register not in _TRADER_AUTHOR_REGISTERS:
        return []
    violations: list[str] = []
    if _CHECKLIST_RE.search(text):
        violations.append(
            "checklist mode: body is a list of 'I see/I note/I read' "
            "sentences, not reasoning"
        )
    # S.8.97 — 'First, ... Second, ... Third, ...' as sentence-
    # openers is enumerated structure in disguise. Flag it.
    # S.9.01 — but concept-explainer's register explicitly asks for
    # a diagnostic list of four to six items. The model uses ordinals
    # to mark them. That's not a numbered list; it's the shape the
    # register asked for. Skip the ordinal check for that register.
    if register in ("concept-explainer", "psychology-note"):
        return violations
    seq_hits = re.findall(
        r"(?:^|(?<=[.!?]))\s*(First|Second|Third|Fourth|Fifth|"
        r"Sixth|Seventh)\s*,",
        text,
    )
    if len(seq_hits) >= 2:
        violations.append(
            f"enumerated structure: 'First/Second/Third...' x"
            f"{len(seq_hits)} — write continuous prose, not a "
            f"numbered list in sentence form"
        )
    return violations


def _check_trader_third_person(text: str, register: str) -> list[str]:
    """Flag articles that refer to 'the trader' in the third
    person. In these registers the trader is the author, not a
    subject. 'The trader decided' should be 'I decided.'"""
    if register not in _TRADER_AUTHOR_REGISTERS:
        return []
    matches = re.findall(r"\bthe trader\b", text, re.IGNORECASE)
    if matches:
        return [f"third-person reference to 'the trader' ({len(matches)}x) — write AS the trader, not about"]
    return []


def _check_section_headers(text: str) -> list[str]:
    """Reject articles with section headers or bold labels at
    line starts. The register's beats are instructions for the
    model, not labels for the article body.

    Two layers:
      - _SECTION_HEADER_RE catches markdown # / ## headers.
      - _BOLD_LABEL_RE catches any **Title Case** phrase at the
        start of a line, whether or not it's a named beat. The
        model will reach for '**Problem**', '**Mechanism**',
        '**Diagnostic list**' even when forbidden by name — this
        catches the pattern, not just the words.
    """
    violations = []
    for m in _SECTION_HEADER_RE.finditer(text):
        label = m.group(0).strip()
        violations.append(f"section header in article: {label!r}")
    # Bold label at line start: **Something**
    for m in re.finditer(
        r"(?m)^\s*\*\*[A-Z][^*\n]{1,40}\*\*",
        text,
    ):
        label = m.group(0).strip()
        violations.append(f"bold section label: {label!r}")
    # Plain-line beat names: Hook, Mechanism, Diagnostic 1, Fix 2, etc.
    _BEAT_NAMES = (
        r"Hook|Name the problem|Mechanism|Diagnostic list|Diagnostics?"
        r"|Fixes?|Reframe|CTA|Checklist"
    )
    for m in re.finditer(
        rf"(?m)^\s*(?:\d+\.\s*)?(?:\*\*)?({_BEAT_NAMES})"
        rf"(?:\s+\d+)?(?:\*\*)?\s*[:\u2014-]?\s*$",
        text,
    ):
        label = m.group(0).strip()
        violations.append(f"beat name as heading: {label!r}")
    return violations


def _check_specificity(text: str, register: str) -> list[str]:
    """concept-explainer pieces must carry the trader's specifics:
    an instrument or universe, a numeric threshold, a citation."""
    if register not in ("concept-explainer", "psychology-note"):
        return []
    violations: list[str] = []
    # S.8.98 — widened. Matches:
    #   $10,000 / $200,000 / $12,500        (dollar amounts)
    #   1.5 × / 1.5x / 150% / 0.02 %        (ratios, percents)
    #   20-day / 3-day / thirty trades      (windows)
    #   2 contracts / 500 shares            (counts)
    # Handles non-breaking spaces (\u00A0) from the model's output.
    _NUM_RE = re.compile(
        r"(?:"
        r"\$\d[\d,]*(?:\.\d+)?"
        r"|"
        r"\b\d+(?:\.\d+)?[\s\u00A0]?(?:%|×|x\b|bps?\b)"
        r"|"
        r"\b\d+[\s\u00A0]*-[\s\u00A0]?day"
        r"|"
        r"\b\d+[\s\u00A0]+(?:contracts?|shares?|lots?)"
        r"|"
        r"\b(?:ten|twenty|thirty|forty|fifty|sixty|"
        r"hundred|thousand)[\s\u00A0]+(?:trades?|days?|"
        r"contracts?|shares?)"
        r")",
        re.IGNORECASE,
    )
    if not _NUM_RE.search(text):
        violations.append(
            "no numeric threshold from the process"
        )

    # S.9.04 — instrument / universe.
    # Match either an explicitly listed ticker (ES, ZN, SPY, ...)
    # or any all-caps token 2-5 letters (AAPL, TSLA, NVDA, IWM, XLE),
    # or a named universe phrase.
    if not re.search(
        r"(?:"
        r"\b(?:ES|ZN|MES|MNQ|MYM|M2K|CL|GC|SI|NG|ZB|ZC|ZS|"
        r"SPY|QQQ|TLT|GLD|HYG|IWM|DIA|EEM|EFA|XL[KEFPVLIBU]|"
        r"S&P 500|E-mini|Globex|CME|COMEX|NYMEX|CBOT|"
        r"top \d+ by (?:dollar )?volume|Russell 2000|Nasdaq)\b"
        r"|"
        r"\b[A-Z]{2,5}\b"          # any 2-5 letter all-caps ticker
        r")",
        text,
    ):
        violations.append(
            "no specific instrument or universe from the process"
        )

    # S.9.04 — citation. Only required when the body CLAIMS a source.
    # A piece that never mentions "the literature", "the research",
    # "a study", "the paper", or "empirical" is not obligated to cite.
    claims_a_source = bool(re.search(
        r"\b(?:literature|research|study|studies|paper|papers|"
        r"empirical|documented|shown by|as shown in|per the)\b",
        text,
        re.IGNORECASE,
    ))
    if claims_a_source and not re.search(
        r"\b[A-Z][a-z]+\s+(?:and\s+[A-Z][a-z]+\s+)?\(?\d{4}\)?",
        text,
    ):
        violations.append(
            "claims a source but names no paper"
        )
    return violations


def _check_reader_voice(text: str, register: str) -> list[str]:
    """concept-explainer is the trader's first person. Reader-directed
    second person is a register failure — it turns the piece into
    self-help."""
    if register not in ("concept-explainer", "psychology-note"):
        return []
    hits = re.findall(r"\b(you|your|You|Your)\b", text)
    if len(hits) >= 5:
        return [
            f"reader-voice 'you/your' x{len(hits)} — write in the "
            f"trader's first person, not second person"
        ]
    return []


# ──── market_analysis four-criteria checks ────
# Every market_analysis article must contain: precise times,
# exact prices, account figures (dollar AND percent), and one
# moment of emotional loss of control.

def _check_precise_times(body: str) -> str | None:
    """Article must have at least 2 clock times with timezone."""
    import re
    matches = re.findall(
        r"\b\d{1,2}:\d{2}\s*(?:[AaPp][Mm]|[Uu][Tt][Cc]|[Cc][Tt]|[Ee][Tt]|[Ee][Ss][Tt])",
        body,
    )
    return None if len(matches) >= 2 else "precise_times_missing"


def _check_exact_prices(body: str) -> str | None:
    """Article must contain at least 3 exact prices (X.XX format)."""
    import re
    matches = re.findall(r"\b\d{2,4}\.\d{2}\b", body)
    return None if len(matches) >= 3 else "exact_prices_missing"


def _check_account_figures(body: str) -> str | None:
    """Article must contain both a dollar amount and a percent."""
    import re
    has_dollars = bool(re.search(r"\$\d[\d,]*", body))
    has_pct = bool(re.search(r"\b\d+(?:\.\d+)?\s*%", body))
    if has_dollars and has_pct:
        return None
    return "account_figures_missing"


def _check_emotional_moment(body: str) -> str | None:
    """Article must contain a first-person emotional admission."""
    import re
    patterns = [
        r"\b(?:I (?:ignored|refused|couldn'?t|could not|wanted|told myself|let|let my|let the|convinced myself))\b",
        r"\b(?:denial|refus(?:al|ed)|ego|tilt|impulse|tempted)\b",
        r"\b(?:I was (?:angry|frustrated|scared|afraid|desperate))\b",
    ]
    for p in patterns:
        if re.search(p, body, re.IGNORECASE):
            return None
    return "emotional_moment_missing"


def _check_chart_header(text: str) -> str | None:
    """Flag articles that quote the block's chart header
    instead of naming the chart by what it shows. "the chart
    titled THE DAILY VIEW" is quoting a block header, not a
    chart reference."""
    import re
    if re.search(r"chart titled ['\"]?THE ", text, re.IGNORECASE):
        return "chart_quoted_by_header"
    return None


def _check_has_fix(text: str) -> str | None:
    """Article must contain a process fix.

    A fix is any sentence that either:
      - opens with a conditional (If / When / Whenever) and commits
        to an action in first person
      - commits to a discipline ("I now", "I only", "I never", etc.)
      - names a rule ("the rule is", "my rule", "the conditional rule")
      - states a change ("I added", "I changed", "going forward")
    """
    import re
    patterns = [
        # Conditional + first-person commitment
        r"(?:^|\.\s+|\n\s*)(?:If|When|Whenever)\s+.{0,300}?,\s*(?:then\s+)?(?:I\s+|I'll\s+|I will\s+)",
        # Commitment verbs
        r"\bI\s+(?:now|only|always|never|skip|avoid|limit|cap|require|refuse|stop)\b",
        # Rule statements
        r"\b(?:the|my)\s+(?:conditional\s+)?rule\s+(?:is|I\s|codifies|emerged|governs|now)\b",
        # Change statements
        r"\bI\s+(?:added|changed|started|stopped|began|updated|modified|revised|tightened|widened)\b",
        # Going forward
        r"\b(?:going forward|from now on|since that trade|the lesson)\b",
        # "The fix is" / "The takeaway is"
        r"\bthe\s+(?:fix|takeaway)\s+(?:is|was)\b",
    ]
    for p_ in patterns:
        if re.search(p_, text, re.IGNORECASE):
            return None
    return "fix_missing"


def _check_fabrication_hedge(text: str) -> str | None:
    """Flag articles where the model hedges on a number it invented.
    Phrases like "(not in the source)", "(approximate)", "(inferred)",
    "(estimated)" are admissions that the model is fabricating."""
    import re
    patterns = [
        # Only the parenthetical admissions. Legitimate prose
        # uses of "approximately" (e.g. "saving approximately
        # $1,200") are not fabrication.
        r"\(not in the source\)",
        r"\(approximate(?:ly)?\)",
        r"\(inferred\)",
        r"\(implied by the pattern\)",
        r"\(estimated\)",
        r"\bnot in the source\b",
        r"\binferred from the pattern\b",
        r"\bimplied by the chart\b",
    ]
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return "fabrication_hedge"
    return None




# ─────────────────────────────────────────────────────────────────────
# Per-template market_analysis gates.
#
# The four templates (trade, setup, dialogue, process) each have
# different shapes and different failure modes. The old flat gate
# checked every template against every rule, which produced false
# positives and let real failures through.
# ─────────────────────────────────────────────────────────────────────


def _check_no_checklist(text: str) -> str | None:
    """Flag articles that end with a checklist of questions."""
    import re
    tail = text[-800:] if len(text) > 800 else text
    if tail.count("?") >= 2:
        return "checklist_at_end"
    return None


def _check_no_process_narration(text: str) -> str | None:
    """Flag articles that narrate the process instead of doing it."""
    import re
    patterns = [
        r"\bthe (?:first|second|third|next|final|last) step is\b",
        r"\bI apply the filter\b",
        r"\bthe initial filter I apply\b",
        r"\bmy first step is\b",
        r"\bnext I (?:look|check|apply|examine)\b",
    ]
    for p_ in patterns:
        if re.search(p_, text, re.IGNORECASE):
            return "process_narration"
    return None


def _check_references_trade_log(text: str) -> str | None:
    """Process articles must reference at least one aggregate statistic."""
    import re
    patterns = [
        r"\bwin rate\b",
        r"\bexpectancy\b",
        r"\bavg(?:erage)? win\b",
        r"\bavg(?:erage)? loss\b",
        r"\bslippage\b",
        r"\bdrawdown\b",
        r"\bevent-day penalty\b",
        r"\bover \d+ trades\b",
    ]
    for p_ in patterns:
        if re.search(p_, text, re.IGNORECASE):
            return None
    return "trade_log_not_referenced"


def _check_no_labeled_rule(text: str) -> str | None:
    """Flag articles that end with 'The final rule:' as a label.

    The rule should stand on its own. The label is a template tic.
    """
    import re
    if re.search(r"^\s*The final rule\s*[:—-]", text, re.MULTILINE | re.IGNORECASE):
        return "labeled_final_rule"
    return None


def _check_ma_trade(text: str, template_id: str | None) -> list[str]:
    if template_id != "trade":
        return []
    v = [
        _check_no_labeled_rule(text),
        _check_precise_times(text),
        _check_has_fix(text),
        _check_fabrication_hedge(text),
        _check_no_checklist(text),
        _check_chart_header(text),
    ]
    return [x for x in v if x]


def _check_ma_setup(text: str, template_id: str | None) -> list[str]:
    if template_id != "setup":
        return []
    v = [
        _check_no_labeled_rule(text),
        _check_has_fix(text),
        _check_fabrication_hedge(text),
        _check_no_checklist(text),
        _check_no_process_narration(text),
        _check_chart_header(text),
    ]
    return [x for x in v if x]


def _check_ma_dialogue(text: str, template_id: str | None) -> list[str]:
    if template_id != "dialogue":
        return []
    v = [
        _check_no_labeled_rule(text),
        _check_precise_times(text),
        _check_has_fix(text),
        _check_fabrication_hedge(text),
        _check_no_checklist(text),
        _check_no_process_narration(text),
        _check_chart_header(text),
    ]
    return [x for x in v if x]


def _check_ma_process(text: str, template_id: str | None) -> list[str]:
    if template_id != "process":
        return []
    v = [
        _check_no_labeled_rule(text),
        _check_has_fix(text),
        _check_fabrication_hedge(text),
        _check_no_checklist(text),
        _check_no_process_narration(text),
        _check_references_trade_log(text),
    ]
    return [x for x in v if x]


def delivery_check(
    text: str,
    register: str,
    template_id: str | None = None,
) -> None:
    """Run the full delivery check. Raises DeliveryCheckError on failure.

    template_id controls which checks apply. Setup articles have no
    trade, so the trade-specific checks (precise times, exact prices,
    emotional moment) are skipped. Trade articles run the full set.
    """
    _is_setup = (template_id == "setup")
    _skip_trade_checks = _is_setup

    if register not in REGISTERS:
        raise DeliveryCheckError([f"unknown register: {register}"])
    violations = (
        _check_common_prohibitions(text)
        + _check_register_prohibitions(text, register)
        + _check_table_prohibition(text, register)
        + _check_section_headers(text)
        + _check_trader_third_person(text, register)
        + _check_checklist_mode(text, register)
        + _check_decorative_language(text, register)
        + _check_empty_sentences(text, register)
        + _check_no_the_trader(text, register)
        + _check_specificity(text, register)
        + _check_reader_voice(text, register)
    )
    # market_analysis has four templates. Each has its own gate.
    if register == "market_analysis":
        violations += _check_ma_trade(text, template_id)
        violations += _check_ma_setup(text, template_id)
        violations += _check_ma_dialogue(text, template_id)
        violations += _check_ma_process(text, template_id)
    if violations:
        raise DeliveryCheckError(violations)


# ─────────────────────────────────────────────────────────────────────
# Register picker — deterministic rotation per subblock
# ─────────────────────────────────────────────────────────────────────

def pick_register(subblock: str, index: int = 0) -> str:
    """Pick a register for a subblock. index rotates between
    allowed registers when a subblock maps to more than one."""
    if subblock not in SUBBLOCK_REGISTERS:
        raise KeyError(f"unknown subblock: {subblock}")
    allowed = SUBBLOCK_REGISTERS[subblock]
    if not allowed:
        return DEFAULT_REGISTER
    return allowed[index % len(allowed)]


# ─────────────────────────────────────────────────────────────────────
# Accessors
# ─────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────
# Form-aware register fragments for the block registers.
# The block register template already describes all three forms.
# But the model reads the *user prompt* (the F1/F2/F3 template)
# last, and the user prompt's voice rule overrides the register's
# voice rule. Fix: after the register fragment is rendered, append
# a form-specific voice directive and a form-specific beat list,
# so the register has the last word on voice and beats.
# ─────────────────────────────────────────────────────────────────

def register_fragment(name: str | None, **params) -> str:
    """Return the prompt fragment for a register, with parameters
    substituted. Falls back to DEFAULT_REGISTER if name is unknown.

    Parameters:
      market — 'US' (default) or 'India' for wire-close
      form   — 'F1' | 'F2' | 'F3' for the block registers. When set,
               the fragment ends with a form-specific voice line and
               a form-specific beat list.
    """
    if not name or name not in REGISTERS:
        name = DEFAULT_REGISTER
    tmpl = REGISTERS[name]
    if "{market}" in tmpl:
        market = params.get("market", "US")
        tmpl = tmpl.format(market=market)

    # Voice and beats are the subblock prompt's job (F1/F2/F3
    # templates). The register describes audience, evidence rules,
    # vocabulary, and thresholds only. Emitting a FORM block here
    # duplicated the subblock prompt and contradicted it on voice.
    return tmpl


# ─────────────────────────────────────────────────────────────────────
# Tier-nested register pools
# ─────────────────────────────────────────────────────────────────────
# Each subblock maps tier -> the registers eligible for that tier.
# A tier-3 piece on market_analysis picks quant-case-study; a tier-1
# piece picks retail-news or wire-close. SUBBLOCK_REGISTERS (the
# pre-tier map) is the fallback for subblocks not yet listed here.

SUBBLOCK_TIERS: dict[str, dict[str, list[str]]] = {
    "market_analysis": {
        # Single register. market_analysis is a personal blog now,
        # not six separate regime reports. The register names the
        # voice and the beat shape; the six block-bN-* registers
        # are retired for this subblock.
        "tier_1": ["market_analysis"],
        "tier_1": ["market_analysis"],
        "tier_1": ["market_analysis"],
    },
    "market_condition": {
        "tier_1": ["market-regime"],
        "tier_2": ["market-regime"],
        "tier_3": ["market-regime"],
    },
    "market_signals": {
        "tier_1": ["market-regime"],
        "tier_2": ["market-regime"],
        "tier_3": ["market-regime"],
    },
    "market_cap": {
        "tier_1": ["market-structure"],
        "tier_2": ["market-structure"],
        "tier_3": ["market-structure"],
    },
    "stock_lists": {
        "tier_1": ["market-structure"],
        "tier_2": ["market-structure"],
        "tier_3": ["market-structure"],
    },
    "fundamental_metrics": {
        "tier_1": ["market-regime"],
        "tier_2": ["market-regime"],
        "tier_3": ["market-regime"],
    },
    "movers": {
        "tier_1": ["market-daily"],
        "tier_2": ["market-daily"],
        "tier_3": ["market-daily"],
    },
    "screener_picks": {
        "tier_1": ["practitioner-screen"],
        "tier_2": ["practitioner-screen"],
        "tier_3": ["practitioner-screen"],
    },
    "technical_indicators": {
        "tier_1": ["practitioner-indicators"],
        "tier_2": ["practitioner-indicators"],
        "tier_3": ["practitioner-indicators"],
    },
    "asset_classes": {
        "tier_1": ["market-structure"],
        "tier_2": ["market-structure"],
        "tier_3": ["market-structure"],
    },
    "economic_events": {
        "tier_1": ["market-structure"],
        "tier_2": ["market-structure"],
        "tier_3": ["market-structure"],
    },
    "investment_strategies": {
        "tier_1": ["practitioner-strategies"],
        "tier_2": ["practitioner-strategies"],
        "tier_3": ["practitioner-strategies"],
    },

    "risk_management": {
        "tier_1": ["practitioner-risk"],
        "tier_2": ["practitioner-risk"],
        "tier_3": ["practitioner-risk"],
    },
    "trading_styles": {
        "tier_1": ["concept-explainer"],
        "tier_2": ["concept-explainer"],
        "tier_3": ["concept-explainer"],
    },
    "class_room": {
        "tier_1": ["concept-explainer"],
        "tier_2": ["concept-explainer"],
        "tier_3": ["concept-explainer"],
    },
    "trading_tools": {
        "tier_1": ["concept-explainer"],
        "tier_2": ["concept-explainer"],
        "tier_3": ["concept-explainer"],
    },
    "trading_psychology": {
        "tier_1": ["psychology-note"],
        "tier_2": ["psychology-note"],
        "tier_3": ["psychology-note"],
    },
}


def eligible_registers(
    slug: str | None,
    tier: str | None = None,
) -> list[str]:
    """Return the registers a subblock can draw from, optionally
    filtered by tier.

    Precedence:
      1. SUBBLOCK_TIERS[slug][tier]  — tier-nested pool
      2. SUBBLOCK_TIERS[slug]        — union of all tiers,
                                       ONLY when no tier was passed
      3. SUBBLOCK_REGISTERS[slug]    — pre-tier map (fallback)
      4. POOL_MARKET                 — no slug at all

    When a tier is explicitly passed and the subblock's map does not
    contain it, the pool is empty — the subblock does not produce
    that tier. This prevents tier_1 from falling through to a
    register mapped only at tier_2/tier_3 (observed as
    trading_psychology tier_1 picking trading-log).
    """
    if slug and slug in SUBBLOCK_TIERS:
        tier_pool = SUBBLOCK_TIERS[slug]
        if tier:
            # Tier explicitly passed: exact match or nothing.
            return list(tier_pool.get(tier, []))
        # No tier passed — union across the subblock's tiers.
        return sorted({r for regs in tier_pool.values() for r in regs})
    if not slug:
        return list(POOL_MARKET)
    return SUBBLOCK_REGISTERS.get(slug, [])


def iter_registers(pool: Iterable[str]) -> Iterable[tuple[str, str]]:
    """Yield (name, spec) pairs for every register in a pool."""
    for name in pool:
        yield name, REGISTERS[name]