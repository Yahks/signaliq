"""Corner analysis for match prediction.

Data source: Football-Data.co.uk CSVs include HC (home corners) and
AC (away corners) for every match going back to 2000+ for major leagues.

Two computations:

1. Per-team corner rates
   - corners_for_per_match (attack signal)
   - corners_against_per_match (defense signal)

2. Per-match predicted corners
   - expected_home_corners = avg(home_corners_for) + avg(away_corners_against) / 2
   - expected_away_corners = avg(away_corners_for) + avg(home_corners_against) / 2
   - expected_total = sum

3. Per-team "corner residual" (recent form vs season baseline)
   - Same technique as goals form
   - Used to detect teams whose style is changing

NOTE: corners are a STYLE signal, not a STRENGTH signal. A team can
get many corners and score few goals. The predictive value of corners
for match outcome is weak and must be validated empirically before
being added to the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class CornerStats:
    team: str
    competition: str
    matches_considered: int
    corners_for_per_match: float
    corners_against_per_match: float
    corners_for_last5: float
    corners_against_last5: float


@dataclass
class CornerPrediction:
    home_team: str
    away_team: str
    expected_home_corners: float
    expected_away_corners: float
    expected_total_corners: float
    home_stats: CornerStats | None = None
    away_stats: CornerStats | None = None
    confidence: str = "normal"          # "normal" | "low_insufficient_history"


async def compute_corner_stats(
    session: AsyncSession,
    *,
    team: str,
    competition: str,
    as_of: datetime | None = None,
) -> CornerStats | None:
    """Compute corner stats for one team up to as_of."""
    as_of = as_of or datetime.now(UTC)

    # Baseline: all finished matches with corners data
    baseline = (await session.execute(sql_text("""
        SELECT
            avg(CASE WHEN home_team = :team THEN home_corners ELSE away_corners END) AS cf,
            avg(CASE WHEN home_team = :team THEN away_corners ELSE home_corners END) AS ca,
            count(*) AS n
        FROM match_corners
        WHERE competition = :comp
          AND kickoff_at < :as_of
          AND (home_team = :team OR away_team = :team)
          AND home_corners IS NOT NULL
    """), {"team": team, "comp": competition, "as_of": as_of})).first()

    if not baseline or baseline[2] == 0:
        return None

    # Last 5
    recent_rows = (await session.execute(sql_text("""
        SELECT
            CASE WHEN home_team = :team THEN home_corners ELSE away_corners END AS cf,
            CASE WHEN home_team = :team THEN away_corners ELSE home_corners END AS ca
        FROM match_corners
        WHERE competition = :comp
          AND kickoff_at < :as_of
          AND (home_team = :team OR away_team = :team)
          AND home_corners IS NOT NULL
        ORDER BY kickoff_at DESC
        LIMIT 5
    """), {"team": team, "comp": competition, "as_of": as_of})).all()

    n_recent = len(recent_rows)
    cf_recent = sum(r[0] for r in recent_rows) / n_recent if n_recent else 0.0
    ca_recent = sum(r[1] for r in recent_rows) / n_recent if n_recent else 0.0

    return CornerStats(
        team=team,
        competition=competition,
        matches_considered=int(baseline[2]),
        corners_for_per_match=round(float(baseline[0]), 3),
        corners_against_per_match=round(float(baseline[1]), 3),
        corners_for_last5=round(cf_recent, 3),
        corners_against_last5=round(ca_recent, 3),
    )


async def _league_average_corners(
    session: AsyncSession,
    *,
    competition: str,
    as_of: datetime,
) -> tuple[float, float]:
    """Fallback: league-wide avg corners scored/conceded per side.

    Returns (avg_for, avg_against) for a generic team in this competition.
    Used when a specific team has no corner history (e.g. newly promoted).
    """
    row = (await session.execute(sql_text("""
        SELECT
            avg(home_corners) AS avg_home_c,
            avg(away_corners) AS avg_away_c,
            count(*)          AS n
        FROM match_corners
        WHERE competition = :comp
          AND kickoff_at < :as_of
          AND home_corners IS NOT NULL
    """), {"comp": competition, "as_of": as_of})).first()

    if not row or row[2] == 0:
        # Absolute last resort — empirical EPL medians
        return (5.5, 5.0)
    # A team is home half the time, away half the time.
    # avg_for = (avg_home_c + avg_away_c) / 2; same for avg_against.
    avg_for = (float(row[0]) + float(row[1])) / 2.0
    return (avg_for, avg_for)


async def predict_corners(
    session: AsyncSession,
    *,
    home_team: str,
    away_team: str,
    competition: str = "EPL",
    as_of: datetime | None = None,
) -> CornerPrediction | None:
    """Predict total and per-team corners for a match.

    If either team lacks corner history (e.g. promoted this season),
    falls back to league averages and flags confidence="low_insufficient_history".
    Returns None only if the competition itself has no corner data.
    """
    as_of = as_of or datetime.now(UTC)

    home_stats = await compute_corner_stats(
        session, team=home_team, competition=competition, as_of=as_of,
    )
    away_stats = await compute_corner_stats(
        session, team=away_team, competition=competition, as_of=as_of,
    )

    # If both missing → no data at all for this competition → give up
    if home_stats is None and away_stats is None:
        return None

    # Fallback for whichever side lacks history
    confidence = "normal"
    league_for, league_against = await _league_average_corners(
        session, competition=competition, as_of=as_of,
    )

    if home_stats is None:
        confidence = "low_insufficient_history"
        home_for = league_for
        home_against = league_against
    else:
        home_for = home_stats.corners_for_per_match
        home_against = home_stats.corners_against_per_match

    if away_stats is None:
        confidence = "low_insufficient_history"
        away_for = league_for
        away_against = league_against
    else:
        away_for = away_stats.corners_for_per_match
        away_against = away_stats.corners_against_per_match

    # Attack vs defense blend
    exp_home = 0.5 * home_for + 0.5 * away_against
    exp_away = 0.5 * away_for + 0.5 * home_against

    return CornerPrediction(
        home_team=home_team,
        away_team=away_team,
        expected_home_corners=round(exp_home, 2),
        expected_away_corners=round(exp_away, 2),
        expected_total_corners=round(exp_home + exp_away, 2),
        home_stats=home_stats,
        away_stats=away_stats,
        confidence=confidence,
    )
