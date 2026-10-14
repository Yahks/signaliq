"""Football-Data.co.uk historical odds fetcher.

Source: https://www.football-data.co.uk/data.php
License: freely available for personal and commercial use (see their site).

Files by competition and season:
  EPL:      https://www.football-data.co.uk/mmz4281/{SS}/E0.csv
  Champ:    https://www.football-data.co.uk/mmz4281/{SS}/E1.csv
  La Liga:  https://www.football-data.co.uk/mmz4281/{SS}/SP1.csv
  Serie A:  https://www.football-data.co.uk/mmz4281/{SS}/I1.csv
  Bundes:   https://www.football-data.co.uk/mmz4281/{SS}/D1.csv
  Ligue 1:  https://www.football-data.co.uk/mmz4281/{SS}/F1.csv

Where {SS} is the 2-digit season suffix:
  2024-25 season → "2425"
  2023-24 season → "2324"

The CSVs include:
  Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR,   (result)
  B365H, B365D, B365A,                          (Bet365 odds)
  BWH, BWD, BWA,                                (Bet&Win)
  IWH, IWD, IWA,                                (Interwetten)
  PSH, PSD, PSA,                                (Pinnacle — sharpest)
  WHH, WHD, WHA,                                (William Hill)
  ... up to 15+ bookmakers

We compute the CONSENSUS from the available odds columns, same math
as The Odds API fetcher (overround removal, median across books).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from packages.core.logging import get_logger

log = get_logger(__name__)

BASE = "https://www.football-data.co.uk/mmz4281"

# competition_code → football-data.co.uk file prefix
COMPETITION_FILES = {
    "EPL": "E0",
    "Championship": "E1",
    "La Liga": "SP1",
    "Serie A": "I1",
    "Bundesliga": "D1",
    "Ligue 1": "F1",
}

# Common bookmaker columns we'll aggregate over
BOOKMAKER_PAIRS = [
    ("B365H", "B365D", "B365A"),   # Bet365
    ("BWH",   "BWD",   "BWA"),     # Bet&Win
    ("IWH",   "IWD",   "IWA"),     # Interwetten
    ("PSH",   "PSD",   "PSA"),     # Pinnacle (sharp)
    ("WHH",   "WHD",   "WHA"),     # William Hill
    ("VCH",   "VCD",   "VCA"),     # VC Bet
    ("1XBH",  "1XBD",  "1XBA"),    # 1xBet
    ("BFH",   "BFD",   "BFA"),     # Betfair Exchange
]


@dataclass
class HistoricalOdds:
    date: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    bookmakers_used: int
    # Optional extras from the CSV (may be None)
    home_corners: int | None = None
    away_corners: int | None = None
    home_yellow: int | None = None
    away_yellow: int | None = None
    home_red: int | None = None
    away_red: int | None = None


def _season_suffix(season: str) -> str:
    """'2024-25' → '2425', '2425' → '2425'."""
    if "-" in season:
        a, b = season.split("-")
        return a[-2:] + b[-2:]
    return season


def _to_implied(odds: float) -> float:
    return 1.0 / odds if odds > 1.0 else 0.0


def _remove_overround(probs: list[float]) -> list[float]:
    total = sum(probs)
    return [p / total for p in probs] if total > 0 else probs


def _consensus_from_row(row: dict[str, str]) -> tuple[float, float, float, int] | None:
    """Extract consensus W/D/L from a CSV row."""
    import statistics

    home_probs: list[float] = []
    draw_probs: list[float] = []
    away_probs: list[float] = []

    for h_col, d_col, a_col in BOOKMAKER_PAIRS:
        try:
            h = float(row.get(h_col, "") or "")
            d = float(row.get(d_col, "") or "")
            a = float(row.get(a_col, "") or "")
        except (ValueError, TypeError):
            continue
        if not (h > 1.0 and d > 1.0 and a > 1.0):
            continue
        raw = [_to_implied(h), _to_implied(d), _to_implied(a)]
        norm = _remove_overround(raw)
        home_probs.append(norm[0])
        draw_probs.append(norm[1])
        away_probs.append(norm[2])

    if not home_probs:
        return None

    return (
        statistics.median(home_probs),
        statistics.median(draw_probs),
        statistics.median(away_probs),
        len(home_probs),
    )


async def fetch_season_odds(
    *,
    competition_code: str,
    season: str,
) -> list[HistoricalOdds]:
    """Download and parse one season of historical odds + results."""
    file_prefix = COMPETITION_FILES.get(competition_code)
    if not file_prefix:
        log.warning("fduk.unknown_competition", code=competition_code)
        return []

    season_suffix = _season_suffix(season)
    url = f"{BASE}/{season_suffix}/{file_prefix}.csv"

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "SignalIQ/0.1 (contact@yoursite.com)"})
    except httpx.HTTPError as exc:
        log.warning("fduk.fetch_failed", url=url, error=str(exc)[:200])
        return []

    if r.status_code != 200:
        log.warning("fduk.http_error", url=url, status=r.status_code)
        return []

    text = r.text
    reader = csv.DictReader(io.StringIO(text))

    out: list[HistoricalOdds] = []
    for row in reader:
        try:
            date_str = (row.get("Date") or "").strip()
            home = (row.get("HomeTeam") or "").strip()
            away = (row.get("AwayTeam") or "").strip()
            if not (date_str and home and away):
                continue

            # Parse result
            hg_raw = (row.get("FTHG") or "").strip()
            ag_raw = (row.get("FTAG") or "").strip()
            if not (hg_raw and ag_raw):
                continue
            hg = int(hg_raw)
            ag = int(ag_raw)

            consensus = _consensus_from_row(row)
            if not consensus:
                continue
            p_home, p_draw, p_away, n_bm = consensus

            def _opt_int(key: str) -> int | None:
                raw = (row.get(key) or "").strip()
                if not raw:
                    return None
                try:
                    return int(float(raw))
                except (ValueError, TypeError):
                    return None

            out.append(HistoricalOdds(
                date=date_str,
                home_team=home,
                away_team=away,
                home_goals=hg,
                away_goals=ag,
                prob_home_win=p_home,
                prob_draw=p_draw,
                prob_away_win=p_away,
                bookmakers_used=n_bm,
                home_corners=_opt_int("HC"),
                away_corners=_opt_int("AC"),
                home_yellow=_opt_int("HY"),
                away_yellow=_opt_int("AY"),
                home_red=_opt_int("HR"),
                away_red=_opt_int("AR"),
            ))
        except Exception as exc:
            log.warning("fduk.parse_failed", error=str(exc)[:200])
            continue

    log.info("fduk.fetched", competition=competition_code, season=season, matches=len(out))
    return out
