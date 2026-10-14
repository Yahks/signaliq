# Register library

A register is a distinct writing voice, extracted from a real
professional article. The pipeline picks a register per article so
that no two consecutive pieces read the same way.

Registers are grouped into pools. Each subblock draws from the
registers whose scope matches its data.

## Pool structure

    MARKET pool:    20 registers   (17 named, 8 sampled, 3 to add)
    EDUCATION pool: 10 registers   (0 built, 9 named, awaiting samples)
    MACRO/TOOLS:     5 registers   (0 built, awaiting samples)
                   ─────────────
    total:          35 registers

## Market pool — the 17 named

### Sampled (7) — spec written from a real article

    01  technical-strategist   chart-driven opinion, reasons in
                               orders, ends on a question

    02  retail-recap           narrative frame, vs-expectations
                               comparisons, one personal scan

    03  wire-close             post-bell, sectioned, data-carried,
                               absent writer.
                               Market parameter: US | India

    04  ticker-alert           single-symbol, reactive, question-
                               sectioned, SEO-shaped

    05  sector-picks           multi-name, thematic, three profiles
                               under one sector thesis

    06  research-screen        verdict-driven, bearish or bullish,
                               research-product funnel

    07  market-milestone       built around a threshold crossing;
                               "remember when" opening; table is
                               the deliverable

    Note: india-wire-close is a market parameter of wire-close,
    not a separate register.

### Provisional (9) — named, awaiting a sample

    08  strategist-note        structured, hedged, institutional;
                               "our desk believes"

    09  macro-strategist       rates, currencies, policy,
                               cross-asset

    10  mid-session-recap      written during the session, not
                               the close; timestamped

    11  narrative-recap        the register is the opening device

    12  explainer-recap        disambiguates a number the market
                               read wrong; "here's the technicality"

    13  markets-wrap           the whole tape: bonds, commodities,
                               currencies

    14  data-close             quiet-day default; mostly numbers,
                               two or three sentences of prose.
                               Fits 8 of the 10 market subblocks.

    15  stock-analyzer         one name, decision-oriented;
                               valuation, dividend safety, bull/bear

    16  single-ticker-brief    one name, short (150-250 words),
                               purely directional

## Subblock -> register map

### MARKET

    market_analysis     wire-close, technical-strategist, retail-recap,
                        market-milestone, data-close, strategist-note,
                        markets-wrap

    market_condition    technical-strategist, strategist-note,
                        macro-strategist, data-close

    market_signals      research-screen, data-close, sector-picks

    market_cap          market-milestone, data-close, sector-picks

    asset_classes       markets-wrap, macro-strategist, data-close

    stock_lists         research-screen, sector-picks, data-close

    screener_picks      research-screen, data-close

    technical_indicator data-close, technical-strategist

    fundamental_metrics stock-analyzer, ticker-alert, single-ticker-brief

    market_movers       ticker-alert, single-ticker-brief

### EDUCATION (0 built — 9 names, awaiting samples)

    trading_psychology      confessional, teacher, case-study, lesson

    trading_styles          comparison, guide, case-study

    investment_strategies   framework, case-study, explainer

    class_room              lesson, explainer, tutorial

### MACRO / TOOLS (0 built — deferred)

    credit_risk         TBD
    economic_events     TBD
    trading_tools       TBD

## Register spec format

    Each register lives in docs/registers/<pool>/NN-<name>.md
    and contains:

      - Name, pool, source article
      - Shape (opening, structure, ending)
      - Person (first / second / third)
      - Length band
      - Forbidden moves
      - Sample markers
      - Subblocks it fits

## Adding the next register

    1. Paste a sample article.
    2. Extract the register — what is constant across the sample.
    3. Name it.
    4. Write the spec to docs/registers/market/NN-<name>.md.
    5. Add it to this README.
    6. Wire the prompt fragment and the subblock map.
    