"""Pydantic schemas for market conditions."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class MarketConditions(BaseModel):
    what_i_track: str = Field("", max_length=8000)
    baselines: str = Field("", max_length=8000)
    regimes: str = Field("", max_length=8000)
    thresholds: str = Field("", max_length=8000)
    adjustments: str = Field("", max_length=8000)
    mechanisms: str = Field("", max_length=8000)


class MarketConditionsResponse(MarketConditions):
    updated_at: datetime | None = None
