"""Scrapers that fetch external market data and write it to the DB.

Two modules:

  - market_history: daily closes for a configured list of symbols
    (VIX, SPY, MDY, IWM, TLT, GLD, HYG, UUP). Writes to market_history.
  - stock_metadata: sector, industry, market cap for symbols appearing
    in market_events.top_movers. Writes to stock_metadata.

Both use httpx against Yahoo's public chart and quoteSummary endpoints.
No API keys. On failure they log and return empty, so a downstream
task never crashes because a fetch failed.
"""
