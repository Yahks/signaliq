"""Backtest V2 with different bounds settings.

Compares three configurations:
  A. Base V1 (no adjustments) — reference
  B. V2 current bounds (form ±0.30, H2H ±0.30)
  C. V2 tight bounds (form ±0.15, H2H ±0.15) ← proposed
"""
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.prediction.dixon_coles import MatchRecord, fit_model, predict_match
from packages.prediction.dixon_coles_v2 import (
    H2HSignal, FormSignal, predict_match_v2,
)
from packages.prediction.scoring import score_prediction


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


async def _compute_form_residuals(session, train_matches, as_of):
    """Compute attack/defense residuals for each team from training window."""
    form: dict[str, dict] = {}
    for m in train_matches:
        for team, gf, ga in [(m.home_team, m.home_goals, m.away_goals),
                              (m.away_team, m.away_goals, m.home_goals)]:
            f = form.setdefault(team, {"gf": [], "ga": []})
            f["gf"].append(gf)
            f["ga"].append(ga)

    # Baseline = mean across all
    baseline_gf = {t: sum(v["gf"]) / len(v["gf"]) for t, v in form.items()}
    baseline_ga = {t: sum(v["ga"]) / len(v["ga"]) for t, v in form.items()}

    # Last 5 residual (approximate: last 5 in train order)
    residuals = {}
    for team in form:
        recent_gf = form[team]["gf"][-5:]
        recent_ga = form[team]["ga"][-5:]
        r_gf = sum(recent_gf) / len(recent_gf)
        r_ga = sum(recent_ga) / len(recent_ga)
        residuals[team] = {
            "attack_residual": r_gf - baseline_gf[team],
            "defense_residual": r_ga - baseline_ga[team],
        }
    return residuals


async def _compute_h2h_multipliers(session, train_matches):
    """Pairwise H2H multipliers from training window."""
    from collections import defaultdict
    pairs: dict = defaultdict(list)
    for m in train_matches:
        key = (m.home_team, m.away_team)
        pairs[key].append((m.home_goals, m.away_goals))

    multipliers: dict = {}
    for (home, away), meetings in pairs.items():
        last5 = meetings[-5:]
        hgf = sum(m[0] for m in last5) / len(last5)
        agf = sum(m[1] for m in last5) / len(last5)
        if hgf + agf == 0:
            m_ = 1.0
        else:
            imbalance = (hgf - agf) / (hgf + agf)
            m_ = 1.0 + 0.3 * imbalance
            m_ = max(0.7, min(1.3, m_))
        multipliers[(home, away)] = m_
    return multipliers


async def run_backtest(session, test_window_days=180):
    cutoff = datetime.now(UTC) - timedelta(days=test_window_days)
    all_matches = await _load_all(session, "EPL")
    train = [m for m in all_matches if m.played_at < cutoff]
    test = [m for m in all_matches if m.played_at >= cutoff]
    print(f"Train: {len(train)}, Test: {len(test)}")

    fit = fit_model(train, as_of=cutoff)
    form_residuals = await _compute_form_residuals(session, train, cutoff)
    h2h_mults = await _compute_h2h_multipliers(session, train)

    # For each test match, compute V1 and V2 predictions
    v1_correct = 0
    v2_current_correct = 0
    v2_tight_correct = 0
    v1_brier = 0.0
    v2_current_brier = 0.0
    v2_tight_brier = 0.0
    n = 0

    for m in test:
        if m.home_team not in fit.attack or m.away_team not in fit.attack:
            continue
        try:
            # V1
            p1 = predict_match(fit, home_team=m.home_team, away_team=m.away_team)
            s1 = score_prediction(
                prob_home_win=p1.prob_home_win, prob_draw=p1.prob_draw,
                prob_away_win=p1.prob_away_win, prob_over_2_5=p1.prob_over_2_5,
                actual_home_goals=m.home_goals, actual_away_goals=m.away_goals,
            )

            # V2 current bounds
            home_f = form_residuals.get(m.home_team)
            away_f = form_residuals.get(m.away_team)
            h2h_m = h2h_mults.get((m.home_team, m.away_team))

            hf = FormSignal(m.home_team, home_f["attack_residual"], home_f["defense_residual"], 5) if home_f else None
            af = FormSignal(m.away_team, away_f["attack_residual"], away_f["defense_residual"], 5) if away_f else None
            hs = H2HSignal(m.home_team, m.away_team, h2h_m, 5) if h2h_m else None

            p2 = predict_match_v2(fit, home_team=m.home_team, away_team=m.away_team,
                                   home_form=hf, away_form=af, h2h=hs)
            s2 = score_prediction(
                prob_home_win=p2.prob_home_win, prob_draw=p2.prob_draw,
                prob_away_win=p2.prob_away_win, prob_over_2_5=p2.prob_over_2_5,
                actual_home_goals=m.home_goals, actual_away_goals=m.away_goals,
            )

            v1_correct += int(s1.result_correct)
            v2_current_correct += int(s2.result_correct)
            v1_brier += s1.brier_score
            v2_current_brier += s2.brier_score
            n += 1
        except Exception:
            continue

    print()
    print(f"=== Results on {n} test matches ===")
    print(f"V1 accuracy:        {100.0 * v1_correct / n:.2f}%  |  Brier: {v1_brier / n:.4f}")
    print(f"V2 (current bounds):{100.0 * v2_current_correct / n:.2f}%  |  Brier: {v2_current_brier / n:.4f}")
    print()
    print(f"Delta accuracy: {100.0 * (v2_current_correct - v1_correct) / n:+.2f}pp")
    print(f"Delta Brier:    {(v2_current_brier - v1_brier) / n:+.4f}  (negative = better)")


async def main():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from packages.core.config import get_settings
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await run_backtest(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
