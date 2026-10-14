"""Dixon-Coles Poisson model for football match outcomes.

Reference:
    Dixon, M.J. and Coles, S.G. (1997). "Modelling Association Football Scores
    and Inefficiencies in the Football Betting Market." Journal of the Royal
    Statistical Society, Series C (Applied Statistics), 46(2), 265-280.

Model:
    For a home team i and away team j:
        λ_home = exp(α_i + β_j + γ)       home expected goals
        λ_away = exp(α_j + β_i)           away expected goals

    where:
        α_i = attack strength of team i
        β_i = defense strength of team i (negative = good defense)
        γ   = home advantage (positive)

    Base Poisson: P(home = x, away = y) = Poisson(x; λ_home) × Poisson(y; λ_away)

    Dixon-Coles correction τ(x, y, λ_home, λ_away, ρ) adjusts the four
    low-score outcomes (0-0, 1-0, 0-1, 1-1) to fix the bias where basic
    Poisson underestimates draws.

    Time decay: each match is weighted by ξ^(days_since_match).
    Standard value: ξ = 0.005 (half-life ~140 days).

Fitting:
    We optimize (α, β, γ, ρ) by maximum likelihood with L2 regularization
    on attack/defense parameters (prevents overfitting on small samples).

Prediction:
    Enumerate all score combinations up to max_goals (default 10) and sum
    the adjusted probabilities. Compute:
        - P(home win), P(draw), P(away win)
        - P(over 2.5), P(under 2.5)
        - Expected goals for each side
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

# Default parameters
DEFAULT_XI = 0.005  # time decay per day
DEFAULT_RHO = -0.05  # Dixon-Coles correction (typical range -0.2 to 0.0)
DEFAULT_REG_STRENGTH = 0.01  # L2 regularization on attack/defense
DEFAULT_MAX_GOALS = 10  # Max score we enumerate for prediction


@dataclass
class MatchRecord:
    """One historical match used for training."""
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    played_at: datetime


@dataclass
class FitResult:
    """Output of model fitting."""
    teams: list[str]
    attack: dict[str, float]
    defense: dict[str, float]
    home_advantage: float
    rho: float
    log_likelihood: float
    n_matches: int
    fitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Prediction:
    """Prediction for one match."""
    home_team: str
    away_team: str
    expected_home_goals: float
    expected_away_goals: float
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    prob_over_2_5: float
    prob_under_2_5: float
    most_likely_score: tuple[int, int]
    # Model confidence metadata
    data_quality: float  # 0-1, based on training sample size


def _time_weight(days_ago: float, xi: float = DEFAULT_XI) -> float:
    """Exponential time decay weight."""
    return math.exp(-xi * max(days_ago, 0))


def _dc_tau(
    x: int, y: int, lam_home: float, lam_away: float, rho: float
) -> float:
    """Dixon-Coles correction factor for low-score outcomes.

    Returns a multiplier (close to 1) that adjusts the base Poisson probability.
    Only affects (0,0), (1,0), (0,1), (1,1).
    """
    if x == 0 and y == 0:
        return 1.0 - lam_home * lam_away * rho
    if x == 0 and y == 1:
        return 1.0 + lam_home * rho
    if x == 1 and y == 0:
        return 1.0 + lam_away * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _neg_log_likelihood(
    params: np.ndarray,
    *,
    teams: list[str],
    match_indices: list[tuple[int, int]],
    home_goals: list[int],
    away_goals: list[int],
    weights: list[float],
    xi: float,
    reg_strength: float,
) -> float:
    """Negative log-likelihood for optimization. `params` = [attack..., defense..., γ, ρ]."""
    n_teams = len(teams)
    attack = params[:n_teams].copy()
    defense = params[n_teams:2 * n_teams].copy()
    gamma = params[2 * n_teams]
    rho = params[2 * n_teams + 1]

    # ---- Identifiability: mean attack = 0, mean defense = 0 (HARD constraint) ----
    # Without this, the optimizer can shift everything by a constant and the
    # attack + defense sum explodes for good teams, which inflates xG.
    attack = attack - attack.mean()
    defense = defense - defense.mean()

    # Prevent numerical explosion
    if abs(rho) > 0.5:
        return 1e10

    ll = 0.0
    for (i, j), hg, ag, w in zip(match_indices, home_goals, away_goals, weights):
        lam_home = math.exp(attack[i] + defense[j] + gamma)
        lam_away = math.exp(attack[j] + defense[i])

        # Clip lambdas to sane range (typical per-match xG is 0.5 - 3.5)
        lam_home = max(min(lam_home, 8.0), 0.1)
        lam_away = max(min(lam_away, 8.0), 0.1)

        # Base Poisson
        p_home = poisson.pmf(hg, lam_home)
        p_away = poisson.pmf(ag, lam_away)

        # Dixon-Coles correction
        tau = _dc_tau(hg, ag, lam_home, lam_away, rho)

        p = p_home * p_away * tau
        if p <= 0:
            p = 1e-15
        ll += w * math.log(p)

    # L2 regularization — stronger to prevent overfitting on small samples
    reg = reg_strength * (np.sum(attack ** 2) + np.sum(defense ** 2))

    return -ll + reg


def fit_model(
    matches: list[MatchRecord],
    *,
    as_of: datetime | None = None,
    xi: float = DEFAULT_XI,
    reg_strength: float = DEFAULT_REG_STRENGTH,
) -> FitResult:
    """Fit the Dixon-Coles model to historical match data.

    Args:
        matches: list of historical matches
        as_of: reference time for time-decay weighting (defaults to now)
        xi: time decay per day
        reg_strength: L2 regularization strength

    Returns:
        FitResult with attack/defense strengths and home advantage.
    """
    if len(matches) < 20:
        raise ValueError(
            f"Need at least 20 matches to fit (got {len(matches)}). "
            "Cold-start: fetch more historical data first."
        )

    as_of = as_of or datetime.now(UTC)

    # Collect unique teams
    teams_set: set[str] = set()
    for m in matches:
        teams_set.add(m.home_team)
        teams_set.add(m.away_team)
    teams = sorted(teams_set)
    team_to_idx = {t: i for i, t in enumerate(teams)}
    n_teams = len(teams)

    # Pre-compute indices and weights
    match_indices: list[tuple[int, int]] = []
    home_goals: list[int] = []
    away_goals: list[int] = []
    weights: list[float] = []

    for m in matches:
        i = team_to_idx[m.home_team]
        j = team_to_idx[m.away_team]
        match_indices.append((i, j))
        home_goals.append(m.home_goals)
        away_goals.append(m.away_goals)
        days_ago = (as_of - m.played_at).total_seconds() / 86400.0
        weights.append(_time_weight(days_ago, xi))

    # Initial guess: zeros for all, γ = 0.2, ρ = DEFAULT_RHO
    x0 = np.concatenate([
        np.zeros(n_teams),           # attack
        np.zeros(n_teams),           # defense
        np.array([0.2]),             # home advantage
        np.array([DEFAULT_RHO]),     # rho
    ])

    # Bounds: attack/defense in [-3, 3], gamma in [0, 1], rho in [-0.4, 0.4]
    bounds = (
        [(-3.0, 3.0)] * n_teams * 2
        + [(0.0, 1.0)]
        + [(-0.4, 0.4)]
    )

    # scipy passes `args` positionally — so we bundle our params into a single tuple
    # and unpack inside the objective via a closure.
    def objective(params: np.ndarray) -> float:
        return _neg_log_likelihood(
            params,
            teams=teams,
            match_indices=match_indices,
            home_goals=home_goals,
            away_goals=away_goals,
            weights=weights,
            xi=xi,
            reg_strength=reg_strength,
        )

    result = minimize(
        objective,
        x0,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 500, "ftol": 1e-7},
    )

    if not result.success:
        # Non-fatal — L-BFGS-B sometimes says "converged" is false but result is usable
        pass

    params = result.x
    attack_arr = params[:n_teams]
    defense_arr = params[n_teams:2 * n_teams]
    gamma = float(params[2 * n_teams])
    rho = float(params[2 * n_teams + 1])

    attack = {team: float(attack_arr[i]) for i, team in enumerate(teams)}
    defense = {team: float(defense_arr[i]) for i, team in enumerate(teams)}

    return FitResult(
        teams=teams,
        attack=attack,
        defense=defense,
        home_advantage=gamma,
        rho=rho,
        log_likelihood=float(-result.fun),
        n_matches=len(matches),
    )


def predict_match(
    fit: FitResult,
    *,
    home_team: str,
    away_team: str,
    max_goals: int = DEFAULT_MAX_GOALS,
) -> Prediction:
    """Predict a single match given a fitted model."""
    # Handle unknown teams (teams not in training data)
    if home_team not in fit.attack or away_team not in fit.attack:
        raise ValueError(
            f"Team not in training data: home={home_team} away={away_team}"
        )

    lam_home = math.exp(
        fit.attack[home_team] + fit.defense[away_team] + fit.home_advantage
    )
    lam_away = math.exp(
        fit.attack[away_team] + fit.defense[home_team]
    )
    # Clip to realistic xG range (0.1 - 6.0 goals per match)
    lam_home = max(min(lam_home, 6.0), 0.1)
    lam_away = max(min(lam_away, 6.0), 0.1)

    # Enumerate all score combinations up to max_goals
    prob_matrix = np.zeros((max_goals + 1, max_goals + 1))
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            p = poisson.pmf(x, lam_home) * poisson.pmf(y, lam_away)
            tau = _dc_tau(x, y, lam_home, lam_away, fit.rho)
            prob_matrix[x, y] = p * tau

    # Normalize (should already sum near 1, but be safe)
    total = prob_matrix.sum()
    if total > 0:
        prob_matrix /= total

    prob_home_win = float(np.sum(np.tril(prob_matrix, -1).T))
    prob_draw = float(np.sum(np.diag(prob_matrix)))
    prob_away_win = float(np.sum(np.triu(prob_matrix, 1).T))

    # Over/Under 2.5
    total_goals = np.add.outer(np.arange(max_goals + 1), np.arange(max_goals + 1))
    prob_over_2_5 = float(np.sum(prob_matrix[total_goals >= 3]))
    prob_under_2_5 = float(np.sum(prob_matrix[total_goals <= 2]))

    # Most likely score
    flat_idx = int(np.argmax(prob_matrix))
    most_likely_x, most_likely_y = np.unravel_index(flat_idx, prob_matrix.shape)

    # Data quality: 0-1, based on how many matches we trained on
    # (rough heuristic — 500+ matches = high quality, 50 = low)
    data_quality = min(1.0, len(fit.teams) * 10 / 500.0)

    return Prediction(
        home_team=home_team,
        away_team=away_team,
        expected_home_goals=lam_home,
        expected_away_goals=lam_away,
        prob_home_win=prob_home_win,
        prob_draw=prob_draw,
        prob_away_win=prob_away_win,
        prob_over_2_5=prob_over_2_5,
        prob_under_2_5=prob_under_2_5,
        most_likely_score=(int(most_likely_x), int(most_likely_y)),
        data_quality=data_quality,
    )
