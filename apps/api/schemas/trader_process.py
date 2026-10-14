"""Pydantic schemas for the trader process."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


SECTIONS = (
    "daily_scan", "entry_logic", "position_sizing", "exit_logic",
    "holding_period", "research_to_rules", "chart_overlay",
    "no_backtest_rule", "multi_strategy_blend",
    "rolling", "stocks_vs_options", "universes",
    "emotion_removal", "fundamental_metrics", "trade_history_summary",
)


class ProcessDocument(BaseModel):
    daily_scan: str = Field("", max_length=8000)
    entry_logic: str = Field("", max_length=8000)
    position_sizing: str = Field("", max_length=8000)
    exit_logic: str = Field("", max_length=8000)
    holding_period: str = Field("", max_length=8000)
    research_to_rules: str = Field("", max_length=8000)
    chart_overlay: str = Field("", max_length=8000)
    no_backtest_rule: str = Field("", max_length=8000)
    multi_strategy_blend: str = Field("", max_length=8000)
    rolling: str = Field("", max_length=8000)
    stocks_vs_options: str = Field("", max_length=8000)
    universes: str = Field("", max_length=8000)
    emotion_removal: str = Field("", max_length=8000)
    fundamental_metrics: str = Field("", max_length=8000)
    trade_history_summary: str = Field("", max_length=8000)


class ProcessResponse(ProcessDocument):
    updated_at: datetime | None = None


class DraftListItem(BaseModel):
    id: str
    created_at: datetime
    reviewed_at: datetime | None = None
    preview: str


class DraftResponse(ProcessDocument):
    id: str
    created_at: datetime
    reviewed_at: datetime | None = None
