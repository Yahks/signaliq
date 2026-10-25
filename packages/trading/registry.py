"""Content registry — picks the next unused coordinate.

Every piece is Dimension × Angle. 14 × 10 = 140 unique
coordinates.

The "used" set is derived from trading_posts on every call.
There is no state file. A coordinate is used if and only if
an article with that coordinate exists in the DB for the org.
A crashed run does not burn a coordinate. Deleting a post
frees its coordinate.
"""
from __future__ import annotations

from sqlalchemy import text as sql_text

from packages.core.logging import get_logger

log = get_logger(__name__)


# Each subblock may only use a slice of the ten angles. This
# partitions the 140 coordinates (14 dims x 10 angles) across the
# four concept-explainer subblocks so no two ever write the same
# coordinate. Total angles: 3 + 3 + 2 + 2 = 10.
SUBBLOCK_ANGLES: dict[str, tuple[str, ...]] = {
    "class_room":         tuple(f"A{i}" for i in range(1, 11)),  # all angles → 140 topics
    "trading_psychology": ("A7", "A10"),        # tension, failure
    "trading_styles":     tuple(f"A{i}" for i in range(1, 11)),  # all angles → 140 topics
    "trading_tools":      tuple(f"A{i}" for i in range(1, 11)),  # all angles → 140 topics
    # Practitioner registers — post-mortem shape. The person
    # map in each register requires a first-person mistake or
    # tension. A1 (reveal), A2 (myth-bust), A4 (comparison),
    # A5 (research), A6 (tool), A8 (scale), A9 (beginner) all
    # produce a different shape and fail the register.
    "risk_management":       ("A3",),   # the mistake — locked
    "investment_strategies": ("A3", "A7", "A10"),
    "technical_indicators":  ("A3", "A7", "A10"),
    "screener_picks":        ("A3", "A7", "A10"),
}


DIMENSIONS: dict[str, dict[str, str]] = {
    "D1":  {"label": "the daily scan",           "keywords": "data quality"},
    "D2":  {"label": "entry logic",              "keywords": "backtest"},
    "D3":  {"label": "position sizing",          "keywords": "position sizing"},
    "D4":  {"label": "exit logic",               "keywords": "backtest"},
    "D5":  {"label": "rolling",                  "keywords": "tool review"},
    "D6":  {"label": "holding periods",          "keywords": "style"},
    "D7":  {"label": "multi-strategy blending",  "keywords": "systematic"},
    "D8":  {"label": "the human overlay",        "keywords": "psychology"},
    "D9":  {"label": "research-to-rules",        "keywords": "methodology"},
    "D10": {"label": "stocks vs options",        "keywords": "tool review"},
    "D11": {"label": "universes",                "keywords": "data quality"},
    "D12": {"label": "the no-backtest question", "keywords": "backtest"},
    "D13": {"label": "emotion removal",          "keywords": "psychology"},
    "D14": {"label": "the operation as a whole", "keywords": "systematic"},
}

ANGLES: dict[str, dict[str, str]] = {
    "A1":  {"label": "the reveal",       "framing": "Here is exactly what I do."},
    "A2":  {"label": "the myth-bust",    "framing": "Everyone says X. Here is why I do not."},
    "A3":  {"label": "the mistake",      "framing": "I got this wrong for a year."},
    "A4":  {"label": "the comparison",   "framing": "Discretionary vs. my system."},
    "A5":  {"label": "the research",     "framing": "The paper that inspired this rule."},
    "A6":  {"label": "the tool",         "framing": "How I scan, roll, size."},
    "A7":  {"label": "the tension",      "framing": "The one place I still use judgment."},
    "A8":  {"label": "the scale",        "framing": "Why AQR does this with billions."},
    "A9":  {"label": "the beginner",     "framing": "If you are starting from zero."},
    "A10": {"label": "the failure mode", "framing": "How this blows up."},
}


async def _last_process_save(session, org_id: str):
    """Return the timestamp of the most recent trader_process_history
    row for the org, or None if none exist.

    The save is the boundary between cycles. A coordinate is only
    "used" if it was written since the last save.
    """
    from sqlalchemy import text as sql_text
    row = (await session.execute(sql_text("""
        SELECT MAX(created_at) FROM trader_process_history
        WHERE org_id = :oid
    """), {"oid": org_id})).first()
    return row[0] if row and row[0] else None


async def used_coordinates(
    session, *, org_id: str, subblock_id: str,
) -> set[str]:
    """The set of coordinates already written for this org + subblock
    SINCE the last process save.

    If the process has never been versioned (no history rows), falls
    back to counting every article ever written for the subblock.

    The cycle boundary is the last process save. Every save opens a
    fresh 140-coordinate cycle, sourced from the newest process.
    """
    from sqlalchemy import text as sql_text
    last_save = await _last_process_save(session, org_id)

    if last_save is None:
        # Never versioned. Count everything ever written.
        rows = (await session.execute(sql_text("""
            SELECT DISTINCT source_payload->>'coordinate'
            FROM trading_posts
            WHERE org_id = :oid
              AND subblock_id = :sid
              AND deleted_at IS NULL
              AND source_payload->>'coordinate' IS NOT NULL
        """), {"oid": org_id, "sid": subblock_id})).all()
    else:
        rows = (await session.execute(sql_text("""
            SELECT DISTINCT source_payload->>'coordinate'
            FROM trading_posts
            WHERE org_id = :oid
              AND subblock_id = :sid
              AND deleted_at IS NULL
              AND created_at > :since
              AND source_payload->>'coordinate' IS NOT NULL
        """), {
            "oid": org_id, "sid": subblock_id, "since": last_save,
        })).all()

    return {r[0] for r in rows if r[0]}


async def pick_coordinate(
    session,
    *,
    org_id: str,
    subblock_id: str,
    subblock_slug: str | None = None,
    covered_dimensions: set[str] | None = None,
    allowed_angles: tuple[str, ...] | None = None,
) -> dict[str, str]:
    """Return the next unused coordinate for this org + subblock.

    If covered_dimensions is provided, only coordinates whose
    dimension is in that set are eligible.
    """
    used = await used_coordinates(
        session, org_id=org_id, subblock_id=subblock_id,
    )
    allowed_dims = _subblock_dimension_filter(subblock_slug)

    # If the caller didn't pass an explicit angle pool, fall back
    # to the subblock's own mapping. Without this, SUBBLOCK_ANGLES
    # is defined but never read, and the picker walks all ten
    # angles for a subblock that should only see three.
    if allowed_angles is None and subblock_slug:
        allowed_angles = SUBBLOCK_ANGLES.get(subblock_slug)

    for d_id, d_info in DIMENSIONS.items():
        if allowed_dims is not None and d_id not in allowed_dims:
            continue
        if covered_dimensions is not None and d_id not in covered_dimensions:
            continue
        for a_id, a_info in ANGLES.items():
            # S.9.08 — the subblock may only use a slice of angles.
            if allowed_angles is not None and a_id not in allowed_angles:
                continue
            coord = f"{d_id}-{a_id}"
            if coord in used:
                continue
            log.info("registry.picked", coord=coord,
                     label=f"{d_info['label']} / {a_info['label']}")
            return {
                "coord": coord,
                "dimension_id": d_id,
                "dimension_label": d_info["label"],
                "dimension_keywords": d_info["keywords"],
                "angle_id": a_id,
                "angle_label": a_info["label"],
                "angle_framing": a_info["framing"],
                "topic": f"{d_info['label']} — {a_info['label']}",
            }

    # Every allowed coordinate is used. Wrap around — take the first
    # coordinate that respects the SAME filters as the main loop:
    # allowed_dims, allowed_angles, covered_dimensions. Picking the
    # raw first D/A pair ignores the subblock's restrictions and
    # returns e.g. D1-A1 for a subblock locked to D7-A3/A7/A10.
    log.info("registry.exhausted",
             total=len(DIMENSIONS) * len(ANGLES))
    for d_id, d_info in DIMENSIONS.items():
        if allowed_dims is not None and d_id not in allowed_dims:
            continue
        if covered_dimensions is not None and d_id not in covered_dimensions:
            continue
        for a_id, a_info in ANGLES.items():
            if allowed_angles is not None and a_id not in allowed_angles:
                continue
            # First allowed coordinate. Use it (a repeat of an
            # earlier one, but at least a valid one for this subblock).
            log.info("registry.wrapped", coord=f"{d_id}-{a_id}")
            return {
                "coord": f"{d_id}-{a_id}",
                "dimension_id": d_id,
                "dimension_label": d_info["label"],
                "dimension_keywords": d_info["keywords"],
                "angle_id": a_id,
                "angle_label": a_info["label"],
                "angle_framing": a_info["framing"],
                "topic": f"{d_info['label']} — {a_info['label']}",
            }
    # No coordinate at all is allowed by the filters. This means
    # the subblock's config is broken — every dimension or angle
    # is filtered out. Fail loudly rather than returning something
    # out of spec.
    raise ValueError(
        f"pick_coordinate: no allowed coordinate for subblock "
        f"{subblock_slug!r} (dims={allowed_dims}, angles={allowed_angles})"
    )


async def remaining_count(
    session, *, org_id: str, subblock_id: str,
) -> int:
    used = await used_coordinates(
        session, org_id=org_id, subblock_id=subblock_id,
    )
    return len(DIMENSIONS) * len(ANGLES) - len(used)


# ─────────────────────────────────────────────────────────────────────
# Dimension coverage
# ─────────────────────────────────────────────────────────────────────

_DIMENSION_TO_SECTION = {
    "D1":  "daily_scan",
    "D2":  "entry_logic",
    "D3":  "position_sizing",
    "D4":  "exit_logic",
    "D5":  "rolling",
    "D6":  "holding_period",
    "D7":  "multi_strategy_blend",
    "D8":  "chart_overlay",
    "D9":  "research_to_rules",
    "D10": "stocks_vs_options",
    "D11": "universes",
    "D12": "no_backtest_rule",
    "D13": "emotion_removal",
    "D14": None,
}


async def covered_dimensions(session, org_id: str) -> set[str]:
    """Return the dimensions whose section has at least one entry."""
    _SECTIONS = (
        "daily_scan", "entry_logic", "position_sizing", "exit_logic",
        "holding_period", "research_to_rules", "chart_overlay",
        "no_backtest_rule", "multi_strategy_blend",
        "rolling", "stocks_vs_options", "universes", "emotion_removal",
    )
    row = (await session.execute(sql_text("""
        SELECT
            LENGTH(COALESCE(daily_scan, ''))           > 0 AS daily_scan,
            LENGTH(COALESCE(entry_logic, ''))          > 0 AS entry_logic,
            LENGTH(COALESCE(position_sizing, ''))      > 0 AS position_sizing,
            LENGTH(COALESCE(exit_logic, ''))           > 0 AS exit_logic,
            LENGTH(COALESCE(holding_period, ''))       > 0 AS holding_period,
            LENGTH(COALESCE(research_to_rules, ''))    > 0 AS research_to_rules,
            LENGTH(COALESCE(chart_overlay, ''))        > 0 AS chart_overlay,
            LENGTH(COALESCE(no_backtest_rule, ''))     > 0 AS no_backtest_rule,
            LENGTH(COALESCE(multi_strategy_blend, '')) > 0 AS multi_strategy_blend,
            LENGTH(COALESCE(rolling, ''))              > 0 AS rolling,
            LENGTH(COALESCE(stocks_vs_options, ''))    > 0 AS stocks_vs_options,
            LENGTH(COALESCE(universes, ''))            > 0 AS universes,
            LENGTH(COALESCE(emotion_removal, ''))      > 0 AS emotion_removal
        FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL
        LIMIT 1
    """), {"oid": org_id})).first()
    if not row:
        return set()
    filled_sections = {name for name, val in zip(_SECTIONS, row) if val}

    covered = set()
    for dim, section in _DIMENSION_TO_SECTION.items():
        if section is None:
            if all(s in filled_sections for s in {
                "daily_scan", "entry_logic", "position_sizing",
                "exit_logic", "holding_period", "research_to_rules",
                "chart_overlay", "no_backtest_rule",
                "multi_strategy_blend",
            }):
                covered.add(dim)
        elif section in filled_sections:
            covered.add(dim)
    return covered

# ─────────────────────────────────────────────────────────────────────
# Fundamental metrics coordinates — 8 evergreen essays for the
# fundamental_metrics subblock. No wrap: after all 8 are used for a
# process version, the subblock stops until the trader saves a new
# process (which starts a new cycle).
# ─────────────────────────────────────────────────────────────────────

FUNDAMENTAL_METRICS_COORDINATES: dict[str, str] = {
    "FM1": "why trend persistence varies by regime",
    "FM2": "why mean reversion works in compressed volatility",
    "FM3": "why correlation rises in drawdowns",
    "FM4": "why diversification fails in stress",
    "FM5": "why breadth narrows before declines",
    "FM6": "why gaps cluster",
    # The spread is four states, not one. Each coordinate is a
    # single path with a single direction (see
    # fundamental_signal_states); an article covering "the spread"
    # in general would have to hold two contradictory theses.
    "FM7a": "why widening backwardation is more bullish, not less",
    "FM7b": "why narrowing backwardation toward contango erodes "
            "the bullish bias",
    "FM7c": "why widening contango is more bearish",
    "FM7d": "why narrowing contango toward backwardation erodes "
            "the bearish bias",
    "FM8": "why momentum decays",
}

# Which signal state each spread coordinate refers to.
FM_COORDINATE_STATE: dict[str, tuple[str, str]] = {
    "FM7a": ("spread", "backwardation_widening"),
    "FM7b": ("spread", "backwardation_narrowing"),
    "FM7c": ("spread", "contango_widening"),
    "FM7d": ("spread", "contango_narrowing"),
}


async def pick_fm_coordinate(
    session, *, org_id: str, subblock_id: str,
) -> dict[str, str] | None:
    """Return the next unused FM coordinate for the org + subblock.

    No-wrap. After 8 are used, returns None.

    Cycle boundary: the last trader_process_history row for the org.
    Only FM coordinates written since the last process save count as
    used. A new process save resets the 8-coordinate cycle.
    """
    from sqlalchemy import text as sql_text

    # Cycle boundary — the last process save.
    last_save = await _last_process_save(session, org_id)

    if last_save is None:
        rows = (await session.execute(sql_text("""
            SELECT DISTINCT source_payload->>'coordinate'
            FROM trading_posts
            WHERE org_id = :oid
              AND subblock_id = :sid
              AND deleted_at IS NULL
              AND source_payload->>'coordinate' LIKE 'FM%'
        """), {"oid": org_id, "sid": subblock_id})).all()
    else:
        rows = (await session.execute(sql_text("""
            SELECT DISTINCT source_payload->>'coordinate'
            FROM trading_posts
            WHERE org_id = :oid
              AND subblock_id = :sid
              AND deleted_at IS NULL
              AND created_at > :since
              AND source_payload->>'coordinate' LIKE 'FM%'
        """), {
            "oid": org_id, "sid": subblock_id, "since": last_save,
        })).all()

    used = {r[0] for r in rows if r[0]}
    for code, label in FUNDAMENTAL_METRICS_COORDINATES.items():
        if code in used:
            continue
        log.info("registry.picked_fm", coord=code, label=label)
        return {"coord": code, "label": label}

    log.info("registry.fm_exhausted",
             total=len(FUNDAMENTAL_METRICS_COORDINATES))
    return None

# Which dimensions each process-based subblock may use. The concept-
# explainer four use all 14; the practitioner-process four use only
# the sections their topic reads from.
SUBBLOCK_DIMENSIONS: dict[str, tuple[str, ...]] = {
    # concept-explainer — all 14
    "class_room":            tuple(f"D{i}" for i in range(1, 15)),
    "trading_styles":        tuple(f"D{i}" for i in range(1, 15)),
    "trading_psychology":    ("D8", "D13"),         # human overlay, emotion removal
    "trading_tools":         tuple(f"D{i}" for i in range(1, 15)),
    # practitioner-process — the specific sections each topic reads
    "risk_management":       ("D1", "D2", "D7", "D8"),   # daily_scan, entry_logic, risk_behavior, drawdown
    "screener_picks":        ("D1",),               # daily_scan
    "technical_indicators":  ("D2", "D8"),          # entry_logic, chart_overlay
    "investment_strategies": ("D7",),               # multi_strategy_blend
}


def _subblock_dimension_filter(
    slug: str | None,
    explicit: set[str] | None = None,
) -> set[str] | None:
    """Return the set of dimension IDs this subblock may use, or None
    for no filter."""
    if explicit is not None:
        return explicit
    if slug and slug in SUBBLOCK_DIMENSIONS:
        return set(SUBBLOCK_DIMENSIONS[slug])
    return None

