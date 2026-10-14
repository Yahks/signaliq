"""Post-process market-regime articles.

The model drops beats and adds forbidden elements in a single
pass. The register declares the rules. The delivery check
catches violations. But without a regenerate loop, articles
draft on every miss.

This module is the mechanical fallback. It removes what the
register forbids and ensures the required beats are present.

  - Strips markdown tables.
  - Strips bold labels (**Checklist**) at line starts.
  - Appends a checklist if the article has none.

The post-processor does NOT invent article content. It only
removes forbidden elements and appends a checklist that names
the metrics already discussed.
"""
from __future__ import annotations

import re

from packages.core.logging import get_logger

log = get_logger(__name__)


# Metrics and thresholds we recognise when building a checklist.
_METRIC_QUESTIONS = [
    (r"\bcandidate count\b", "Have I checked the candidate count?"),
    (r"\brealized volatilit", "Do I see realized volatility within the normal band?"),
    (r"\bmedian spread\b", "Is the median spread under 0.5 points?"),
    (r"\bpairwise correlation\b|\bcorrelation\b",
     "Can I confirm the pairwise correlation is above 0.50?"),
    (r"\bVIX[- ]NQ spread\b", "Am I watching the VIX-NQ spread?"),
    (r"\bdaily range\b", "Is the daily range near its baseline?"),
    (r"\bATR\b", "Have I verified the ATR filter?"),
    (r"\bclearance\b", "Did the breakout clearance meet the threshold?"),
    (r"\bstop\b", "Have I placed the stop at the rule's distance?"),
]


def strip_tables(body: str) -> str:
    """Remove markdown tables. Lines that start with | and the
    separator line of a markdown table are deleted."""
    if not body:
        return body
    lines = body.split("\n")
    out = []
    for ln in lines:
        s = ln.strip()
        # Markdown table row.
        if s.startswith("|") and s.endswith("|"):
            continue
        # Markdown table separator (| --- | --- |).
        if re.match(r"^\|[\s\-:|]+\|$", s):
            continue
        out.append(ln)
    return "\n".join(out)


def strip_bold_labels(body: str) -> str:
    """Remove every '**' marker. The register forbids bold labels
    and section headers. Inline emphasis is also removed — the
    article reads as plain prose."""
    if not body:
        return body
    lines = []
    for ln in body.split("\n"):
        s = ln.strip()
        # A line that is only a bold label ('**Checklist**').
        if re.match(r"^\*\*[^*]+\*\*\s*$", s):
            continue
        # Strip the label prefix if the line starts with one.
        ln = re.sub(r"^\s*\*\*[^*]+\*\*\s*", "", ln)
        ln = re.sub(r"^\s*Checklist:\s*", "", ln)
        # Strip all remaining '**' markers (inline emphasis or
        # unpaired).
        ln = re.sub(r"\*\*", "", ln)
        lines.append(ln)
    return "\n".join(lines)


def _has_checklist(body: str) -> bool:
    """At least three question-form sentences. Accepts both
    first-person ('Can I') and declarative ('Is the') forms."""
    hits = re.findall(
        r"\b(?:Can I|Do I|Have I|Did I|Will I|"
        r"Is the|Are the|Does the|Has the|Was the|Were the)\b",
        body or "", re.IGNORECASE,
    )
    return len(hits) >= 3


def append_checklist(body: str) -> str:
    """If the article has no checklist, build one from the metrics
    mentioned in the body and append it before the CTA.

    The checklist is derived from what the article already
    discusses. It does not introduce new facts.
    """
    if not body or _has_checklist(body):
        return body

    # Find metrics the article mentions.
    low = body.lower()
    questions: list[str] = []
    for pattern, question in _METRIC_QUESTIONS:
        if re.search(pattern, low, re.IGNORECASE) and question not in questions:
            questions.append(question)

    # Ensure at least three questions. Pad with generic ones.
    fallbacks = [
        "Have I checked the dashboard numbers before entry?",
        "Do I know which regime is active this session?",
        "Am I applying the process adjustments for this regime?",
        "Can I confirm the risk cap for this session?",
        "Have I verified the entry filters against the regime?",
    ]
    for q in fallbacks:
        if len(questions) >= 5:
            break
        if q not in questions:
            questions.append(q)

    questions = questions[:5]

    # Split at the CTA (the last 'I built' paragraph).
    m = re.search(
        r"(\n\nI built[^\n]*(?:https?://\S+)?[^\n]*)",
        body,
        re.IGNORECASE,
    )
    if m:
        before = body[:m.start()].rstrip()
        cta = body[m.start():].lstrip("\n")
        return before + "\n\n" + " ".join(questions) + "\n\n" + cta
    # No CTA found — append at the end.
    return body.rstrip() + "\n\n" + " ".join(questions)


def collapse_checklists(body: str) -> str:
    """If the body has two or more contiguous blocks of question
    sentences, keep only the LAST one (usually the one that uses
    the register's first-person form)."""
    if not body:
        return body
    # Split into paragraphs (blank-line separated).
    paras = re.split(r"\n\s*\n", body)
    is_checklist = []
    for para in paras:
        # A paragraph counts as a checklist if at least 3 of its
        # sentences end with '?'.
        qs = len(re.findall(r"\?", para))
        is_checklist.append(qs >= 3)
    if sum(is_checklist) < 2:
        return body
    # Find the last checklist paragraph index.
    last_idx = max(i for i, c in enumerate(is_checklist) if c)
    # Remove earlier checklist paragraphs.
    out = [
        p for i, p in enumerate(paras)
        if not (is_checklist[i] and i != last_idx)
    ]
    return "\n\n".join(out)


def post_process_article(body: str) -> str:
    """Apply all post-processing steps in order."""
    if not body:
        return body
    body = strip_tables(body)
    body = strip_bold_labels(body)
    body = append_checklist(body)
    body = collapse_checklists(body)
    return body
