"""Load historical matches from football-data.org into the matches table.

Two modes:
  1. fetch_and_store(): pull finished matches for a season from the API
  2. load_from_db(): read stored matches back into MatchRecord for fitting

Usage (single season, EPL):
    await fetch_and_store_season("PL", "2025", api_key)
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.ingestion.sources.football_data import (
    FetchedMatch,
    fetch_competition_matches,
)
from packages.prediction.dixon_coles import MatchRecord

log = get_logger(__name__)


def _to_fetched(raw: dict[str, Any], competition_code: str) -> FetchedMatch | None:
    """Convert football-data.org match shape to our FetchedMatch."""
    # Reuse the fetcher's converter
    from packages.ingestion.sources.football_data import _to_match
    return _to_match(raw)


async def fetch_and_store_season(
    session: AsyncSession,
    *,
    competition_code: str,
    season: str,  # e.g. "2025" for 2025-26 season
    api_key: str,
) -> dict:
    """Fetch all matches for a season and store them.

    `season` maps to football-data.org's `season` query param (a year).
    For EPL 2025-26 season, use season="2025".
    """
    # The API accepts season as a query param, but our helper doesn't yet.
    # We'll fetch by date range for the full season instead.
    year = int(season)
    # EPL seasons run August -> May
    date_from = datetime(year, 8, 1, tzinfo=UTC)
    date_to = datetime(year + 1, 6, 15, tzinfo=UTC)

    log.info(
        "loader.fetch_season_start",
        competition=competition_code,
        season=season,
        date_from=date_from.date().isoformat(),
        date_to=date_to.date().isoformat(),
    )

    matches = await fetch_competition_matches(
        competition_code=competition_code,
        date_from=date_from,
        date_to=date_to,
        api_key=api_key,
    )

    inserted = 0
    updated = 0
    for m in matches:
        if m.status != "finished" or m.home_goals is None or m.away_goals is None:
            continue
        # Upsert
        result = await session.execute(sql_text("""
            INSERT INTO matches (
                external_id, competition, season, home_team, away_team,
                kickoff_at, status, home_goals, away_goals, metadata
            ) VALUES (
                :ext_id, :comp, :season, :home, :away,
                :kickoff, :status, :hg, :ag, CAST(:meta AS jsonb)
            )
            ON CONFLICT (external_id) DO UPDATE
            SET status = EXCLUDED.status,
                home_goals = EXCLUDED.home_goals,
                away_goals = EXCLUDED.away_goals,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            RETURNING (xmax = 0) AS inserted
        """), {
            "ext_id": m.external_id,
            "comp": m.competition,
            "season": m.season,
            "home": m.home_team,
            "away": m.away_team,
            "kickoff": m.kickoff_at,
            "status": m.status,
            "hg": m.home_goals,
            "ag": m.away_goals,
            "meta": __import__("json").dumps(m.metadata or {}),
        })
        row = result.first()
        if row and row[0]:
            inserted += 1
        else:
            updated += 1

    await session.commit()
    log.info("loader.fetch_season_done", inserted=inserted, updated=updated, total=len(matches))
    return {"fetched": len(matches), "inserted": inserted, "updated": updated}


async def load_training_data(
    session: AsyncSession,
    *,
    competition: str = "EPL",
    season: str | None = None,
    max_matches: int = 5000,
) -> list[MatchRecord]:
    """Load finished matches from the DB into MatchRecord objects for fitting."""
    stmt = """
        SELECT home_team, away_team, home_goals, away_goals, kickoff_at
        FROM matches
        WHERE status = 'finished'
          AND home_goals IS NOT NULL
          AND away_goals IS NOT NULL
          AND competition = :comp
    """
    params: dict[str, Any] = {"comp": competition, "limit": max_matches}
    if season:
        stmt += " AND season = :season"
        params["season"] = season
    stmt += " ORDER BY kickoff_at DESC LIMIT :limit"

    rows = (await session.execute(sql_text(stmt), params)).all()
    out = [
        MatchRecord(
            home_team=r[0],
            away_team=r[1],
            home_goals=r[2],
            away_goals=r[3],
            played_at=r[4],
        )
        for r in rows
    ]
    log.info("loader.load_training_data", competition=competition, season=season, count=len(out))
    return out
