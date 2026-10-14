"""Backtest corners as a predictive signal.

Compares:
  1. V1 baseline (Dixon-Coles only)
  2. V1 + corners signal (adds corner-derived residual to attack/defense)

Corners residual logic:
  - Compute per-team corners_for and corners_against (baseline + last 5)
  - A team whose corners_for is ABOVE baseline is "generating pressure" → boost attack
  - A team whose corners_against is ABOVE baseline is "under pressure" → reduce defense

Test window: 2024-25 season. Train on 2022-23 + 2023-24.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.prediction.dixon_coles import MatchRecord, fit_model, predict_match
from packages.prediction.scoring import score_prediction


async def _load_matches(session: AsyncSession) -> list[MatchRecord]:
    rows = (await session.execute(sql_text("""
        SELECT home_team, away_team, home_goals, away_goals, kickoff_at
        FROM matches
        WHERE status = 'finished' AND competition = 'EPL' AND home_goals IS NOT NULL
        ORDER BY kickoff_at ASC
    """))).all()
    return [MatchRecord(home_team=r[0], away_team=r[1], home_goals=r[2],
                        away_goals=r[3], played_at=r[4]) for r in rows]


async def _load_corners_priors(session: AsyncSession, cutoff: datetime) -> dict[str, dict]:
    """For each team, compute corners_for and corners_against from data BEFORE cutoff."""
    rows = (await session.execute(sql_text("""
        SELECT
            home_team, away_team, home_corners, away_corners
        FROM match_corners
        WHERE kickoff_at < :cutoff
          AND home_corners IS NOT NULL
    """), {"cutoff": cutoff})).all()

    by_team: dict[str, dict] = {}
    for home, away, hc, ac in rows:
        by_team.setdefault(home, {"cf": [], "ca": []})
        by_team.setdefault(away, {"cf": [], "ca": []})
        by_team[home]["cf"].append(hc)
        by_team[home]["ca"].append(ac)
        by_team[away]["cf"].append(ac)
        by_team[away]["ca"].append(hc)

    out = {}
    for team, d in by_team.items():
        out[team] = {
            "cf": sum(d["cf"]) / len(d["cf"]),
            "ca": sum(d["ca"]) / len(d["ca"]),
            "n": len(d["cf"]),
        }
    return out


async def main():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from packages.core.config import get_settings
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        all_matches = await _load_matches(session)
        # Cutoff: 2025-08-01 (start of 2025-26 season)
        cutoff = datetime(2025, 8, 1, tzinfo=UTC)
        train = [m for m in all_matches if m.played_at < cutoff]
        test = [m for m in all_matches if m.played_at >= cutoff]
        print(f"Train: {len(train)}, Test: {len(test)}")

        if len(test) < 20:
            print("⚠️  Not enough test matches. Using 2024-25 as test window.")
            # Alternative: test on last 180 days
            cutoff = max(m.played_at for m in all_matches) - timedelta(days=180)
            train = [m for m in all_matches if m.played_at < cutoff]
            test = [m for m in all_matches if m.played_at >= cutoff]
            print(f"Re-split: Train={len(train)}, Test={len(test)}")

        fit = fit_model(train)

        # Corners priors from training window
        corners_priors = await _load_corners_priors(session, cutoff)
        print(f"Teams with corners priors: {len(corners_priors)}")

        # Baseline corner averages (for residual calculation)
        all_cf = [v["cf"] for v in corners_priors.values()]
        all_ca = [v["ca"] for v in corners_priors.values()]
        mean_cf = sum(all_cf) / len(all_cf) if all_cf else 5.0
        mean_ca = sum(all_ca) / len(all_ca) if all_ca else 5.0
        print(f"League avg corners: for={mean_cf:.2f}, against={mean_ca:.2f}")
        print()

        # Backtest two configs
        v1_correct = v2_correct = 0
        v1_brier = v2_brier = 0.0
        v1_ll = v2_ll = 0.0
        n = 0

        # Corner effect strength (how much corners shift log-xG)
        CORNER_EFFECT = 0.05  # conservative

        for m in test:
            if m.home_team not in fit.attack or m.away_team not in fit.attack:
                continue
            try:
                # V1 baseline
                p1 = predict_match(fit, home_team=m.home_team, away_team=m.away_team)
                s1 = score_prediction(
                    prob_home_win=p1.prob_home_win, prob_draw=p1.prob_draw,
                    prob_away_win=p1.prob_away_win, prob_over_2_5=p1.prob_over_2_5,
                    actual_home_goals=m.home_goals, actual_away_goals=m.away_goals,
                )

                # V1 + corners
                # Compute shift from corner residuals
                import math
                home_prior = corners_priors.get(m.home_team, {"cf": mean_cf, "ca": mean_ca})
                away_prior = corners_priors.get(m.away_team, {"cf": mean_cf, "ca": mean_ca})

                # Home team's attack boosted if they generate many corners AND opponent concedes many
                home_corner_signal = 0.5 * (home_prior["cf"] - mean_cf) + 0.5 * (away_prior["ca"] - mean_ca)
                away_corner_signal = 0.5 * (away_prior["cf"] - mean_cf) + 0.5 * (home_prior["ca"] - mean_ca)

                home_shift = max(-0.15, min(0.15, home_corner_signal * CORNER_EFFECT))
                away_shift = max(-0.15, min(0.15, away_corner_signal * CORNER_EFFECT))

                lam_h = p1.expected_home_goals * math.exp(home_shift)
                lam_a = p1.expected_away_goals * math.exp(away_shift)

                # Rebuild score matrix
                import numpy as np
                from scipy.stats import poisson
                from packages.prediction.dixon_coles import _dc_tau, DEFAULT_MAX_GOALS

                mtx = np.zeros((DEFAULT_MAX_GOALS + 1, DEFAULT_MAX_GOALS + 1))
                for x in range(DEFAULT_MAX_GOALS + 1):
                    for y in range(DEFAULT_MAX_GOALS + 1):
                        pr = poisson.pmf(x, lam_h) * poisson.pmf(y, lam_a)
                        tau = _dc_tau(x, y, lam_h, lam_a, fit.rho)
                        mtx[x, y] = pr * tau
                mtx /= mtx.sum()

                p_h = float(np.sum(np.tril(mtx, -1).T))
                p_d = float(np.sum(np.diag(mtx)))
                p_a = float(np.sum(np.triu(mtx, 1).T))
                tg = np.add.outer(np.arange(DEFAULT_MAX_GOALS + 1), np.arange(DEFAULT_MAX_GOALS + 1))
                p_o = float(np.sum(mtx[tg >= 3]))

                s2 = score_prediction(
                    prob_home_win=p_h, prob_draw=p_d, prob_away_win=p_a,
                    prob_over_2_5=p_o,
                    actual_home_goals=m.home_goals, actual_away_goals=m.away_goals,
                )

                v1_correct += int(s1.result_correct)
                v2_correct += int(s2.result_correct)
                v1_brier += s1.brier_score
                v2_brier += s2.brier_score
                v1_ll += s1.log_loss
                v2_ll += s2.log_loss
                n += 1
            except Exception:
                continue

        print(f"=== Results on {n} test matches ===")
        print(f"V1 baseline:       {100.0 * v1_correct / n:.2f}%  Brier: {v1_brier / n:.4f}  LL: {v1_ll / n:.4f}")
        print(f"V1 + corners:      {100.0 * v2_correct / n:.2f}%  Brier: {v2_brier / n:.4f}  LL: {v2_ll / n:.4f}")
        print()
        print(f"Delta accuracy: {100.0 * (v2_correct - v1_correct) / n:+.2f}pp")
        print(f"Delta Brier:    {(v2_brier - v1_brier) / n:+.4f}  (negative = better)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
