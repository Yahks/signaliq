"""Seed and query the macro_events table.

FOMC, CPI, NFP, GDP dates are known in advance. The list is
maintained here. When a new year's calendar is published, add
to the list and re-run seed.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession


# Static list of macro event dates. Update annually.
# Source: federalreserve.gov (FOMC), bls.gov (CPI, NFP),
# bea.gov (GDP, PCE).
_EVENTS: list[tuple[date, str, str]] = [
    # FOMC meeting dates 2025
    (date(2025, 1, 29), "FOMC", "January FOMC"),
    (date(2025, 3, 19), "FOMC", "March FOMC"),
    (date(2025, 5, 7),  "FOMC", "May FOMC"),
    (date(2025, 6, 18), "FOMC", "June FOMC"),
    (date(2025, 7, 30), "FOMC", "July FOMC"),
    (date(2025, 9, 17), "FOMC", "September FOMC"),
    (date(2025, 11, 5), "FOMC", "November FOMC"),
    (date(2025, 12, 17),"FOMC", "December FOMC"),
    # CPI release dates 2025
    (date(2025, 1, 15), "CPI", "December 2024 CPI"),
    (date(2025, 2, 12), "CPI", "January CPI"),
    (date(2025, 3, 12), "CPI", "February CPI"),
    (date(2025, 4, 10), "CPI", "March CPI"),
    (date(2025, 5, 13), "CPI", "April CPI"),
    (date(2025, 6, 11), "CPI", "May CPI"),
    (date(2025, 7, 11), "CPI", "June CPI"),
    (date(2025, 8, 12), "CPI", "July CPI"),
    (date(2025, 9, 11), "CPI", "August CPI"),
    (date(2025, 10, 15),"CPI", "September CPI"),
    (date(2025, 11, 13),"CPI", "October CPI"),
    (date(2025, 12, 18),"CPI", "November CPI"),
    # NFP (first Friday of each month, roughly)
    (date(2025, 1, 3),  "NFP", "December 2024 NFP"),
    (date(2025, 2, 7),  "NFP", "January NFP"),
    (date(2025, 3, 7),  "NFP", "February NFP"),
    (date(2025, 4, 4),  "NFP", "March NFP"),
    (date(2025, 5, 2),  "NFP", "April NFP"),
    (date(2025, 6, 6),  "NFP", "May NFP"),
    (date(2025, 7, 3),  "NFP", "June NFP"),
    (date(2025, 8, 1),  "NFP", "July NFP"),
    (date(2025, 9, 5),  "NFP", "August NFP"),
    (date(2025, 10, 3), "NFP", "September NFP"),
    (date(2025, 11, 7), "NFP", "October NFP"),
    (date(2025, 12, 5), "NFP", "November NFP"),
    # 2026 FOMC
    (date(2026, 1, 28), "FOMC", "January 2026 FOMC"),
    (date(2026, 3, 18), "FOMC", "March 2026 FOMC"),
    (date(2026, 4, 29), "FOMC", "April 2026 FOMC"),
    (date(2026, 6, 17), "FOMC", "June 2026 FOMC"),
    (date(2026, 7, 29), "FOMC", "July 2026 FOMC"),
    (date(2026, 9, 16), "FOMC", "September 2026 FOMC"),
    (date(2026, 11, 4), "FOMC", "November 2026 FOMC"),
    (date(2026, 12, 16),"FOMC", "December 2026 FOMC"),
]


async def seed_macro_events(session: AsyncSession) -> int:
    """Insert any missing macro events. Idempotent."""
    existing = set(
        r[0] for r in (await session.execute(sql_text(
            "SELECT event_date FROM macro_events"
        ))).all()
    )
    inserted = 0
    for d, kind, desc in _EVENTS:
        if d in existing:
            continue
        await session.execute(sql_text("""
            INSERT INTO macro_events (event_date, event_type, description)
            VALUES (:d, :k, :desc)
        """), {"d": d, "k": kind, "desc": desc})
        inserted += 1
    await session.commit()
    return inserted


async def get_events_in_range(
    session: AsyncSession, *, start: date, end: date,
) -> list[dict]:
    rows = (await session.execute(sql_text("""
        SELECT event_date, event_type, description
        FROM macro_events
        WHERE event_date BETWEEN :s AND :e
        ORDER BY event_date
    """), {"s": start, "e": end})).mappings().all()
    return [dict(r) for r in rows]
