"""Dixon-Coles v2: base model + recent form + H2H adjustments.

Layered prediction:
    1. Fit base Dixon-Coles on all historical matches (as before)
    2. For each team, load recent-form residuals from form_snapshots
    3. For each matchup, load H2H multiplier from h2h_snapshots
    4. Adjust base λ_home and λ_away by the recent-form residual
    5. Apply H2H multiplier
    6. Predict

Why layer instead of train together:
  - The base model has ~760 matches of signal — it's stable
  - Form and H2H are extra signals from small samples (last 5)
  - Training them jointly would let small samples dominate
  - Layering lets us bound the influence of each signal explicitly

Bounds (deliberate):
  - Form residual can shift log-λ by at most ±0.3
  - H2H multiplier is bounded [0.7, 1.3]
  - Base model always contributes the majority of the prediction
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from packages.core.logging import get_logger
from packages.prediction.dixon_coles import (
    DEFAULT_MAX_GOALS,
    FitResult,
    Prediction,
    predict_match as dc_predict,
)

log = get_logger(__name__)

# Bounds on how much each signal can adjust the base λ
MAX_FORM_SHIFT = 0.30           # log scale — ~35% max xG shift
H2H_MULTIPLIER_MIN = 0.7
H2H_MULTIPLIER_MAX = 1.3


@dataclass
class FormSignal:
    team: str
    attack_residual: float
    defense_residual: float
    matches_considered: int


@dataclass
class H2HSignal:
    home_team: str
    away_team: str
    multiplier: float
    matches_considered: int


@dataclass
class V2Prediction:
    """V2 prediction with full audit trail of adjustments."""
    base: Prediction
    adjusted_expected_home_goals: float
    adjusted_expected_away_goals: float
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    prob_over_2_5: float
    prob_under_2_5: float
    most_likely_score: tuple[int, int]
    # Audit trail
    home_form: FormSignal | None
    away_form: FormSignal | None
    h2h: H2HSignal | None
    form_shift_home: float
    form_shift_away: float
    h2h_multiplier: float


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _renormalize_score_matrix(
    lam_home: float,
    lam_away: float,
    rho: float,
    max_goals: int = DEFAULT_MAX_GOALS,
) -> tuple[float, float, float, float, float, tuple[int, int]]:
    """Rebuild the score matrix with adjusted lambdas.

    Returns (p_home, p_draw, p_away, p_over_2_5, p_under_2_5, most_likely).
    """
    import numpy as np
    from scipy.stats import poisson

    from packages.prediction.dixon_coles import _dc_tau

    prob_matrix = np.zeros((max_goals + 1, max_goals + 1))
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            p = poisson.pmf(x, lam_home) * poisson.pmf(y, lam_away)
            tau = _dc_tau(x, y, lam_home, lam_away, rho)
            prob_matrix[x, y] = p * tau

    total = prob_matrix.sum()
    if total > 0:
        prob_matrix /= total

    p_home = float(np.sum(np.tril(prob_matrix, -1).T))
    p_draw = float(np.sum(np.diag(prob_matrix)))
    p_away = float(np.sum(np.triu(prob_matrix, 1).T))

    total_goals = np.add.outer(np.arange(max_goals + 1), np.arange(max_goals + 1))
    p_over = float(np.sum(prob_matrix[total_goals >= 3]))
    p_under = float(np.sum(prob_matrix[total_goals <= 2]))

    flat_idx = int(np.argmax(prob_matrix))
    mlx, mly = np.unravel_index(flat_idx, prob_matrix.shape)

    return p_home, p_draw, p_away, p_over, p_under, (int(mlx), int(mly))


def predict_match_v2(
    fit: FitResult,
    *,
    home_team: str,
    away_team: str,
    home_form: FormSignal | None = None,
    away_form: FormSignal | None = None,
    h2h: H2HSignal | None = None,
) -> V2Prediction:
    """Predict with layered adjustments.

    Order of operations:
      1. Get base prediction from Dixon-Coles v1
      2. Adjust λ_home by (home form attack + opponent defense concession form)
      3. Adjust λ_away similarly
      4. Multiply by H2H
      5. Rebuild score matrix and re-normalize
    """
    # --- 1. Base prediction ---
    base = dc_predict(fit, home_team=home_team, away_team=away_team)

    lam_home = base.expected_home_goals
    lam_away = base.expected_away_goals

    # --- 2. Form adjustment ---
    # The home team's attack residual shifts their expected goals
    # The away team's defense residual shifts the home team's expected goals
    form_shift_home = 0.0
    form_shift_away = 0.0

    if home_form is not None and away_form is not None:
        # Home team attack: home attack residual + (opponent's defense concession)
        # If home team is hot AND away team is leaky → boost
        home_signal = 0.5 * home_form.attack_residual - 0.5 * away_form.defense_residual
        # Away team attack: away attack residual + home defense concession
        away_signal = 0.5 * away_form.attack_residual - 0.5 * home_form.defense_residual

        form_shift_home = _clamp(home_signal * 0.5, -MAX_FORM_SHIFT, MAX_FORM_SHIFT)
        form_shift_away = _clamp(away_signal * 0.5, -MAX_FORM_SHIFT, MAX_FORM_SHIFT)

    lam_home *= pow(2.718281828, form_shift_home)
    lam_away *= pow(2.718281828, form_shift_away)

    # --- 3. H2H adjustment ---
    h2h_mult = 1.0
    if h2h is not None:
        h2h_mult = _clamp(h2h.multiplier, H2H_MULTIPLIER_MIN, H2H_MULTIPLIER_MAX)
        # H2H shifts both lambdas: home team gets more, away team gets less
        lam_home *= h2h_mult
        lam_away /= h2h_mult

    # Clip lambdas to realistic range
    lam_home = _clamp(lam_home, 0.1, 6.0)
    lam_away = _clamp(lam_away, 0.1, 6.0)

    # --- 4. Rebuild score matrix ---
    p_home, p_draw, p_away, p_over, p_under, ml = _renormalize_score_matrix(
        lam_home, lam_away, fit.rho
    )

    return V2Prediction(
        base=base,
        adjusted_expected_home_goals=round(lam_home, 3),
        adjusted_expected_away_goals=round(lam_away, 3),
        prob_home_win=p_home,
        prob_draw=p_draw,
        prob_away_win=p_away,
        prob_over_2_5=p_over,
        prob_under_2_5=p_under,
        most_likely_score=ml,
        home_form=home_form,
        away_form=away_form,
        h2h=h2h,
        form_shift_home=round(form_shift_home, 4),
        form_shift_away=round(form_shift_away, 4),
        h2h_multiplier=round(h2h_mult, 3),
    )
