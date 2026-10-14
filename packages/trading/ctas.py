"""CTA picker — reads content/cta-library.md and selects a CTA."""
from __future__ import annotations

import re
from pathlib import Path

from packages.core.logging import get_logger

log = get_logger(__name__)

CTA_FILE = Path(__file__).resolve().parents[2] / "content" / "cta-library.md"


def _parse_library() -> dict[int, list[str]]:
    if not CTA_FILE.exists():
        log.warning("cta.library_missing", path=str(CTA_FILE))
        return {}
    src = CTA_FILE.read_text()
    groups: dict[int, list[str]] = {}
    current: int | None = None
    for line in src.splitlines():
        m = re.match(r"^## Group (\d+) —", line)
        if m:
            current = int(m.group(1))
            groups[current] = []
            continue
        if current is None:
            continue
        cta = re.match(r"^\d+\.\s+(.+)$", line.strip())
        if cta:
            groups[current].append(cta.group(1).strip())
    return groups


TOPIC_TO_GROUPS: list[tuple[tuple[str, ...], tuple[int, ...]]] = [
    (("data quality", "restatement", "survivorship", "ticker reuse",
      "point-in-time", "look-ahead", "universes"), (2, 1)),
    (("position sizing", "exposure", "risk per trade", "cap",
      "size", "sizing", "martingale"), (3, 2, 6)),
    (("backtest", "live", "slippage", "fill"), (1, 2)),
    (("overfitting", "parameter", "curve-fitting", "walk-forward",
      "audit"), (5, 2)),
    (("tool review", "engine", "platform", "rolling",
      "stocks vs options"), (3, 4)),
    (("psychology", "fear", "greed", "discipline", "revenge",
      "drawdown", "human overlay", "emotion"), (9, 9)),
    (("systematic", "methodology", "research"), (5, 2)),
    # Insider-filing articles want a methodology or sample offer,
    # not a subscribe pitch.
    (("insider", "form 4", "filing", "disclosure", "10b5-1",
      "13f"), (2, 5)),
]
DEFAULT_GROUPS = (5, 2, 1)


def pick_cta(topic: str, post_index: int = 0) -> str:
    groups = _parse_library()
    if not groups:
        return ""
    topic_lower = topic.lower()
    chosen: tuple[int, ...] = DEFAULT_GROUPS
    for keywords, group_nums in TOPIC_TO_GROUPS:
        if any(k in topic_lower for k in keywords):
            chosen = group_nums
            break
    from packages.core.config import get_settings
    _url = get_settings().product_url

    def _sub(text: str) -> str:
        return text.replace("[link]", _url)

    for gnum in chosen:
        ctas = groups.get(gnum, [])
        if ctas:
            return _sub(ctas[post_index % len(ctas)])
    for gnum in sorted(groups.keys()):
        if groups[gnum]:
            return _sub(groups[gnum][post_index % len(groups[gnum])])
    return ""
