"""Multi-config backtest for V2.

Tests 5 configurations against held-out EPL matches:
  1. V1 (reference — no adjustments)
  2. V2 light:     form ±0.05, H2H ±0.05
  3. V2 medium:    form ±0.15, H2H ±0.15
  4. V2 fast-decay + medium: xi=0.02, form ±0.10, H2H ±0.10
  5. V2 fast-decay + light:  xi=0.03, form ±0.05, H2H ±0.05

Each config fits on the training window, predicts the test window,
scores with result accuracy + Brier + log loss.

We pick the winner by Brier score (proper scoring rule — prefers
well-calibrated probabilities, not just correct picks).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.prediction.dixon_coles import MatchRecord, fit_model, predict_match
from packages.prediction.scoring import score_prediction


@dataclass
class ConfigResult:
    name: str
    xi: float
    form_cap: float
    h2h_cap: float
    n: int
    accuracy: float
    brier: float
    log_loss: float


async def _load_all(session: AsyncSession, competition: str) -> list[MatchRecord]:
    rows = (await session.execute(sql_text("""
        SELECT home_team, away_team, home_goals, away_goals, kickoff_at
        FROM matches
        WHERE status = 'finished'
          AND competition = :comp
          AND home_goals IS NOT NULL
        ORDER BY kickoff_at ASC
    """), {"comp": competition})).all()
    return [MatchRecord(home_team=r[0], away_team=r[1], home_goals=r[2],
                        away_goals=r[3], played_at=r[4]) for r in rows]


def _compute_form_residuals(train: list[MatchRecord], as_of: datetime) -> dict[str, dict]:
    """Last-5 residuals per team from training window."""
    by_team: dict[str, list[tuple[datetime, int, int]]] = {}
    for m in train:
        by_team.setdefault(m.home_team, []).append((m.played_at, m.home_goals, m.away_goals))
        by_team.setdefault(m.away_team, []).append((m.played_at, m.away_goals, m.home_goals))

    residuals = {}
    for team, matches in by_team.items():
        matches.sort(key=lambda x: x[0])
        all_gf = [m[1] for m in matches]
        all_ga = [m[2] for m in matches]
        if len(all_gf) < 6:
            continue
        baseline_gf = sum(all_gf[:-5]) / max(len(all_gf) - 5, 1)
        baseline_ga = sum(all_ga[:-5]) / max(len(all_ga) - 5, 1)
        recent_gf = sum(all_gf[-5:]) / 5
        recent_ga = sum(all_ga[-5:]) / 5
        residuals[team] = {
            "attack": recent_gf - baseline_gf,
            "defense": recent_ga - baseline_ga,
        }
    return residuals


def _compute_h2h_multipliers(train: list[MatchRecord]) -> dict[tuple[str, str], float]:
    """Last-5 H2H multiplier per (home, away) pair."""
    from collections import defaultdict
    pairs: dict = defaultdict(list)
    for m in train:
        pairs[(m.home_team, m.away_team)].append((m.played_at, m.home_goals, m.away_goals))
    out: dict[tuple[str, str], float] = {}
    for key, meetings in pairs.items():
        meetings.sort(key=lambda x: x[0])
        last5 = meetings[-5:]
        if not last5:
            continue
        hgf = sum(m[1] for m in last5) / len(last5)
        agf = sum(m[2] for m in last5) / len(last5)
        if hgf + agf == 0:
            m_ = 1.0
        else:
            imbalance = (hgf - agf) / (hgf + agf)
            m_ = 1.0 + 0.3 * imbalance
            m_ = max(0.7, min(1.3, m_))
        out[key] = m_
    return out


def _apply_v2_adjustments(
    base_home_xg: float,
    base_away_xg: float,
    *,
    home_form: dict | None,
    away_form: dict | None,
    h2h_mult: float | None,
    form_cap: float,
    h2h_cap: float,
) -> tuple[float, float]:
    """Apply form + H2H adjustments bounded by caps."""
    import math
    lam_home = base_home_xg
    lam_away = base_away_xg

    # Form
    if home_form and away_form:
        home_signal = 0.5 * home_form["attack"] - 0.5 * away_form["defense"]
        away_signal = 0.5 * away_form["attack"] - 0.5 * home_form["defense"]
        home_shift = max(-form_cap, min(form_cap, home_signal * 0.5))
        away_shift = max(-form_cap, min(form_cap, away_signal * 0.5))
        lam_home *= math.exp(home_shift)
        lam_away *= math.exp(away_shift)

    # H2H
    if h2h_mult is not None:
        capped = max(1.0 - h2h_cap, min(1.0 + h2h_cap, h2h_mult))
        lam_home *= capped
        lam_away /= capped

    lam_home = max(0.1, min(6.0, lam_home))
    lam_away = max(0.1, min(6.0, lam_away))
    return lam_home, lam_away


def _pred_from_lambdas(lam_home: float, lam_away: float, rho: float) -> tuple[float, float, float, float, float]:
    """Rebuild score matrix, return (p_home, p_draw, p_away, p_over, p_under)."""
    import numpy as np
    from scipy.stats import poisson
    from packages.prediction.dixon_coles import _dc_tau, DEFAULT_MAX_GOALS

    m = np.zeros((DEFAULT_MAX_GOALS + 1, DEFAULT_MAX_GOALS + 1))
    for x in range(DEFAULT_MAX_GOALS + 1):
        for y in range(DEFAULT_MAX_GOALS + 1):
            p = poisson.pmf(x, lam_home) * poisson.pmf(y, lam_away)
            tau = _dc_tau(x, y, lam_home, lam_away, rho)
            m[x, y] = p * tau
    m /= m.sum()
    p_h = float(np.sum(np.tril(m, -1).T))
    p_d = float(np.sum(np.diag(m)))
    p_a = float(np.sum(np.triu(m, 1).T))
    tg = np.add.outer(np.arange(DEFAULT_MAX_GOALS + 1), np.arange(DEFAULT_MAX_GOALS + 1))
    p_over = float(np.sum(m[tg >= 3]))
    p_under = float(np.sum(m[tg <= 2]))
    return p_h, p_d, p_a, p_over, p_under


async def run_config(
    session: AsyncSession,
    *,
    name: str,
    xi: float,
    form_cap: float,
    h2h_cap: float,
    train: list[MatchRecord],
    test: list[MatchRecord],
    cutoff: datetime,
) -> ConfigResult:
    """Run one config. form_cap=0 disables adjustments (V1 baseline)."""
    # Fit with given xi
    fit = fit_model(train, as_of=cutoff, xi=xi)

    # Compute adjustments
    form_res = _compute_form_residuals(train, cutoff) if form_cap > 0 else {}
    h2h_mults = _compute_h2h_multipliers(train) if h2h_cap > 0 else {}

    n = 0
    correct = 0
    brier = 0.0
    log_loss = 0.0

    for m in test:
        if m.home_team not in fit.attack or m.away_team not in fit.attack:
            continue
        try:
            base = predict_match(fit, home_team=m.home_team, away_team=m.away_team)

            if form_cap > 0 or h2h_cap > 0:
                hf = form_res.get(m.home_team)
                af = form_res.get(m.away_team)
                hm = h2h_mults.get((m.home_team, m.away_team))

                lam_h, lam_a = _apply_v2_adjustments(
                    base.expected_home_goals, base.expected_away_goals,
                    home_form=hf, away_form=af, h2h_mult=hm,
                    form_cap=form_cap, h2h_cap=h2h_cap,
                )
                p_h, p_d, p_a, p_over, p_under = _pred_from_lambdas(lam_h, lam_a, fit.rho)
            else:
                p_h, p_d, p_a = base.prob_home_win, base.prob_draw, base.prob_away_win
                p_over, p_under = base.prob_over_2_5, base.prob_under_2_5

            s = score_prediction(
                prob_home_win=p_h, prob_draw=p_d, prob_away_win=p_a,
                prob_over_2_5=p_over,
                actual_home_goals=m.home_goals, actual_away_goals=m.away_goals,
            )
            correct += int(s.result_correct)
            brier += s.brier_score
            log_loss += s.log_loss
            n += 1
        except Exception:
            continue

    return ConfigResult(
        name=name, xi=xi, form_cap=form_cap, h2h_cap=h2h_cap, n=n,
        accuracy=correct / n if n else 0,
        brier=brier / n if n else 0,
        log_loss=log_loss / n if n else 0,
    )


async def main():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from packages.core.config import get_settings
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        all_matches = await _load_all(session, "EPL")
        cutoff = datetime.now(UTC) - timedelta(days=180)
        train = [m for m in all_matches if m.played_at < cutoff]
        test = [m for m in all_matches if m.played_at >= cutoff]
        print(f"Train: {len(train)}, Test: {len(test)}")
        print()

        configs = [
            ("V1 (reference)",        0.005, 0.00, 0.00),
            ("V2 light",              0.005, 0.05, 0.05),
            ("V2 medium",             0.005, 0.15, 0.15),
            ("V2 fast-decay medium",  0.020, 0.10, 0.10),
            ("V2 fast-decay light",   0.030, 0.05, 0.05),
            ("V2 very fast + light",  0.050, 0.05, 0.05),
        ]

        results = []
        for name, xi, fc, hc in configs:
            print(f"Running {name} (xi={xi}, form_cap={fc}, h2h_cap={hc})...")
            r = await run_config(
                session, name=name, xi=xi, form_cap=fc, h2h_cap=hc,
                train=train, test=test, cutoff=cutoff,
            )
            results.append(r)
            print(f"   accuracy={r.accuracy:.4f}  brier={r.brier:.4f}  logloss={r.log_loss:.4f}")
            print()

        print("=" * 80)
        print(f"{'Config':<28} {'N':>4}  {'Accuracy':>10}  {'Brier':>10}  {'LogLoss':>10}")
        print("-" * 80)
        for r in results:
            print(f"{r.name:<28} {r.n:>4}  {r.accuracy:>9.2%}  {r.brier:>10.4f}  {r.log_loss:>10.4f}")
        print()

        # Winner by Brier (lower is better)
        winner = min(results, key=lambda r: r.brier)
        print(f"🏆 Winner by Brier score: {winner.name}")
        print(f"   accuracy={winner.accuracy:.2%}  brier={winner.brier:.4f}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
