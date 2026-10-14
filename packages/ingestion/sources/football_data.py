"""football-data.org fetcher.

Free tier: 10 requests/minute, covers 12 major competitions.
Docs: https://docs.football-data.org/general/v4/index.html

We fetch:
  - Competition standings (for context)
  - Upcoming fixtures for the next N days
  - Finished matches for the last M days (for model training)

Everything goes to the `matches` table (via a Celery task).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from packages.core.config import get_settings
from packages.core.logging import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.football-data.org/v4"
TIMEOUT = 15.0

# football-data.org competition codes we care about (EPL, Championship, etc.)
COMPETITION_CODES = {
    "PL": "EPL",
    "ELC": "Championship",
    "PD": "La Liga",
    "SA": "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "CL": "Champions League",
}


@dataclass
class FetchedMatch:
    external_id: str
    competition: str
    season: str
    home_team: str
    away_team: str
    kickoff_at: datetime
    status: str  # scheduled | live | finished | postponed | cancelled
    home_goals: int | None = None
    away_goals: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _status_map(api_status: str) -> str:
    """Map football-data.org status to our internal status."""
    m = {
        "SCHEDULED": "scheduled",
        "TIMED": "scheduled",
        "IN_PLAY": "live",
        "PAUSED": "live",
        "FINISHED": "finished",
        "POSTPONED": "postponed",
        "SUSPENDED": "postponed",
        "CANCELLED": "cancelled",
        "AWARDED": "finished",
    }
    return m.get(api_status, "scheduled")


def _to_match(raw: dict[str, Any]) -> FetchedMatch | None:
    """Convert an API match object to FetchedMatch. Returns None if invalid."""
    try:
        match_id = str(raw["id"])
        competition_obj = raw.get("competition") or {}
        comp_code = competition_obj.get("code") or ""
        competition = COMPETITION_CODES.get(comp_code, competition_obj.get("name", "Unknown"))

        season_obj = raw.get("season") or {}
        season = str(season_obj.get("startDate", ""))[:4] + "-" + str(season_obj.get("endDate", ""))[:4]
        if season == "-":
            season = str(season_obj.get("id", "unknown"))

        home = (raw.get("homeTeam") or {}).get("name")
        away = (raw.get("awayTeam") or {}).get("name")
        if not home or not away:
            return None

        kickoff = _parse_dt(raw.get("utcDate"))
        if not kickoff:
            return None

        status = _status_map(str(raw.get("status", "SCHEDULED")))

        score = raw.get("score") or {}
        full_time = score.get("fullTime") or {}
        home_goals = full_time.get("home")
        away_goals = full_time.get("away")
        if status != "finished":
            home_goals = None
            away_goals = None

        return FetchedMatch(
            external_id=match_id,
            competition=competition,
            season=season,
            home_team=home,
            away_team=away,
            kickoff_at=kickoff,
            status=status,
            home_goals=home_goals,
            away_goals=away_goals,
            metadata={
                "matchday": raw.get("matchday"),
                "venue": raw.get("venue"),
                "referee": (raw.get("referees") or [{}])[0].get("name") if raw.get("referees") else None,
            },
        )
    except Exception as exc:
        log.warning("football.to_match_failed", error=str(exc)[:200])
        return None


async def fetch_competition_matches(
    *,
    competition_code: str,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    api_key: str,
) -> list[FetchedMatch]:
    """Fetch matches for a competition within a date range."""
    url = f"{BASE_URL}/competitions/{competition_code}/matches"
    params: dict[str, str] = {}
    if date_from:
        params["dateFrom"] = date_from.date().isoformat()
    if date_to:
        params["dateTo"] = date_to.date().isoformat()

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(url, params=params, headers={"X-Auth-Token": api_key})
    except httpx.HTTPError as exc:
        log.warning("football.fetch_failed", competition=competition_code, error=str(exc)[:200])
        return []

    if r.status_code == 403:
        log.warning("football.auth_failed", competition=competition_code, hint="check API key")
        return []
    if r.status_code == 429:
        log.warning("football.rate_limited", competition=competition_code)
        return []
    if r.status_code != 200:
        log.warning("football.http_error", competition=competition_code, status=r.status_code)
        return []

    data = r.json()
    matches_raw = data.get("matches") or []
    out: list[FetchedMatch] = []
    for raw in matches_raw:
        m = _to_match(raw)
        if m:
            out.append(m)

    log.info("football.fetched", competition=competition_code, count=len(out))
    return out


async def fetch_upcoming_matches(
    *,
    days_ahead: int = 7,
    competitions: list[str] | None = None,
    api_key: str,
) -> list[FetchedMatch]:
    """Fetch upcoming fixtures across selected competitions."""
    now = datetime.now(UTC)
    date_to = now + timedelta(days=days_ahead)
    comps = competitions or list(COMPETITION_CODES.keys())

    all_matches: list[FetchedMatch] = []
    for code in comps:
        matches = await fetch_competition_matches(
            competition_code=code,
            date_from=now,
            date_to=date_to,
            api_key=api_key,
        )
        all_matches.extend(matches)
    return all_matches


async def fetch_recent_results(
    *,
    days_back: int = 7,
    competitions: list[str] | None = None,
    api_key: str,
) -> list[FetchedMatch]:
    """Fetch finished matches from the last N days."""
    now = datetime.now(UTC)
    date_from = now - timedelta(days=days_back)
    comps = competitions or list(COMPETITION_CODES.keys())

    all_matches: list[FetchedMatch] = []
    for code in comps:
        matches = await fetch_competition_matches(
            competition_code=code,
            date_from=date_from,
            date_to=now,
            api_key=api_key,
        )
        # Filter to finished only
        all_matches.extend([m for m in matches if m.status == "finished"])
    return all_matches
