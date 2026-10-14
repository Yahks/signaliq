"""Shared helpers for FRED-backed trading subblocks."""
from __future__ import annotations

from packages.trading.sources.fred import fetch_releases, fetch_series

__all__ = ["fetch_series", "fetch_releases", "fmt_value"]


def fmt_value(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}"
