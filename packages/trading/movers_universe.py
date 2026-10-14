"""Stock universe for the movers subblock scanner.

Two tiers:
  - SP100   — the S&P 100 constituents (the big ones).
  - RETAIL  — popular retail names not in the S&P 100 (TSLA, PLTR,
              COIN, etc.) that generate the most search traffic.

The scanner fetches quotes for the union. ~150 symbols total — one
batch fetch.
"""
from __future__ import annotations

SP100: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "BRK-B", "LLY", "AVGO",
    "TSM", "JPM", "V", "UNH", "XOM", "MA", "JNJ", "PG", "COST", "HD",
    "ABBV", "WMT", "MRK", "CVX", "KO", "PEP", "ADBE", "CRM", "BAC", "NFLX",
    "AMD", "TMO", "LIN", "ACN", "MCD", "ABT", "CSCO", "WFC", "DIS", "ORCL",
    "INTC", "VZ", "TXN", "INTU", "QCOM", "DHR", "AMGN", "PM", "CAT", "IBM",
    "GE", "NOW", "UNP", "SPGI", "RTX", "GS", "AMAT", "PFE", "UBER", "BLK",
    "ISRG", "BKNG", "LOW", "T", "HON", "TJX", "SYK", "ETN", "VRTX", "MDT",
    "C", "PLD", "REGN", "MMC", "BSX", "CB", "LMT", "SCHW", "ADP", "MDLZ",
    "CI", "BA", "SBUX", "GILD", "ADI", "DE", "MU", "MMM", "PANW", "KLAC",
    "SO", "PGR", "ZTS", "CDNS", "APH", "SNPS", "CME", "ICE", "CMG", "PYPL",
]

RETAIL: list[str] = [
    "TSLA", "PLTR", "COIN", "SOFI", "RIVN", "LCID", "NIO", "XPEV", "LI", "GME",
    "AMC", "MSTR", "HOOD", "RBLX", "U", "SNAP", "PINS", "SPOT", "SHOP", "XYZ",
    "ABNB", "DASH", "LYFT", "ZM", "DOCU", "ROKU", "TTD", "NET", "DDOG", "SNOW",
    "CRWD", "MDB", "TEAM", "ZS", "OKTA", "TWLO", "BABA", "JD", "PDD", "BIDU",
    "F", "GM", "AAL", "UAL", "DAL", "LUV", "CCL", "NCLH", "RCL", "MAR",
]


def get_universe() -> list[str]:
    """Return the full scan universe, deduplicated and stable."""
    seen: set[str] = set()
    out: list[str] = []
    for sym in SP100 + RETAIL:
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out

# Reference symbols movers fetches in addition to the equity
# universe. These aren't movers to write about — they're the
# context the event record needs: volatility regime, segment
# behavior, asset-class behavior. Read by the four downstream
# market-structure subblocks (economic_events, market_cap,
# asset_classes, stock_lists).
REFERENCE_SYMBOLS: list[str] = [
    "^VIX",   # volatility
    "SPY",    # large-cap segment
    "MDY",    # mid-cap segment
    "IWM",    # small-cap segment
    "TLT",    # rates / duration
    "GLD",    # commodities / hard assets
    "HYG",    # credit
    "UUP",    # currencies
]

