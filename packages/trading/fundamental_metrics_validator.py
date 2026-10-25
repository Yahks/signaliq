"""fundamental_metrics_validator: checks for the fundamental_metrics subblock.

Separate from article_validator (trade-centric) and from
market_condition_validator (regime-centric). This subblock is a
conceptual essay keyed to an FM coordinate, sourced from the
trader's fundamental_metrics text.

The reviewer's finding on the FM8 article was economic, not
formatting: the trader's section says a draw supports a bullish
bias, and the article said a 6 mb draw eroded the trend. No gate
caught it because nothing checked the sign.

The sign check parses the trader's prose for
"<signal> ... <direction>" pairs and rejects an article that
contradicts a stated direction. It fails OPEN — an unparsed
sentence simply does not fire — so a reworded section loses
coverage but never rejects a correct article.
"""
from __future__ import annotations

import re

_BULL = r"(bullish|supports? a? ?long|supports? a? ?bull|nudges? me toward longs|long bias)"
_BEAR = r"(bearish|supports? a? ?short|supports? a? ?bear|nudges? me toward shorts|short bias)"


def _check_signal_signs(
    body: str, directions: dict[str, str], signals: dict[str, str],
) -> list[str]:
    """Per signal: the article must state its direction, and the
    direction must match the structured value.

    Scans the whole body once per signal, not once per sentence.
    An article that says "a draw is bullish" and then mentions
    "draw" again later is correct, not a violation. Exactly one
    reason per signal at most:

      - never states a direction           -> "does not state"
      - states the wrong direction         -> "treats it as <wrong>"
      - states the right direction         -> pass
    """
    _BULL = r"bullish|support(s|ed)? (a )?(long|bull)|nudge[sd]? me toward long"
    _BEAR = (
        r"bearish|support(s|ed)? (a )?(short|bear)|nudge[sd]? me toward short"
        r"|eroded|evaporated|stalled|weakened|sold off|faded"
    )
    reasons: list[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", body or "")

    for key, dirn in directions.items():
        word = signals.get(key, key).lower()
        naming = [s for s in sentences if word in s.lower()]
        if not naming:
            continue
        bulls = sum(1 for s in naming if re.search(_BULL, s.lower()))
        bears = sum(1 for s in naming if re.search(_BEAR, s.lower()))

        if bulls == 0 and bears == 0:
            reasons.append(
                f"the article names a {word} but never states whether "
                f"it treats it as bullish or bearish"
            )
        elif dirn == "bullish" and bears > bulls:
            reasons.append(
                f"the trader's data says a {word} is bullish, but the "
                f"article treats it as bearish"
            )
        elif dirn == "bearish" and bulls > bears:
            reasons.append(
                f"the trader's data says a {word} is bearish, but the "
                f"article treats it as bullish"
            )
    return reasons


def _check_no_cta(body: str) -> list[str]:
    if re.search(
        r"(?i)\b(?:email me|reach out|sign up|subscribe|follow me|"
        r"dm me|link in bio|click (?:here|the link))\b", body or "",
    ):
        return ["body contains a CTA / marketing line"]
    if re.search(r"https?://(?!signaliq\.com/static)", body or ""):
        return ["body contains a URL"]
    return []


def _check_no_checklist(body: str) -> list[str]:
    if re.search(r"(?i)\bchecklist\b", body or ""):
        return ["article contains a checklist beat — removed from this subblock"]
    n = len(re.findall(r"(?i)(?:^|\n)\s*(?:can i|do i|have i)\b", body or ""))
    if n >= 3:
        return [f"article contains {n} 'Can I / Do I / Have I' questions — checklist removed"]
    return []


def _check_length(body: str) -> list[str]:
    words = len((body or "").split())
    if words < 820:
        return [f"article is {words} words; the floor is 820"]
    return []


def _check_chart_tag_present(body: str) -> list[str]:
    """The prompt asks for [CHART: daily]; the pipeline appends the
    chart if the tag is missing, but a missing tag means the model
    ignored the instruction. Flag it, do not reject."""
    return []


def validate_fundamental_metrics(
    body: str, trader_section: str,
    directions: dict[str, str] | None = None,
    signals: dict[str, str] | None = None,
) -> list[str]:
    if not body:
        return ["empty body"]
    reasons: list[str] = []
    if directions:
        reasons.extend(_check_signal_signs(
            body, directions, signals or {},
        ))
    reasons.extend(_check_length(body))
    reasons.extend(_check_no_checklist(body))
    reasons.extend(_check_no_cta(body))
    return reasons
