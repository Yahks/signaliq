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
    """Per signal state: the first direction the article states for
    that state must match the table.

    Positional, not a vote. The sentence "Backwardation widening is
    bearish: the bullish bias is eroding" claims bearish — the word
    right after the signal name is the claim, and the later mention
    of "bullish" is part of the explanation, not a second claim.
    Counting direction words across the sentence made that a tie and
    passed it.
    """
    _BULL = r"bullish|\bbull\b|long bias|toward long"
    _BEAR = r"bearish|\bbear\b|short bias|toward short|erod(?:e|es|ed|ing)"
    reasons: list[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", body or "")

    for key, dirn in directions.items():
        word = signals.get(key, key).lower()
        # The article may say "backwardation widening" or "widening
        # backwardation" — the state words match in any order.
        _state_tokens = [t for t in word.split() if t]
        claimed = None
        for sent in sentences:
            s = sent.lower()
            if not all(t in s for t in _state_tokens):
                continue
            # The direction may precede or follow the signal name —
            # "widening contango is more bearish" states bearish just
            # as plainly as "bearish widening contango". Match the
            # whole sentence, and prefer the direction word nearest
            # the signal when both appear.
            pos = s.find(word)
            m_bull = re.search(_BULL, s)
            m_bear = re.search(_BEAR, s)
            if not m_bull and not m_bear:
                continue
            if m_bull and not m_bear:
                claimed = "bullish"
            elif m_bear and not m_bull:
                claimed = "bearish"
            else:
                claimed = (
                    "bullish"
                    if abs(m_bull.start() - pos) <= abs(m_bear.start() - pos)
                    else "bearish"
                )
            break

        if claimed is None:
            reasons.append(
                f"the article names {word} but never states whether it "
                f"treats it as bullish or bearish"
            )
        elif claimed != dirn:
            reasons.append(
                f"the trader's data says {word} is {dirn}, but the "
                f"article states it is {claimed}"
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


def _check_length(body: str, floor: int = 650) -> list[str]:
    words = len((body or "").split())
    if words < floor:
        return [f"article is {words} words; the floor is {floor}"]
    return []


def _check_chart_tag_present(body: str) -> list[str]:
    """The prompt asks for [CHART: daily]; the pipeline appends the
    chart if the tag is missing, but a missing tag means the model
    ignored the instruction. Flag it, do not reject."""
    return []


def _check_trade_cited(body: str, facts: dict | None) -> list[str]:
    """If a backtest exists, the article must cite its most recent
    trade: date, entry, stop, exit, and R."""
    if not facts:
        return []
    sb = facts.get("signal_backtest")
    if not sb or not sb.get("most_recent"):
        return []
    mr = sb["most_recent"]
    b = body or ""
    miss = []
    for key, val in (
        ("entry", mr.get("entry")), ("stop", mr.get("stop")),
        ("exit", mr.get("exit")),
    ):
        if val is None:
            continue
        if f"{float(val):.2f}" not in b and f"{float(val):g}" not in b:
            miss.append(key)
    if miss:
        return [f"backtest exists but the article does not cite the "
                f"most-recent trade's {', '.join(miss)}"]
    return []


def _check_paragraph_length(body: str) -> list[str]:
    paras = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
    short = [p for p in paras
             if not p.startswith(("|", "!", "<!--"))
             and "REFRAME" not in p
             and len(re.findall(r"[.!?](?:\s|$)", p)) < 2]
    if short:
        return [f"{len(short)} paragraph(s) have fewer than three "
                f"sentences"]
    return []



def _merge_short_paragraphs(body: str) -> str:
    """Merge any paragraph with fewer than three sentences into the
    next one. The model writes short bursts; merging is formatting,
    not content, and the model has already produced the prose.
    """
    paras = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
    if not paras:
        return body
    out: list[str] = []
    buf = ""
    for p in paras:
        buf = f"{buf} {p}".strip() if buf else p
        # Count sentences: split on . ! ? followed by space or end.
        n = 0
        for ch in buf:
            if ch in ".!?":
                n += 1
        if n >= 3:
            out.append(buf)
            buf = ""
    if buf:
        if out:
            out[-1] = f"{out[-1]} {buf}".strip()
        else:
            out.append(buf)
    return "\n\n".join(out)


def validate_fundamental_metrics(
    body: str, trader_section: str,
    directions: dict[str, str] | None = None,
    signals: dict[str, str] | None = None,
    facts: dict | None = None,
    is_conceptual: bool = False,
) -> list[str]:
    if not body:
        return ["empty body"]
    reasons: list[str] = []
    # Merge first: a short paragraph that the merger can join is a
    # formatting artefact, not a defect. Only a paragraph the merger
    # cannot join (the last one, if it cannot be merged forward) is
    # reported.
    body = _merge_short_paragraphs(body)
    reasons.extend(_check_paragraph_length(body))
    if directions:
        reasons.extend(_check_signal_signs(
            body, directions, signals or {},
        ))
    reasons.extend(_check_length(
        body, floor=(450 if is_conceptual else 650)))
    reasons.extend(_check_no_checklist(body))
    reasons.extend(_check_no_cta(body))
    reasons.extend(_check_trade_cited(body, facts))
    if facts and facts.get('math_block'):
        reasons.extend(_check_numbers_from_math(
            body, facts['math_block']))
    return reasons


def _check_numbers_from_math(body: str, math_block: str) -> list[str]:
    """Every number in the article must appear in the math block.

    The model does no arithmetic. signal_math computes R, percent of
    equity, stop multiple, payoff, breakeven, edge gap — and the
    article quotes them. A number that is not in the block was
    computed by the model, which is how the 40% breakeven and the
    0.2% spread swing got in.
    """
    if not math_block:
        return []
    allowed = set(re.findall(r"-?\d+(?:\.\d+)?", math_block))
    reasons: list[str] = []
    for m in re.finditer(r"\$?(-?\d[\d,]*(?:\.\d+)?)\s*(%|R\b|points?|dollars)?", body or ""):
        raw = m.group(1).replace(",", "")
        # bare integers 1-31 are dates/contracts/labels; skip them
        try:
            v = float(raw)
        except ValueError:
            continue
        if v.is_integer() and 0 < v < 32:
            continue
        if raw not in allowed and raw.rstrip("0").rstrip(".") not in allowed:
            reasons.append(f"number {raw} is not in the computed math block")
    return reasons

