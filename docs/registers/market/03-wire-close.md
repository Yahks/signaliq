# wire-close

    Pool:     market
    Source:   "U.S. stocks closed lower across all five major
              indices on Wednesday" — a news-service markets wrap
    Markets:  US (default) | India (see "Market parameter" below)

## Shape

Opens with the summary fact, not a frame. The index close and the
week's move lead. No character, no tension.

Sections follow, each with a bolded subhead stating the section's
point in one line:

    - the close (indices, with the levels, the point moves, the %)
    - the "why" (what drove it, with named sources)
    - the table of index closes
    - the yields section
    - winners / losers (named, with percentages)
    - the cross-asset section (crude, gold, silver, bitcoin)
    - a short "lesson for traders"

## Person

Third person throughout. The writer is absent.

No first person. No second person. No opinion from the writer.
The register cites sources for opinions, not itself.

## Length

500-700 words, plus the list sections.

## Ending

A short "lesson for traders" section. A general truth stated as
instruction, not as personal experience.

Example: "A falling index does not mean every stock is falling."

## Forbidden

    - A narrative opening
      ("The American consumer has been carrying this economy...")
    - Any first-person or second-person address
    - Any opinion from the writer
    - A question as the closing
    - Causal language ("driven by", "because of") unless it
      comes from a named source

## Sample markers

From the sample article:

    - "U.S. stocks closed lower across all five major indices on
       Wednesday."
    - Named sources carry the opinions:
       "Fed Governor Michael Barr also said further rate hikes
        are likely needed."
    - Numbers with three levels of precision where relevant:
       "51,517.16, down 352.04 points or 0.68%"
    - Named winners and losers with percentages, not just counts:
       "Palo Alto Networks rose 5.00%... Airbnb fell 7.56%..."
    - Cross-asset close:
       "Gold fell 1.73% to $4,284.24, silver dropped 3.93%..."
    - The final "lesson for traders" section, one paragraph.

## Market parameter

The register has a `market` field:

    US (default)
      S&P 500, Nasdaq, Dow, Russell 2000, Nasdaq 100
      Indices are named with their ticker (^GSPC, ^IXIC, ^DJI, ^RUT)
      Yields referenced as "the 10-year Treasury yield"
      Flows referenced as institutional activity

    India
      Sensex, Nifty
      Dateline opener: "Mumbai, Sep 4 (PTI):"
      Analyst-quote density is higher (2-4 named quotes per piece)
      Market internals section (advancers / decliners count)
      FII / DII flows section
      Rupee and crore / lakh units
      Weekly move stated alongside the daily move

Same voice. Same shape. Different market conventions.

## Subblocks it fits

    market_analysis
    economic_events

## Scope

Whole market, post-bell. Not for intraday (use mid-session-recap
or retail-recap), not for single names (use ticker-alert or
single-ticker-brief).