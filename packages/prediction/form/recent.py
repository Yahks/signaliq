"""Compute recent form (last N league matches) per team.

For each team at a reference date, look back at their last N league
matches and derive:
  - goals_for_avg     (avg goals scored in those matches)
  - goals_against_avg (avg goals conceded)
  - points_per_game   (0-3 scale)
  - attack_residual   (goals_for_avg - team's long-run goals_for_avg)
  - defense_residual  (goals_against_avg - team's long-run goals_against_avg)

Residuals are the key signal — how much better/worse is the team RIGHT NOW
compared to its usual level. This is what we feed into Dixon-Coles v2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class FormSnapshot:
    team: str
    competition: str
    as_of_date: date
    matches_considered: int
    goals_for_avg: float
    goals_against_avg: float
    points_per_game: float
    attack_residual: float
    defense_residual: float
    raw_recent_matches: list[dict] = field(default_factory=list)


async def compute_form_for_team(
    session: AsyncSession,
    *,
    team: str,
    competition: str,
    as_of: datetime | None = None,
    window: int = 5,
) -> FormSnapshot | None:
    """Compute form for one team using the last N league matches before `as_of`."""
    as_of = as_of or datetime.now(UTC)

    # Pull the team's last N matches (home or away)
    rows = (await session.execute(sql_text("""
        SELECT
            kickoff_at, home_team, away_team,
            home_goals, away_goals
        FROM matches
        WHERE status = 'finished'
          AND competition = :comp
          AND kickoff_at < :as_of
          AND (home_team = :team OR away_team = :team)
          AND home_goals IS NOT NULL
        ORDER BY kickoff_at DESC
        LIMIT :lim
    """), {
        "comp": competition, "as_of": as_of, "team": team, "lim": window,
    })).all()

    if not rows:
        return None

    goals_for = 0
    goals_against = 0
    points = 0
    raw_matches = []

    for r in rows:
        kickoff, home, away, hg, ag = r
        is_home = home == team
        gf = hg if is_home else ag
        ga = ag if is_home else hg
        goals_for += gf
        goals_against += ga
        if gf > ga:
            points += 3
        elif gf == ga:
            points += 1
        raw_matches.append({
            "date": kickoff.date().isoformat(),
            "home": home,
            "away": away,
            "score": f"{hg}-{ag}",
            "opponent": away if is_home else home,
            "venue": "H" if is_home else "A",
            "result": "W" if gf > ga else ("D" if gf == ga else "L"),
        })

    n = len(rows)
    gfa = goals_for / n
    gaa = goals_against / n
    ppg = points / n

    # Compute season baseline: ALL matches for the team up to as_of
    baseline = (await session.execute(sql_text("""
        SELECT
            avg(CASE WHEN home_team = :team THEN home_goals ELSE away_goals END) AS gf,
            avg(CASE WHEN home_team = :team THEN away_goals ELSE home_goals END) AS ga
        FROM matches
        WHERE status = 'finished'
          AND competition = :comp
          AND kickoff_at < :as_of
          AND (home_team = :team OR away_team = :team)
          AND home_goals IS NOT NULL
    """), {"comp": competition, "as_of": as_of, "team": team})).first()

    baseline_gf = float(baseline[0] or 0.0)
    baseline_ga = float(baseline[1] or 0.0)

    attack_residual = gfa - baseline_gf
    defense_residual = gaa - baseline_ga

    return FormSnapshot(
        team=team,
        competition=competition,
        as_of_date=as_of.date(),
        matches_considered=n,
        goals_for_avg=round(gfa, 3),
        goals_against_avg=round(gaa, 3),
        points_per_game=round(ppg, 3),
        attack_residual=round(attack_residual, 3),
        defense_residual=round(defense_residual, 3),
        raw_recent_matches=raw_matches,
    )


async def compute_form_for_competition(
    session: AsyncSession,
    *,
    competition: str = "EPL",
    as_of: datetime | None = None,
    window: int = 5,
) -> dict[str, FormSnapshot]:
    """Compute form for all teams in a competition."""
    as_of = as_of or datetime.now(UTC)

    teams = (await session.execute(sql_text("""
        SELECT DISTINCT team FROM (
            SELECT home_team AS team FROM matches
            WHERE status='finished' AND competition = :comp
            UNION
            SELECT away_team AS team FROM matches
            WHERE status='finished' AND competition = :comp
        ) t
        ORDER BY team
    """), {"comp": competition})).all()

    out: dict[str, FormSnapshot] = {}
    for (team,) in teams:
        snap = await compute_form_for_team(
            session, team=team, competition=competition, as_of=as_of, window=window,
        )
        if snap:
            out[team] = snap

    log.info("form.computed", competition=competition, teams=len(out))
    return out


async def save_form_snapshots(
    session: AsyncSession,
    snapshots: list[FormSnapshot],
) -> int:
    """Upsert snapshots into the DB."""
    import json
    saved = 0
    for s in snapshots:
        await session.execute(sql_text("""
            INSERT INTO form_snapshots (
                team, competition, as_of_date, matches_considered,
                goals_for_avg, goals_against_avg, points_per_game,
                attack_residual, defense_residual, raw_recent_matches
            ) VALUES (
                :t, :c, :d, :n,
                :gfa, :gaa, :ppg,
                :ar, :dr, CAST(:raw AS jsonb)
            )
            ON CONFLICT (team, competition, as_of_date) DO UPDATE
            SET matches_considered = EXCLUDED.matches_considered,
                goals_for_avg = EXCLUDED.goals_for_avg,
                goals_against_avg = EXCLUDED.goals_against_avg,
                points_per_game = EXCLUDED.points_per_game,
                attack_residual = EXCLUDED.attack_residual,
                defense_residual = EXCLUDED.defense_residual,
                raw_recent_matches = EXCLUDED.raw_recent_matches,
                computed_at = now()
        """), {
            "t": s.team, "c": s.competition, "d": s.as_of_date, "n": s.matches_considered,
            "gfa": s.goals_for_avg, "gaa": s.goals_against_avg, "ppg": s.points_per_game,
            "ar": s.attack_residual, "dr": s.defense_residual,
            "raw": json.dumps(s.raw_recent_matches),
        })
        saved += 1
    await session.commit()
    return saved
