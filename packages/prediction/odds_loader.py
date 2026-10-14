"""Load live odds from The Odds API into external_predictions.

Flow:
  1. Fetch consensus odds for a competition (returns per-match probabilities)
  2. For each odds row, match it to a match in our `matches` table
  3. Store/update an `external_predictions` row attributed to "the_odds_api"

Team name matching: The Odds API uses short names ("Tottenham Hotspur"),
our DB uses long names ("Tottenham Hotspur FC"). We use fuzzy matching with
a threshold.

Idempotent: re-running updates the same row (UNIQUE on source_id, match_id).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.ingestion.sources.odds_api import OddsPrediction, fetch_odds_for_competition

log = get_logger(__name__)

# Name aliases from The Odds API → our DB canonical names
ALIASES = {
    # === The Odds API style ===
    "Tottenham Hotspur": "Tottenham Hotspur FC",
    "Brighton and Hove Albion": "Brighton & Hove Albion FC",
    "Brighton & Hove Albion": "Brighton & Hove Albion FC",
    "Newcastle United": "Newcastle United FC",
    "Manchester United": "Manchester United FC",
    "Manchester City": "Manchester City FC",
    "Nottingham Forest": "Nottingham Forest FC",
    "West Ham United": "West Ham United FC",
    "Wolverhampton Wanderers": "Wolverhampton Wanderers FC",
    "Leicester City": "Leicester City FC",
    "Ipswich Town": "Ipswich Town FC",
    "Hull City": "Hull City AFC",
    "Coventry City": "Coventry City FC",
    "AFC Bournemouth": "AFC Bournemouth",
    "Crystal Palace": "Crystal Palace FC",
    "Aston Villa": "Aston Villa FC",
    "Sheffield United": "Sheffield United FC",
    "West Bromwich Albion": "West Bromwich Albion FC",
    "Leeds United": "Leeds United FC",
    "Norwich City": "Norwich City FC",
    "Sunderland": "Sunderland AFC",
    # === Football-Data.co.uk style (heavy abbreviation) ===
    "Man United": "Manchester United FC",
    "Man Utd": "Manchester United FC",
    "Man City": "Manchester City FC",
    "Nott'm Forest": "Nottingham Forest FC",
    "Nottm Forest": "Nottingham Forest FC",
    "Newcastle": "Newcastle United FC",
    "Brighton": "Brighton & Hove Albion FC",
    "Spurs": "Tottenham Hotspur FC",
    "Tottenham": "Tottenham Hotspur FC",
    "Wolves": "Wolverhampton Wanderers FC",
    "West Ham": "West Ham United FC",
    "West Brom": "West Bromwich Albion FC",
    "Leicester": "Leicester City FC",
    "Ipswich": "Ipswich Town FC",
    "Sheffield United": "Sheffield United FC",
    "Sheffield Utd": "Sheffield United FC",
    "Hull": "Hull City AFC",
    "Coventry": "Coventry City FC",
    "Bournemouth": "AFC Bournemouth",
    "Crystal Palace": "Crystal Palace FC",
    "Aston Villa": "Aston Villa FC",
    "Leeds": "Leeds United FC",
    "Norwich": "Norwich City FC",
    "Sunderland": "Sunderland AFC",
    "Everton": "Everton FC",
    "Fulham": "Fulham FC",
    "Burnley": "Burnley FC",
    "Luton": "Luton Town FC",
    "Brentford": "Brentford FC",
    "Chelsea": "Chelsea FC",
    "Arsenal": "Arsenal FC",
    "Liverpool": "Liverpool FC",
    "Southampton": "Southampton FC",
    "Watford": "Watford FC",
    "Cardiff": "Cardiff City FC",
    "Huddersfield": "Huddersfield Town AFC",
    "Stoke": "Stoke City FC",
    "Swansea": "Swansea City AFC",
    "QPR": "Queens Park Rangers FC",
    "Blackburn": "Blackburn Rovers FC",
    "Middlesbrough": "Middlesbrough FC",
    "Sheffield Wednesday": "Sheffield Wednesday FC",
    "Derby": "Derby County FC",
    "Birmingham": "Birmingham City FC",
    "Reading": "Reading FC",
    "Bolton": "Bolton Wanderers FC",
    "Wigan": "Wigan Athletic FC",
    "Blackpool": "Blackpool FC",
    "Charlton": "Charlton Athletic FC",
    "Preston": "Preston North End FC",
    "Millwall": "Millwall FC",
    "Barnsley": "Barnsley FC",
    "Rotherham": "Rotherham United FC",
}


def _fuzzy(name: str, candidates: list[str], threshold: float = 0.75) -> str | None:
    """Match an odds-API name to a canonical DB name."""
    if name in ALIASES:
        return ALIASES[name]
    if name in candidates:
        return name
    best_score = 0.0
    best = None
    for c in candidates:
        c_clean = c.replace(" FC", "").replace(" AFC", "")
        score = SequenceMatcher(None, name.lower(), c_clean.lower()).ratio()
        if score > best_score:
            best_score = score
            best = c
    return best if best_score >= threshold else None


async def load_odds_for_competition(
    session: AsyncSession,
    *,
    competition_code: str,
    api_key: str,
    days_ahead: int = 14,
) -> dict:
    """Fetch live odds and store them as external_predictions."""
    source_row = (await session.execute(sql_text("""
        SELECT id::text FROM external_prediction_sources
        WHERE slug = 'the_odds_api'
    """))).first()
    if not source_row:
        log.warning("odds_loader.source_not_found")
        return {"status": "source_missing"}
    source_id = source_row[0]

    # Fetch from API
    odds = await fetch_odds_for_competition(
        competition_code=competition_code, api_key=api_key, days_ahead=days_ahead,
    )
    log.info("odds_loader.fetched", count=len(odds))

    if not odds:
        return {"status": "no_odds", "inserted": 0}

    # Load candidate match names from DB
    candidate_rows = (await session.execute(sql_text("""
        SELECT DISTINCT team FROM (
            SELECT home_team AS team FROM matches WHERE competition = :comp
            UNION
            SELECT away_team AS team FROM matches WHERE competition = :comp
        ) t
    """), {"comp": "EPL"})).all()
    candidates = [r[0] for r in candidate_rows]

    # Load upcoming matches to match against
    cutoff = datetime.now(UTC) + timedelta(days=days_ahead)
    match_rows = (await session.execute(sql_text("""
        SELECT id::text, home_team, away_team, kickoff_at
        FROM matches
        WHERE competition = :comp
          AND status = 'scheduled'
          AND kickoff_at >= now()
          AND kickoff_at <= :cutoff
    """), {"comp": "EPL", "cutoff": cutoff})).all()

    inserted = 0
    skipped_no_match = 0
    skipped_no_team = 0

    for op in odds:
        # Match the odds-API teams to our DB names
        home_full = _fuzzy(op.home_team, candidates)
        away_full = _fuzzy(op.away_team, candidates)
        if not home_full or not away_full:
            skipped_no_team += 1
            log.info("odds_loader.unknown_team", home=op.home_team, away=op.away_team)
            continue

        # Find the DB match
        db_match = None
        for mid, h, a, kick in match_rows:
            if h == home_full and a == away_full:
                # Also require kickoff times within 24h (safety)
                delta = abs((kick - op.kickoff_at).total_seconds())
                if delta <= 86400:
                    db_match = mid
                    break

        if not db_match:
            skipped_no_match += 1
            continue

        # Upsert
        await session.execute(sql_text("""
            INSERT INTO external_predictions (
                source_id, match_id, prob_home_win, prob_draw, prob_away_win,
                prob_over_2_5, raw_payload, fetched_at
            ) VALUES (
                :sid, :mid, :ph, :pd, :pa,
                :pov, CAST(:raw AS jsonb), now()
            )
            ON CONFLICT (source_id, match_id) DO UPDATE
            SET prob_home_win = EXCLUDED.prob_home_win,
                prob_draw = EXCLUDED.prob_draw,
                prob_away_win = EXCLUDED.prob_away_win,
                prob_over_2_5 = EXCLUDED.prob_over_2_5,
                raw_payload = EXCLUDED.raw_payload,
                fetched_at = now()
        """), {
            "sid": source_id, "mid": db_match,
            "ph": op.prob_home_win, "pd": op.prob_draw, "pa": op.prob_away_win,
            "pov": op.prob_over_2_5,
            "raw": json.dumps({
                "bookmaker_count": op.bookmakers_used,
                "external_id": op.external_id,
                **(op.raw_payload or {}),
            }),
        })
        inserted += 1

    await session.commit()

    return {
        "status": "ok",
        "fetched": len(odds),
        "inserted": inserted,
        "skipped_no_match": skipped_no_match,
        "skipped_no_team": skipped_no_team,
    }
