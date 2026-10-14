"""Score a prediction against the actual result.

Produces:
  - result_correct (bool) — did we call W/D/L right?
  - over_under_correct (bool)
  - brier_score — sum of squared errors across W/D/L probabilities
                  (0 = perfect, 2 = worst possible for 3-class)
  - log_loss — negative log of the probability we assigned to the true outcome
  - All values stored in prediction_results
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ScoringResult:
    actual_result: str          # "home_win" | "draw" | "away_win"
    actual_home_goals: int
    actual_away_goals: int
    actual_total_goals: int
    actual_over_2_5: bool
    result_correct: bool
    over_under_correct: bool
    brier_score: float
    log_loss: float


def score_prediction(
    *,
    prob_home_win: float,
    prob_draw: float,
    prob_away_win: float,
    prob_over_2_5: float,
    actual_home_goals: int,
    actual_away_goals: int,
) -> ScoringResult:
    """Compare a prediction against the actual result."""
    # Determine actual outcome
    if actual_home_goals > actual_away_goals:
        actual_result = "home_win"
        true_probs = (1.0, 0.0, 0.0)
    elif actual_home_goals < actual_away_goals:
        actual_result = "away_win"
        true_probs = (0.0, 0.0, 1.0)
    else:
        actual_result = "draw"
        true_probs = (0.0, 1.0, 0.0)

    # Was the top prediction correct?
    predicted_idx = max(
        range(3),
        key=lambda i: (prob_home_win, prob_draw, prob_away_win)[i],
    )
    actual_idx = {"home_win": 0, "draw": 1, "away_win": 2}[actual_result]
    result_correct = predicted_idx == actual_idx

    # Brier score (multi-class): sum((p_i - y_i)^2)
    brier = (
        (prob_home_win - true_probs[0]) ** 2
        + (prob_draw - true_probs[1]) ** 2
        + (prob_away_win - true_probs[2]) ** 2
    )

    # Log loss: -log(probability assigned to the true outcome)
    p_true = (prob_home_win, prob_draw, prob_away_win)[actual_idx]
    p_true = max(p_true, 1e-15)  # avoid log(0)
    log_loss_value = -math.log(p_true)

    # Over/Under 2.5
    actual_total = actual_home_goals + actual_away_goals
    actual_over_2_5 = actual_total >= 3
    predicted_over = prob_over_2_5 >= 0.5
    over_under_correct = predicted_over == actual_over_2_5

    return ScoringResult(
        actual_result=actual_result,
        actual_home_goals=actual_home_goals,
        actual_away_goals=actual_away_goals,
        actual_total_goals=actual_total,
        actual_over_2_5=actual_over_2_5,
        result_correct=result_correct,
        over_under_correct=over_under_correct,
        brier_score=brier,
        log_loss=log_loss_value,
    )
