"""Extended scoreline predictions from Dixon-Coles.

Given a fit and a match pair, produce:

1. W/D/L (existing)
2. Over/Under at multiple thresholds:
   - 1.5 goals total
   - 2.5 goals total (already in base model)
   - 3.5 goals total
3. Team-to-score distribution (marginals):
   - P(Home scores 0 goals), P(1), P(2), P(3+)
   - Same for away
4. Both-teams-to-score (BTTS):
   - P(Home ≥ 1 AND Away ≥ 1)
5. Most likely scorelines (top 3)

All values come from the SAME score matrix — no extra model
parameters, no additional training. Pure math on the Dixon-Coles
distribution.

For the corner signal (deferred), see corners.py (Phase 10.11).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import poisson

from packages.prediction.dixon_coles import (
    DEFAULT_MAX_GOALS,
    FitResult,
    _dc_tau,
    predict_match,
)


@dataclass
class ScorelinePrediction:
    # Basic (same as Prediction)
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    expected_home_goals: float
    expected_away_goals: float
    # Over/Under at multiple thresholds
    prob_over_1_5: float
    prob_under_1_5: float
    prob_over_2_5: float
    prob_under_2_5: float
    prob_over_3_5: float
    prob_under_3_5: float
    # Both teams to score
    prob_btts_yes: float
    prob_btts_no: float
    # Team-to-score marginals
    home_goals_dist: list[float] = field(default_factory=list)  # [P(0), P(1), P(2), P(3+)]
    away_goals_dist: list[float] = field(default_factory=list)
    # Top 3 most likely scorelines [(home, away, prob), ...]
    top_scorelines: list[tuple[int, int, float]] = field(default_factory=list)


def predict_scoreline(
    fit: FitResult,
    *,
    home_team: str,
    away_team: str,
    max_goals: int = DEFAULT_MAX_GOALS,
) -> ScorelinePrediction:
    """Build the full score matrix and derive every margin."""
    import math

    # Same λ derivation as predict_match — do NOT diverge
    lam_home = math.exp(
        fit.attack[home_team] + fit.defense[away_team] + fit.home_advantage
    )
    lam_away = math.exp(
        fit.attack[away_team] + fit.defense[home_team]
    )
    lam_home = max(min(lam_home, 6.0), 0.1)
    lam_away = max(min(lam_away, 6.0), 0.1)

    # Build the score matrix
    m = np.zeros((max_goals + 1, max_goals + 1))
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            p = poisson.pmf(x, lam_home) * poisson.pmf(y, lam_away)
            tau = _dc_tau(x, y, lam_home, lam_away, fit.rho)
            m[x, y] = p * tau

    # Normalize (should already sum near 1)
    total = m.sum()
    if total > 0:
        m /= total

    # --- W/D/L ---
    p_home = float(np.sum(np.tril(m, -1).T))
    p_draw = float(np.sum(np.diag(m)))
    p_away = float(np.sum(np.triu(m, 1).T))

    # --- Over/Under at each threshold ---
    goals_grid = np.add.outer(np.arange(max_goals + 1), np.arange(max_goals + 1))

    p_over_1_5 = float(np.sum(m[goals_grid >= 2]))
    p_under_1_5 = float(np.sum(m[goals_grid <= 1]))

    p_over_2_5 = float(np.sum(m[goals_grid >= 3]))
    p_under_2_5 = float(np.sum(m[goals_grid <= 2]))

    p_over_3_5 = float(np.sum(m[goals_grid >= 4]))
    p_under_3_5 = float(np.sum(m[goals_grid <= 3]))

    # --- Both teams to score ---
    # P(home >= 1 AND away >= 1)
    p_btts_yes = float(np.sum(m[1:, 1:]))
    p_btts_no = 1.0 - p_btts_yes

    # --- Team-to-score marginals (0, 1, 2, 3+) ---
    # Marginalize over the other team
    home_marginal = m.sum(axis=1)  # marginal of home goals
    away_marginal = m.sum(axis=0)  # marginal of away goals

    home_dist = [
        float(home_marginal[0]),                       # 0 goals
        float(home_marginal[1]),                       # 1 goal
        float(home_marginal[2]),                       # 2 goals
        float(np.sum(home_marginal[3:])),              # 3+ goals
    ]
    away_dist = [
        float(away_marginal[0]),
        float(away_marginal[1]),
        float(away_marginal[2]),
        float(np.sum(away_marginal[3:])),
    ]

    # --- Top 3 most likely scorelines ---
    flat = m.flatten()
    top_idx = np.argsort(flat)[-3:][::-1]
    top_scorelines: list[tuple[int, int, float]] = []
    for idx in top_idx:
        x, y = np.unravel_index(idx, m.shape)
        top_scorelines.append((int(x), int(y), float(m[x, y])))

    return ScorelinePrediction(
        prob_home_win=p_home,
        prob_draw=p_draw,
        prob_away_win=p_away,
        expected_home_goals=lam_home,
        expected_away_goals=lam_away,
        prob_over_1_5=p_over_1_5,
        prob_under_1_5=p_under_1_5,
        prob_over_2_5=p_over_2_5,
        prob_under_2_5=p_under_2_5,
        prob_over_3_5=p_over_3_5,
        prob_under_3_5=p_under_3_5,
        prob_btts_yes=p_btts_yes,
        prob_btts_no=p_btts_no,
        home_goals_dist=home_dist,
        away_goals_dist=away_dist,
        top_scorelines=top_scorelines,
    )
