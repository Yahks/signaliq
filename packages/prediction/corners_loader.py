"""Load historical corners from Football-Data.co.uk into match_corners table.

We resolve team names via the same fuzzy matcher we use elsewhere.
Skips rows that don't match our DB teams.
"""
from __future__ import annotations

from datetime import UTC, datetime

from dateutil import parser as dateparser
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.ingestion.sources.football_data_uk import fetch_season_odds
from packages.prediction.odds_loader import _fuzzy

log = get_logger(__name__)


async def load_corners_for_season(
    session: AsyncSession,
    *,
    competition_code: str,
    season: str,
) -> dict:
    """Fetch historical CSV, store rows in match_corners table."""
    rows = await fetch_season_odds(competition_code=competition_code, season=season)
    log.info("corners_loader.fetched", competition=competition_code, season=season, count=len(rows))

    # Load candidate team names for fuzzy matching
    candidate_rows = (await session.execute(sql_text("""
        SELECT DISTINCT team FROM (
            SELECT home_team AS team FROM matches WHERE competition = 'EPL'
            UNION
            SELECT away_team AS team FROM matches WHERE competition = 'EPL'
        ) t
    """))).all()
    candidates = [r[0] for r in candidate_rows]

    inserted = 0
    skipped_no_team = 0
    skipped_no_corners = 0

    for row in rows:
        if row.home_corners is None or row.away_corners is None:
            skipped_no_corners += 1
            continue

        home_full = _fuzzy(row.home_team, candidates)
        away_full = _fuzzy(row.away_team, candidates)
        if not home_full or not away_full:
            skipped_no_team += 1
            continue

        try:
            # Parse the "DD/MM/YYYY" date
            kickoff = dateparser.parse(row.date, dayfirst=True).replace(tzinfo=UTC)
        except Exception:
            continue

        await session.execute(sql_text("""
            INSERT INTO match_corners (
                external_id, competition, season, home_team, away_team,
                kickoff_at, home_goals, away_goals,
                home_corners, away_corners,
                home_yellow, away_yellow, home_red, away_red
            ) VALUES (
                NULL, :comp, :season, :home, :away,
                :kickoff, :hg, :ag,
                :hc, :ac,
                :hy, :ay, :hr, :ar
            )
            ON CONFLICT (competition, kickoff_at, home_team, away_team) DO UPDATE
            SET home_corners = EXCLUDED.home_corners,
                away_corners = EXCLUDED.away_corners,
                home_yellow = EXCLUDED.home_yellow,
                away_yellow = EXCLUDED.away_yellow,
                home_red = EXCLUDED.home_red,
                away_red = EXCLUDED.away_red
        """), {
            "comp": "EPL",
            "season": row.date[-4:],  # YYYY from DD/MM/YYYY
            "home": home_full, "away": away_full,
            "kickoff": kickoff,
            "hg": row.home_goals, "ag": row.away_goals,
            "hc": row.home_corners, "ac": row.away_corners,
            "hy": row.home_yellow, "ay": row.away_yellow,
            "hr": row.home_red, "ar": row.away_red,
        })
        inserted += 1

    await session.commit()
    log.info("corners_loader.done", inserted=inserted, skipped_no_team=skipped_no_team,
             skipped_no_corners=skipped_no_corners)
    return {
        "fetched": len(rows),
        "inserted": inserted,
        "skipped_no_team": skipped_no_team,
        "skipped_no_corners": skipped_no_corners,
    }
