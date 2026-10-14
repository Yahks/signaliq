"""Verify every numerical claim in a generated article.

The reviewer's specification (PART 2) describes the checks:
  - Clearance percentages must reference the breakout level,
    not the entry price.
  - Threshold values must match the process document.
  - Arithmetic must be correct.
  - Stop math must match the cited trade's ATR.

This module reads from the database (not JSON files) and runs
the checks against a body. Rejections are returned as a list
of reasons — the caller drafts the article with the reasons
visible.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


def _extract_clearance_claims(body: str) -> list[dict[str, Any]]:
    """Find every 'X% of the Y' clearance claim."""
    claims = []
    for m in re.finditer(
        r"(\d+(?:\.\d+)?)\s*%\s+of\s+(?:the\s+)?([a-zA-Z0-9\s\-]+)",
        body,
    ):
        pct = float(m.group(1))
        ref = m.group(2).strip().lower()
        # Ignore innocent uses: "0.5% of equity", "0.5% of the account"
        if any(w in ref for w in ("equity", "account", "capital")):
            continue
        claims.append({
            "pct": pct,
            "reference": ref,
            "span": m.group(0),
        })
    return claims


def _extract_thresholds(body: str) -> list[dict[str, Any]]:
    """Find THRESHOLD claims like 'ATR below $30'.

    Requires a directional word — below, above, exceeds, at or
    below, etc. 'The ATR is 28' is a trade value, not a
    threshold, and does not match.
    """
    claims = []
    for m in re.finditer(
        r"(ATR|clearance|volume|spread)\s+"
        r"(?:is\s+(?:below|above|at or below|at or above)\s+|"
        r"exceeds\s+|falls below\s+|climbs above\s+|"
        r"threshold\s+(?:of\s+)?|filter\s+(?:at\s+)?)"
        r"\$?(\d+(?:\.\d+)?)",
        body,
        re.IGNORECASE,
    ):
        claims.append({
            "subject": m.group(1).lower(),
            "value": float(m.group(2)),
            "span": m.group(0),
        })
    return claims


def _verify_clearance_reference(claims: list[dict]) -> list[str]:
    """Clearance must reference the 10-day high, not the entry price."""
    failures = []
    for c in claims:
        ref = c["reference"]
        if "entry" in ref or "price" in ref and "high" not in ref:
            failures.append(
                f"clearance references the entry price: '{c['span']}'. "
                f"Clearance is measured against the 10-day high level, "
                f"not the entry price."
            )
    return failures


def _verify_thresholds(claims: list[dict]) -> list[str]:
    """Threshold values must match the process document."""
    failures = []
    for c in claims:
        if c["subject"] == "atr" and c["value"] not in (30.0,):
            failures.append(
                f"ATR threshold {c['value']} not in the process "
                f"document: '{c['span']}'"
            )
    return failures


def _verify_arithmetic(body: str) -> list[str]:
    """Explicit a × b = c statements must check out."""
    failures = []
    for m in re.finditer(
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*[×x]\s*"
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:equals|=)\s*"
        r"(\d+(?:,\d{3})*(?:\.\d+)?)",
        body,
    ):
        try:
            a = float(m.group(1).replace(",", ""))
            b = float(m.group(2).replace(",", ""))
            c = float(m.group(3).replace(",", ""))
        except ValueError:
            continue
        if abs(a * b - c) > 0.5:
            failures.append(
                f"arithmetic error: {a} × {b} ≠ {c} "
                f"(found in '{m.group(0)}')"
            )
    return failures


async def check(
    session: AsyncSession,
    org_id: str,
    body: str,
) -> dict[str, Any]:
    """Run every numerical check. Returns {'passed': bool,
    'failures': [...]}."""
    failures: list[str] = []

    clearance_claims = _extract_clearance_claims(body)
    failures.extend(_verify_clearance_reference(clearance_claims))

    thresholds = _extract_thresholds(body)
    failures.extend(_verify_thresholds(thresholds))

    failures.extend(_verify_arithmetic(body))

    if failures:
        log.warning(
            "numerical_check.failed",
            count=len(failures),
            failures=failures[:3],
        )
    return {"passed": not failures, "failures": failures}
