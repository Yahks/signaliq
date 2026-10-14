"""Backtest different weightings of model + market predictions.

Approach:
  1. Fit Dixon-Coles on 2023-24 EPL season (or all matches before a cutoff)
  2. For each match in 2024-25:
     * Get our model's W/D/L from trained model
     * Get market's W/D/L from historical odds CSV
     * Compute blended W/D/L for various weight configurations
     * Score against the actual result
  3. Report which weight wins.

Weighting formula:
    p_blend = w_model * p_model + w_market * p_market
    where w_model + w_market = 1
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ingestion.sources.football_data_uk import fetch_season_odds
from packages.prediction.dixon_coles import MatchRecord, fit_model, predict_match
from packages.prediction.scoring import score_prediction


@dataclass
class BlendResult:
    w_model: float
    w_market: float
    n: int
    accuracy: float
    brier: float
    log_loss: float


async def _load_training(session, competition: str) -> list[MatchRecord]:
    """Load all matches for training (we'll fit on matches before the season we test)."""
    rows = (await session.execute(sql_text("""
        SELECT home_team, away_team, home_goals, away_goals, kickoff_at
        FROM matches
        WHERE status = 'finished' AND competition = :comp AND home_goals IS NOT NULL
        ORDER BY kickoff_at ASC
    """), {"comp": competition})).all()
    return [
        MatchRecord(home_team=r[0], away_team=r[1], home_goals=r[2],
                    away_goals=r[3], played_at=r[4])
        for r in rows
    ]


async def main():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        # Load training data (all EPL we have)
        training = await _load_training(session, "EPL")
        print(f"Training matches: {len(training)}")

        # Fetch historical odds for 2024-25
        print("Fetching 2024-25 EPL historical odds...")
        odds_rows = await fetch_season_odds(competition_code="EPL", season="2024-25")
        print(f"Got {len(odds_rows)} matches with market odds")

        # Fit the model on ALL our data
        fit = fit_model(training)
        print(f"Fitted model on {fit.n_matches} matches")

        # Match each historical-odds row to a Dixon-Coles prediction by team names
        # football-data.co.uk uses short names ("Man United") — our DB uses full ("Manchester United FC")
        # We need fuzzy matching
        from difflib import SequenceMatcher

        def _fuzzy_match(short_name: str, candidates: list[str]) -> str | None:
            """Match a short football-data.co.uk name to our DB full name."""
            # Common abbreviations
            aliases = {
                "Man United": "Manchester United FC",
                "Man City": "Manchester City FC",
                "Newcastle": "Newcastle United FC",
                "Nott'm Forest": "Nottingham Forest FC",
                "Nottm Forest": "Nottingham Forest FC",
                "Spurs": "Tottenham Hotspur FC",
                "Tottenham": "Tottenham Hotspur FC",
                "Brighton": "Brighton & Hove Albion FC",
                "Wolves": "Wolverhampton Wanderers FC",
                "West Ham": "West Ham United FC",
                "Leicester": "Leicester City FC",
                "Southampton": "Southampton FC",
                "Ipswich": "Ipswich Town FC",
                "Everton": "Everton FC",
                "Fulham": "Fulham FC",
                "Bournemouth": "AFC Bournemouth",
                "Crystal Palace": "Crystal Palace FC",
                "Aston Villa": "Aston Villa FC",
                "Chelsea": "Chelsea FC",
                "Arsenal": "Arsenal FC",
                "Liverpool": "Liverpool FC",
                "Brentford": "Brentford FC",
                "Burnley": "Burnley FC",
                "Sheffield United": "Sheffield United FC",
                "Luton": "Luton Town FC",
                "Leeds": "Leeds United FC",
            }
            if short_name in aliases:
                return aliases[short_name]
            # Fuzzy match against candidates
            best_score = 0.0
            best = None
            for c in candidates:
                # Ignore suffix " FC"
                c_short = c.replace(" FC", "").replace(" AFC", "")
                score = SequenceMatcher(None, short_name.lower(), c_short.lower()).ratio()
                if score > best_score:
                    best_score = score
                    best = c
            return best if best_score >= 0.7 else None

        team_candidates = list(fit.attack.keys())

        # For each historical match, get model and market predictions
        match_pairs: list[dict] = []
        unmatched = 0

        for row in odds_rows:
            home_full = _fuzzy_match(row.home_team, team_candidates)
            away_full = _fuzzy_match(row.away_team, team_candidates)
            if not home_full or not away_full:
                unmatched += 1
                continue

            try:
                model_pred = predict_match(fit, home_team=home_full, away_team=away_full)
            except Exception:
                unmatched += 1
                continue

            match_pairs.append({
                "home": home_full, "away": away_full,
                "actual_home_goals": row.home_goals,
                "actual_away_goals": row.away_goals,
                "model": (model_pred.prob_home_win, model_pred.prob_draw, model_pred.prob_away_win,
                          model_pred.prob_over_2_5),
                "market": (row.prob_home_win, row.prob_draw, row.prob_away_win, 0.5),
            })

        print(f"Matched: {len(match_pairs)} matches, unmatched: {unmatched}")
        print()

        # Test various weights
        weights = [
            (1.0, 0.0, "Pure model"),
            (0.9, 0.1, "90/10 model-heavy"),
            (0.7, 0.3, "70/30"),
            (0.5, 0.5, "50/50"),
            (0.3, 0.7, "30/70"),
            (0.2, 0.8, "20/80 market-heavy"),
            (0.1, 0.9, "10/90"),
            (0.0, 1.0, "Pure market"),
        ]

        results: list[BlendResult] = []
        for w_m, w_mk, name in weights:
            correct = 0
            brier = 0.0
            log_loss = 0.0
            n = 0
            for mp in match_pairs:
                m = mp["model"]
                mk = mp["market"]
                p_h = w_m * m[0] + w_mk * mk[0]
                p_d = w_m * m[1] + w_mk * mk[1]
                p_a = w_m * m[2] + w_mk * mk[2]
                p_o = w_m * m[3] + w_mk * mk[3]
                # Renormalize (should already sum ~1)
                total = p_h + p_d + p_a
                if total > 0:
                    p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
                s = score_prediction(
                    prob_home_win=p_h, prob_draw=p_d, prob_away_win=p_a,
                    prob_over_2_5=p_o,
                    actual_home_goals=mp["actual_home_goals"],
                    actual_away_goals=mp["actual_away_goals"],
                )
                correct += int(s.result_correct)
                brier += s.brier_score
                log_loss += s.log_loss
                n += 1
            if n:
                results.append(BlendResult(w_m, w_mk, n, correct / n, brier / n, log_loss / n))

        print(f"{'Config':<22} {'N':>4} {'Accuracy':>10} {'Brier':>10} {'LogLoss':>10}")
        print("-" * 60)
        for i, r in enumerate(results):
            name = weights[i][2]
            print(f"{name:<22} {r.n:>4} {r.accuracy:>9.2%} {r.brier:>10.4f} {r.log_loss:>10.4f}")

        print()
        winner = min(results, key=lambda x: x.brier)
        print(f"🏆 Best by Brier: w_model={winner.w_model}, w_market={winner.w_market}")
        print(f"   accuracy={winner.accuracy:.2%}  brier={winner.brier:.4f}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
