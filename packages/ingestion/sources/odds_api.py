"""The Odds API fetcher.

Docs: https://the-odds-api.com/liveapi/guides/v4/

We fetch:
  - Upcoming EPL fixtures with bookmaker odds
  - From multiple bookmakers → compute the CONSENSUS (median) implied probability
  - Store one external_prediction row per fixture, attributed to "the_odds_api"

Odds conversion:
  Decimal odds = 1 / implied_probability
  implied_probability = 1 / decimal_odds
  Overround adjustment (to remove bookmaker margin):
    - sum(raw implied probs across all outcomes) - 1 = overround
    - Scale each raw prob by (1 / (1 + overround))

Free tier: 500 requests/month. Each EPL fixtures call = 1 request, covers
all matches in the next N days. We schedule one fetch per day.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from packages.core.logging import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"
TIMEOUT = 20.0

# The Odds API sport keys
SPORT_KEYS = {
    "EPL": "soccer_epl",
    "Championship": "soccer_efl_champ",
    "La Liga": "soccer_spain_la_liga",
    "Serie A": "soccer_italy_serie_a",
    "Bundesliga": "soccer_germany_bundesliga",
    "Ligue 1": "soccer_france_ligue_one",
    "Champions League": "soccer_uefa_champs_league",
}


@dataclass
class OddsPrediction:
    """Consensus implied probabilities for a match."""
    external_id: str            # The Odds API match id
    home_team: str
    away_team: str
    kickoff_at: datetime
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    prob_over_2_5: float | None
    bookmakers_used: int
    raw_payload: dict = field(default_factory=dict)


def _to_implied(odds: float) -> float:
    """Convert decimal odds to raw implied probability (uncorrected for margin)."""
    if odds <= 1.0:
        return 0.0
    return 1.0 / odds


def _remove_overround(probs: list[float]) -> list[float]:
    """Remove bookmaker margin by scaling probabilities to sum to 1.0."""
    total = sum(probs)
    if total <= 0:
        return probs
    return [p / total for p in probs]


def _consensus_from_bookmakers(
    bookmakers: list[dict],
    home_team: str,
    away_team: str,
) -> tuple[float, float, float, float | None, int] | None:
    """Aggregate across bookmakers: per-bookmaker normalized probs, then median."""
    import statistics

    home_probs: list[float] = []
    draw_probs: list[float] = []
    away_probs: list[float] = []
    over_probs: list[float] = []

    for bm in bookmakers:
        markets = bm.get("markets") or []
        h2h = next((m for m in markets if m.get("key") == "h2h"), None)
        totals = next((m for m in markets if m.get("key") == "totals"), None)

        if not h2h:
            continue

        outcomes = h2h.get("outcomes") or []
        h_odds = next((o.get("price") for o in outcomes if o.get("name") == home_team), None)
        a_odds = next((o.get("price") for o in outcomes if o.get("name") == away_team), None)
        d_odds = next((o.get("price") for o in outcomes if o.get("name") == "Draw"), None)

        if not (h_odds and a_odds and d_odds):
            continue

        raw = [_to_implied(h_odds), _to_implied(d_odds), _to_implied(a_odds)]
        norm = _remove_overround(raw)
        home_probs.append(norm[0])
        draw_probs.append(norm[1])
        away_probs.append(norm[2])

        # Over/Under 2.5 if available
        if totals:
            ou_outcomes = totals.get("outcomes") or []
            over = next((o.get("price") for o in ou_outcomes
                         if o.get("name") == "Over" and o.get("point") == 2.5), None)
            under = next((o.get("price") for o in ou_outcomes
                          if o.get("name") == "Under" and o.get("point") == 2.5), None)
            if over and under:
                raw_ou = [_to_implied(over), _to_implied(under)]
                norm_ou = _remove_overround(raw_ou)
                over_probs.append(norm_ou[0])

    if not home_probs:
        return None

    p_home = statistics.median(home_probs)
    p_draw = statistics.median(draw_probs)
    p_away = statistics.median(away_probs)
    p_over = statistics.median(over_probs) if over_probs else None
    return p_home, p_draw, p_away, p_over, len(home_probs)


async def fetch_odds_for_competition(
    *,
    competition_code: str,
    api_key: str,
    days_ahead: int = 14,
) -> list[OddsPrediction]:
    """Fetch consensus odds for all upcoming matches in a competition."""
    sport_key = SPORT_KEYS.get(competition_code)
    if not sport_key:
        log.warning("odds.unknown_competition", code=competition_code)
        return []

    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": api_key,
        "regions": "uk,eu",           # UK + EU bookmakers
        "markets": "h2h,totals",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        log.warning("odds.fetch_failed", competition=competition_code, error=str(exc)[:200])
        return []

    if r.status_code == 401:
        log.warning("odds.auth_failed", hint="check THE_ODDS_API_KEY")
        return []
    if r.status_code == 429:
        log.warning("odds.rate_limited", hint="free tier is 500 req/month")
        return []
    if r.status_code != 200:
        log.warning("odds.http_error", competition=competition_code, status=r.status_code)
        return []

    data = r.json()
    log.info("odds.fetched_raw", competition=competition_code, matches=len(data))

    cutoff = datetime.now(UTC) + timedelta(days=days_ahead)
    out: list[OddsPrediction] = []

    for match in data:
        try:
            kickoff = datetime.fromisoformat(match["commence_time"].replace("Z", "+00:00"))
            if kickoff > cutoff:
                continue
            home_team = match.get("home_team")
            away_team = match.get("away_team")
            bookmakers = match.get("bookmakers") or []
            if not home_team or not away_team or not bookmakers:
                continue

            consensus = _consensus_from_bookmakers(bookmakers, home_team, away_team)
            if not consensus:
                continue

            p_home, p_draw, p_away, p_over, n_bm = consensus

            out.append(OddsPrediction(
                external_id=match["id"],
                home_team=home_team,
                away_team=away_team,
                kickoff_at=kickoff,
                prob_home_win=p_home,
                prob_draw=p_draw,
                prob_away_win=p_away,
                prob_over_2_5=p_over,
                bookmakers_used=n_bm,
                raw_payload={
                    "bookmaker_count": n_bm,
                    "sample_bookmakers": [bm.get("key") for bm in bookmakers[:5]],
                },
            ))
        except Exception as exc:
            log.warning("odds.parse_match_failed", error=str(exc)[:200])
            continue

    log.info("odds.fetched", competition=competition_code, consensus=len(out))
    return out
