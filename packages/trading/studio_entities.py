"""Ticker and entity extraction for Studio prompts.

Given "why is Tesla down today?", return ["TSLA"]. Given "what
happened to NVDA and AAPL?", return ["NVDA", "AAPL"].

Two lookup paths:

  1. Explicit tickers — uppercase words 1-5 chars that pass
     is_valid_us_ticker and aren't common English words.

  2. Company names — a static map of the most common names to
     tickers, matched on word boundaries.

A future commit adds an LLM fallback for prompts that mention
companies not in the map.
"""
from __future__ import annotations

import re

from .subblocks.base import is_valid_us_ticker


# The most-searched US tickers by company name. Not exhaustive;
# covers the top movers that drive retail search.
COMPANY_TO_TICKER: dict[str, str] = {
    "tesla": "TSLA",
    "apple": "AAPL",
    "nvidia": "NVDA",
    "microsoft": "MSFT",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "netflix": "NFLX",
    "amd": "AMD",
    "intel": "INTC",
    "boeing": "BA",
    "disney": "DIS",
    "walmart": "WMT",
    "coca cola": "KO",
    "coca-cola": "KO",
    "pepsi": "PEP",
    "pepsico": "PEP",
    "mcdonalds": "MCD",
    "mcdonald's": "MCD",
    "starbucks": "SBUX",
    "nike": "NKE",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "goldman": "GS",
    "goldman sachs": "GS",
    "morgan stanley": "MS",
    "bank of america": "BAC",
    "wells fargo": "WFC",
    "citigroup": "C",
    "berkshire": "BRK.B",
    "berkshire hathaway": "BRK.B",
    "exxon": "XOM",
    "exxonmobil": "XOM",
    "chevron": "CVX",
    "pfizer": "PFE",
    "moderna": "MRNA",
    "johnson & johnson": "JNJ",
    "johnson and johnson": "JNJ",
    "unitedhealth": "UNH",
    "visa": "V",
    "mastercard": "MA",
    "paypal": "PYPL",
    "block": "SQ",
    "square": "SQ",
    "coinbase": "COIN",
    "palantir": "PLTR",
    "salesforce": "CRM",
    "oracle": "ORCL",
    "adobe": "ADBE",
    "broadcom": "AVGO",
    "qualcomm": "QCOM",
    "micron": "MU",
    "arm": "ARM",
    "snowflake": "SNOW",
    "shopify": "SHOP",
    "airbnb": "ABNB",
    "uber": "UBER",
    "lyft": "LYFT",
    "doordash": "DASH",
    "spotify": "SPOT",
    "zoom": "ZM",
    "snap": "SNAP",
    "pinterest": "PINS",
    "reddit": "RDDT",
    "roblox": "RBLX",
    "unity": "U",
    "cloudflare": "NET",
    "datadog": "DDOG",
    "crowdstrike": "CRWD",
    "gitlab": "GTLB",
    "atlassian": "TEAM",
    "twilio": "TWLO",
    "okta": "OKTA",
    "servicenow": "NOW",
    "workday": "WDAY",
    "intuit": "INTU",
    "autodesk": "ADSK",
    "cadence": "CDNS",
    "synopsys": "SNPS",
    "texas instruments": "TXN",
    "analog devices": "ADI",
    "applied materials": "AMAT",
    "lam research": "LRCX",
    "kla": "KLAC",
    "asml": "ASML",
    "tsmc": "TSM",
    "taiwan semiconductor": "TSM",
    "ford": "F",
    "general motors": "GM",
    "rivian": "RIVN",
    "lucid": "LCID",
    "nio": "NIO",
    "xpeng": "XPEV",
    "li auto": "LI",
    "byd": "BYDDY",
    "toyota": "TM",
    "honda": "HMC",
    "ferrari": "RACE",
    "delta": "DAL",
    "united airlines": "UAL",
    "american airlines": "AAL",
    "southwest": "LUV",
    "fedex": "FDX",
    "ups": "UPS",
    "caterpillar": "CAT",
    "deere": "DE",
    "john deere": "DE",
    "3m": "MMM",
    "honeywell": "HON",
    "ge": "GE",
    "general electric": "GE",
    "ibm": "IBM",
    "cisco": "CSCO",
    "dell": "DELL",
    "hp": "HPQ",
    "hewlett packard": "HPQ",
    "verizon": "VZ",
    "at&t": "T",
    "att": "T",
    "t-mobile": "TMUS",
    "t mobile": "TMUS",
    "comcast": "CMCSA",
    "fox": "FOXA",
    "warner bros": "WBD",
    "paramount": "PARA",
    "amc": "AMC",
    "gamestop": "GME",
    "draftkings": "DKNG",
    "caesars": "CZR",
    "mgm": "MGM",
    "marriott": "MAR",
    "hilton": "HLT",
    "chipotle": "CMG",
    "dominos": "DPZ",
    "yum": "YUM",
    "target": "TGT",
    "costco": "COST",
    "home depot": "HD",
    "lowes": "LOW",
    "lowe's": "LOW",
    "best buy": "BBY",
    "kroger": "KR",
    "walgreens": "WBA",
    "cvs": "CVS",
    "dollar general": "DG",
    "dollar tree": "DLTR",
    "tjx": "TJX",
    "ross": "ROST",
    "gap": "GPS",
    "lululemon": "LULU",
    "under armour": "UAA",
    "columbia sportswear": "COLM",
    "tapestry": "TPR",
    "ralph lauren": "RL",
    "estee lauder": "EL",
    "procter & gamble": "PG",
    "procter and gamble": "PG",
    "colgate": "CL",
    "kimberly clark": "KMB",
    "kraft": "KHC",
    "kraft heinz": "KHC",
    "general mills": "GIS",
    "kellogg": "K",
    "mondelez": "MDLZ",
    "hershey": "HSY",
    "tyson": "TSN",
    "archer daniels": "ADM",
    "bunge": "BG",
    "conagra": "CAG",
    "campbell": "CPB",
    "smucker": "SJM",
    "anheuser": "BUD",
    "budweiser": "BUD",
    "constellation": "STZ",
    "molson": "TAP",
    "brown forman": "BF.B",
    "diageo": "DEO",
    "philip morris": "PM",
    "altria": "MO",
    "british american tobacco": "BTI",
    # Common ETFs and index proxies
    "energy": "XLE",
    "oil": "XLE",
    "banks": "XLF",
    "tech": "XLK",
    "healthcare": "XLV",
    "utilities": "XLU",
    "real estate": "XLRE",
    "consumer staples": "XLP",
    "consumer discretionary": "XLY",
    "industrials": "XLI",
    "materials": "XLB",
    "communication services": "XLC",
    "financials": "XLF",
    "semiconductors": "SMH",
    "semis": "SMH",
    "small caps": "IWM",
    "small cap": "IWM",
    "s&p 500": "SPY",
    "s&p500": "SPY",
    "sp500": "SPY",
    "nasdaq": "QQQ",
    "dow": "DIA",
    "dow jones": "DIA",
    "russell": "IWM",
    "russell 2000": "IWM",
    "vix": "VIX",
    "bitcoin": "BTC-USD",
    "btc": "BTC-USD",
    "ethereum": "ETH-USD",
    "solana": "SOL-USD",
}


# Words that look like tickers but aren't. Uppercase 2-5 letter
# English words that would false-match is_valid_us_ticker.
STOPWORDS = frozenset({
    "A", "I", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF",
    "IN", "IS", "IT", "ME", "MY", "NO", "OF", "OH", "OK", "ON", "OR",
    "SO", "TO", "UP", "US", "WE", "AND", "ARE", "BUT", "CAN", "FOR",
    "GET", "HAS", "HOW", "ITS", "NOT", "NOW", "ONE", "OUR", "OUT",
    "SEE", "THE", "TOO", "TWO", "USE", "WAS", "WAY", "WHO", "WHY",
    "YOU", "ALL", "ANY", "DAY", "DID", "END", "FAR", "FEW", "GOT",
    "HIM", "HIS", "LET", "MAN", "NEW", "OLD", "OWN", "PUT", "RUN",
    "SAY", "SHE", "TAX", "TOP", "TRY", "WAR", "YES", "YET", "BUY",
    "SELL", "HOLD", "LONG", "SHORT", "PUTS", "CALL", "CALLS", "CASH",
    "NEWS", "LAST", "WEEK", "YEAR", "TODAY",
})

# S.8.57 — words that look like tickers but are not. The
# _TICKER_RE matches any 1-5 letter uppercase word, so indicator
# names, form types, and acronyms from the prompt were being
# fetched as Yahoo quotes ("RSI", "SMA", "MACD", "SEC", "X", "Y").
# This blocklist stops the garbage fetches and the resulting
# noise in source_payload.
_TICKER_STOPWORDS = frozenset({
    # Technical indicators
    "RSI", "SMA", "EMA", "MACD", "ATR", "ADX", "OBV", "VWAP", "CCI",
    "MFI", "ROC", "BB", "KST", "DMI", "SAR", "ICHIMOKU",
    # Fundamental acronyms
    "EPS", "PE", "PB", "PS", "PEG", "ROE", "ROA", "ROIC", "FCF",
    "EBIT", "EBITDA", "DCF", "WACC", "TTM", "YOY", "QOQ", "MOM",
    "YTD", "PCE", "CPI", "PPI", "PMI", "GDP",
    # Institutional acronyms
    "SEC", "IPO", "CEO", "CFO", "COO", "CTO", "CIO", "CDO",
    "FED", "IMF", "ECB", "BOJ", "BLS", "DOJ", "FTC",
    "ETF", "ETN", "REIT", "MLP", "ADR", "SPV",
    # Tech acronyms the model might use
    "AI", "ML", "AR", "VR", "API", "SDK", "SAAS", "PAAS", "IAAS",
    # Form types
    "K", "Q", "S", "F", "DEF", "DEFA", "DEFA14A",
    # Single-letter false positives (multi-letter tickers are
    # validated by is_valid_us_ticker; single letters need this)
    "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M",
    "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
})


_TICKER_RE = re.compile(r"\b([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\b")


def extract_tickers(prompt: str) -> list[str]:
    """Return a de-duplicated list of tickers mentioned in a prompt.

    Two passes:
      1. Explicit uppercase tickers that pass is_valid_us_ticker
         and are not stopwords.
      2. Company names from COMPANY_TO_TICKER, matched on word
         boundaries in the lowercased prompt.

    Order preserves the first mention.
    """
    if not prompt:
        return []

    found: list[str] = []

    def _add(sym: str) -> None:
        s = sym.upper().strip()
        if s and s not in found:
            found.append(s)

    # Pass 1 — explicit tickers.
    for m in _TICKER_RE.finditer(prompt):
        candidate = m.group(1)
        if candidate in STOPWORDS or candidate in _TICKER_STOPWORDS:
            continue
        if not is_valid_us_ticker(candidate):
            continue
        _add(candidate)

    # Pass 2 — company names.
    lowered = prompt.lower()
    for name, ticker in COMPANY_TO_TICKER.items():
        if re.search(rf"\b{re.escape(name)}\b", lowered):
            _add(ticker)

    return found
