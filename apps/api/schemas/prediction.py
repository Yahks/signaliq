"""Prediction + performance schemas."""
from datetime import datetime

from pydantic import BaseModel


class ModelProbs(BaseModel):
    home: float | None = None
    draw: float | None = None
    away: float | None = None


class MarketProbs(BaseModel):
    home: float | None = None
    draw: float | None = None
    away: float | None = None
    bookmakers: int | None = None


class PredictionResponse(BaseModel):
    id: str
    match_id: str
    home_team: str
    away_team: str
    competition: str
    kickoff_at: datetime
    match_status: str
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    expected_home_goals: float | None
    expected_away_goals: float | None
    prob_over_2_5: float
    prob_under_2_5: float
    # Extended markets (Phase 10.9a)
    prob_over_1_5: float | None = None
    prob_under_1_5: float | None = None
    prob_over_3_5: float | None = None
    prob_under_3_5: float | None = None
    prob_btts_yes: float | None = None
    prob_btts_no: float | None = None
    # Distributions & scorelines
    home_goals_dist: list[float] | None = None
    away_goals_dist: list[float] | None = None
    top_scorelines: list | None = None
    # Corner predictions (may be low-confidence — see corners_confidence)
    expected_home_corners: float | None = None
    expected_away_corners: float | None = None
    expected_total_corners: float | None = None
    corners_confidence: str | None = None
    confidence: str
    data_quality: float | None
    fair_odds_home: float | None
    fair_odds_draw: float | None
    fair_odds_away: float | None
    model_version: str
    published_at: datetime | None
    # Blend provenance
    blend_mode: str | None = None
    model_probs: ModelProbs | None = None
    market_probs: MarketProbs | None = None
    # Optional actual result (if match finished)
    actual_home_goals: int | None = None
    actual_away_goals: int | None = None
    result_correct: bool | None = None
    brier_score: float | None = None


class PerformanceSummary(BaseModel):
    model_version: str
    predictions_total: int
    predictions_scored: int
    result_accuracy: float | None
    over_under_accuracy: float | None
    avg_brier_score: float | None
    avg_log_loss: float | None
    # Comparison to public baselines
    baseline_random: float = 1 / 3
    baseline_always_home: float | None = None
    # Rolling windows
    last_7_days: dict | None = None
    last_30_days: dict | None = None


class UpcomingPredictionsList(BaseModel):
    items: list[PredictionResponse]
    total: int


class SourceReliabilityItem(BaseModel):
    slug: str
    name: str
    kind: str
    enabled: bool
    predictions_scored: int | None = None
    result_accuracy: float | None = None
    avg_brier_score: float | None = None
    reliability_weight: float | None = None
    computed_at: datetime | None = None
    low_sample: bool = True


class SourceReliabilityList(BaseModel):
    window_days: int
    items: list[SourceReliabilityItem]
