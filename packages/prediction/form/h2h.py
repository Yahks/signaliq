"""Head-to-head computation.

For each (home_team, away_team) pair, look at the last N meetings in the
competition and derive a multiplicative adjustment to the base model.

Why multiplicative: the base model already predicts a base λ_home and λ_away.
H2H is an ADJUSTMENT to that base, not a replacement.

Example:
    Arsenal vs Chelsea: base λ_home=1.8, λ_away=1.2
    Last 5 H2H: Arsenal won 4, Chelsea won 1, aggregate 9-3
    → h2h_multiplier = 1.15 (Arsenal over-performs in this fixture)
    → adjusted λ_home = 1.8 * 1.15 = 2.07
    → adjusted λ_away = 1.2 / 1.15 = 1.04

The multiplier is bounded [0.7, 1.3] to prevent small samples from
dominating the base model. This is a deliberate constraint: H2H is
weak evidence; it should not flip an otherwise clear prediction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

# H2H can only shift the base model by ±30%
H2H_MULTIPLIER_MIN = 0.7
H2H_MULTIPLIER_MAX = 1.3


@dataclass
class H2HSnapshot:
    home_team: str
    away_team: str
    competition: str
    as_of_date: date
    matches_considered: int
    home_wins: int
    draws: int
    away_wins: int
    home_goals_avg: float
    away_goals_avg: float
    h2h_multiplier: float
    raw_recent_h2h: list[dict] = field(default_factory=list)


def _compute_multiplier(home_goals_avg: float, away_goals_avg: float) -> float:
    """Derive a home-team-favoring multiplier from H2H goal averages.

    If H2H is neutral, multiplier = 1.0.
    If home team scores more than away team historically, multiplier > 1.0.
    """
    if home_goals_avg + away_goals_avg == 0:
        return 1.0
    # Ratio bounded: [(a-b)/(a+b)] in [-1, 1]
    imbalance = (home_goals_avg - away_goals_avg) / (home_goals_avg + away_goals_avg)
    # Convert to a small multiplicative factor
    multiplier = 1.0 + 0.3 * imbalance
    # Clamp
    return max(H2H_MULTIPLIER_MIN, min(H2H_MULTIPLIER_MAX, multiplier))


async def compute_h2h(
    session: AsyncSession,
    *,
    home_team: str,
    away_team: str,
    competition: str,
    as_of: datetime | None = None,
    window: int = 5,
) -> H2HSnapshot | None:
    """Compute H2H for a pair of teams using their last N meetings."""
    as_of = as_of or datetime.now(UTC)

    # Pull last N meetings in either venue
    rows = (await session.execute(sql_text("""
        SELECT
            kickoff_at, home_team, away_team,
            home_goals, away_goals
        FROM matches
        WHERE status = 'finished'
          AND competition = :comp
          AND kickoff_at < :as_of
          AND home_goals IS NOT NULL
          AND (
            (home_team = :home AND away_team = :away)
            OR (home_team = :away AND away_team = :home)
          )
        ORDER BY kickoff_at DESC
        LIMIT :lim
    """), {
        "comp": competition, "as_of": as_of,
        "home": home_team, "away": away_team, "lim": window,
    })).all()

    if not rows:
        return None

    # Aggregate from the perspective of "home_team" playing at home TODAY
    # (i.e., we count the historical fixture as if today's home team were home)
    home_wins = 0
    draws = 0
    away_wins = 0
    home_goals_for = 0
    away_goals_for = 0
    raw = []

    for r in rows:
        kickoff, h, a, hg, ag = r
        hist_home_is_todays_home = (h == home_team)
        # Normalize: goals "for today's home team" and "for today's away team"
        goals_for_todays_home = hg if hist_home_is_todays_home else ag
        goals_for_todays_away = ag if hist_home_is_todays_home else hg

        home_goals_for += goals_for_todays_home
        away_goals_for += goals_for_todays_away

        if goals_for_todays_home > goals_for_todays_away:
            home_wins += 1
        elif goals_for_todays_home == goals_for_todays_away:
            draws += 1
        else:
            away_wins += 1

        raw.append({
            "date": kickoff.date().isoformat(),
            "home": h,
            "away": a,
            "score": f"{hg}-{ag}",
            "orientation": "as_today" if hist_home_is_todays_home else "reversed",
            "result": "home_win" if goals_for_todays_home > goals_for_todays_away
                      else ("draw" if goals_for_todays_home == goals_for_todays_away
                            else "away_win"),
        })

    n = len(rows)
    home_goals_avg = home_goals_for / n
    away_goals_avg = away_goals_for / n
    multiplier = _compute_multiplier(home_goals_avg, away_goals_avg)

    return H2HSnapshot(
        home_team=home_team,
        away_team=away_team,
        competition=competition,
        as_of_date=as_of.date(),
        matches_considered=n,
        home_wins=home_wins,
        draws=draws,
        away_wins=away_wins,
        home_goals_avg=round(home_goals_avg, 3),
        away_goals_avg=round(away_goals_avg, 3),
        h2h_multiplier=round(multiplier, 3),
        raw_recent_h2h=raw,
    )


async def save_h2h_snapshot(
    session: AsyncSession,
    snapshot: H2HSnapshot,
) -> None:
    """Upsert H2H snapshot."""
    import json
    await session.execute(sql_text("""
        INSERT INTO h2h_snapshots (
            home_team, away_team, competition, as_of_date, matches_considered,
            home_wins, draws, away_wins,
            home_goals_avg, away_goals_avg, h2h_multiplier, raw_recent_h2h
        ) VALUES (
            :h, :a, :c, :d, :n,
            :hw, :dr, :aw,
            :hga, :aga, :mult, CAST(:raw AS jsonb)
        )
        ON CONFLICT (home_team, away_team, competition, as_of_date) DO UPDATE
        SET matches_considered = EXCLUDED.matches_considered,
            home_wins = EXCLUDED.home_wins,
            draws = EXCLUDED.draws,
            away_wins = EXCLUDED.away_wins,
            home_goals_avg = EXCLUDED.home_goals_avg,
            away_goals_avg = EXCLUDED.away_goals_avg,
            h2h_multiplier = EXCLUDED.h2h_multiplier,
            raw_recent_h2h = EXCLUDED.raw_recent_h2h,
            computed_at = now()
    """), {
        "h": snapshot.home_team, "a": snapshot.away_team,
        "c": snapshot.competition, "d": snapshot.as_of_date,
        "n": snapshot.matches_considered,
        "hw": snapshot.home_wins, "dr": snapshot.draws, "aw": snapshot.away_wins,
        "hga": snapshot.home_goals_avg, "aga": snapshot.away_goals_avg,
        "mult": snapshot.h2h_multiplier,
        "raw": json.dumps(snapshot.raw_recent_h2h),
    })
    await session.commit()
