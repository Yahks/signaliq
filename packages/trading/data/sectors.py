"""Ticker → GICS sector lookup.

Static map covering the S&P 100 and the tickers that most often
appear in screener subblocks. Used to precompute sector
concentrations before the LLM sees the data, so the model never
does arithmetic on sector counts (S.7.10.4).

Default for an unmapped ticker: "Unclassified". The article can
then say "N names unclassified" instead of inventing a sector.

To extend: add entries to TICKER_TO_SECTOR. The GICS sector names
are the canonical ones (Technology, Communication Services,
Consumer Discretionary, Consumer Staples, Financials, Healthcare,
Energy, Industrials, Utilities, Real Estate, Materials).
"""
from __future__ import annotations


# GICS sector names. Keys are ticker symbols; values are the sector.
TICKER_TO_SECTOR: dict[str, str] = {
    # ── Technology ──────────────────────────────────────────────
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "TSM": "Technology", "AVGO": "Technology", "AMD": "Technology",
    "MU": "Technology", "SKHY": "Technology", "SPCX": "Technology",
    "IBM": "Technology", "CSCO": "Technology", "DELL": "Technology",
    "HPQ": "Technology", "TXN": "Technology", "ADI": "Technology",
    "AMAT": "Technology", "LRCX": "Technology", "KLAC": "Technology",
    "ASML": "Technology", "NOW": "Technology", "WDAY": "Technology",
    "INTU": "Technology", "ADSK": "Technology", "CDNS": "Technology",
    "SNPS": "Technology", "INTC": "Technology", "QCOM": "Technology",
    "ORCL": "Technology", "CRM": "Technology", "ADBE": "Technology",
    "ACN": "Technology", "PANW": "Technology", "SMCI": "Technology",
    "ARM": "Technology", "MRVL": "Technology",
    # ── Communication Services ─────────────────────────────────
    "GOOG": "Communication Services", "GOOGL": "Communication Services",
    "META": "Communication Services", "NFLX": "Communication Services",
    "VZ": "Communication Services", "T": "Communication Services",
    "TMUS": "Communication Services", "CMCSA": "Communication Services",
    "DIS": "Communication Services", "FOXA": "Communication Services",
    "WBD": "Communication Services", "PARA": "Communication Services",
    "EA": "Communication Services", "TTWO": "Communication Services",
    # ── Consumer Discretionary ─────────────────────────────────
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "MCD": "Consumer Discretionary",
    "NKE": "Consumer Discretionary", "SBUX": "Consumer Discretionary",
    "LOW": "Consumer Discretionary", "TGT": "Consumer Discretionary",
    "BKNG": "Consumer Discretionary", "TJX": "Consumer Discretionary",
    "F": "Consumer Discretionary", "GM": "Consumer Discretionary",
    "RIVN": "Consumer Discretionary", "LCID": "Consumer Discretionary",
    "NIO": "Consumer Discretionary", "XPEV": "Consumer Discretionary",
    "LULU": "Consumer Discretionary", "CMG": "Consumer Discretionary",
    "MAR": "Consumer Discretionary", "HLT": "Consumer Discretionary",
    # ── Consumer Staples ──────────────────────────────────────
    "WMT": "Consumer Staples", "COST": "Consumer Staples",
    "PG": "Consumer Staples", "KO": "Consumer Staples",
    "PEP": "Consumer Staples", "PM": "Consumer Staples",
    "MO": "Consumer Staples", "CL": "Consumer Staples",
    "KMB": "Consumer Staples", "GIS": "Consumer Staples",
    "MDLZ": "Consumer Staples", "KHC": "Consumer Staples",
    "KR": "Consumer Staples", "WBA": "Consumer Staples",
    "STZ": "Consumer Staples", "TAP": "Consumer Staples",
    # ── Financials ────────────────────────────────────────────
    "BRK.A": "Financials", "BRK.B": "Financials", "JPM": "Financials",
    "V": "Financials", "MA": "Financials", "BAC": "Financials",
    "WFC": "Financials", "GS": "Financials", "MS": "Financials",
    "C": "Financials", "BLK": "Financials", "AXP": "Financials",
    "SCHW": "Financials", "SPGI": "Financials", "CB": "Financials",
    "MMC": "Financials", "PGR": "Financials",
    # ── Healthcare ────────────────────────────────────────────
    "LLY": "Healthcare", "UNH": "Healthcare", "JNJ": "Healthcare",
    "ABBV": "Healthcare", "MRK": "Healthcare", "PFE": "Healthcare",
    "TMO": "Healthcare", "ABT": "Healthcare", "DHR": "Healthcare",
    "BMY": "Healthcare", "AMGN": "Healthcare", "GILD": "Healthcare",
    "ISRG": "Healthcare", "VRTX": "Healthcare", "MDT": "Healthcare",
    "SYK": "Healthcare", "CVS": "Healthcare", "CI": "Healthcare",
    # ── Energy ────────────────────────────────────────────────
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    "SLB": "Energy", "EOG": "Energy", "MPC": "Energy",
    "PSX": "Energy", "VLO": "Energy", "OXY": "Energy",
    # ── Industrials ───────────────────────────────────────────
    "GE": "Industrials", "CAT": "Industrials", "HON": "Industrials",
    "UPS": "Industrials", "BA": "Industrials", "RTX": "Industrials",
    "LMT": "Industrials", "DE": "Industrials", "MMM": "Industrials",
    "FDX": "Industrials", "NOC": "Industrials", "GD": "Industrials",
    "DAL": "Industrials", "UAL": "Industrials", "AAL": "Industrials",
    "LUV": "Industrials",
    # ── Utilities ─────────────────────────────────────────────
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities",
    "AEP": "Utilities", "D": "Utilities", "EXC": "Utilities",
    # ── Real Estate ───────────────────────────────────────────
    "AMT": "Real Estate", "PLD": "Real Estate", "SPG": "Real Estate",
    "CCI": "Real Estate", "EQIX": "Real Estate", "PSA": "Real Estate",
    # ── Materials ─────────────────────────────────────────────
    "LIN": "Materials", "APD": "Materials", "SHW": "Materials",
    "ECL": "Materials", "NEM": "Materials", "FCX": "Materials",
}


def sector_for(ticker: str) -> str:
    """Return the GICS sector for a ticker, or 'Unclassified'."""
    if not ticker:
        return "Unclassified"
    return TICKER_TO_SECTOR.get(ticker.upper(), "Unclassified")
