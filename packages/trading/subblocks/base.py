"""Shared infrastructure for trading subblock generators.

Every subblock generator follows the same shape:
  1. Load subblock config (trading_subblocks row)
  2. Fetch data (scraper, yahoo, fred, edgar, or computed)
  3. Render a compact markdown prompt payload
  4. Call the reasoning LLM with the subblock's style_guide as system prompt
  5. Parse JSON response → TradingPost
  6. Persist to trading_posts with full source provenance

This module provides the pieces the generators share so each
generator file is 60-100 lines, not 300.
"""
from __future__ import annotations

import traceback as _TB

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger
from packages.trading.shared_standard import STANDARD

log = get_logger(__name__)

# S.6.4.6 — minimum acceptable length as a fraction of target_words.
# A 260-word target refuses anything under 130 words; a 150-word target
# refuses anything under 75. This catches refusals like
# "requires inspection of SEC filings" before verification, which would
# otherwise see a single true claim and accept the post.
# S.8.58 — the band is absolute, not ratio-based. Every article
# lands in 630-1200 words regardless of target. The ratio names
# are kept for backward compatibility but no longer drive the
# check; see _MIN_WORDS / _MAX_WORDS below.
_MIN_WORDS = 630
_MAX_WORDS = 1200
_MIN_WORDS_RATIO = _MIN_WORDS / 900   # 0.70 — legacy, unused

# S.8.49.5 — view registers tolerate this many verify-only flags.
# The empirical flag rate for view-register articles (cap-view,
# market-daily, viewpoint, macro-view) is 2-4 per 10 claims. Two
# is the threshold that publishes the majority and drafts the
# outliers. Hard failures (__length__, __delivery__, __too_short__)
# always draft regardless of this number.
_VIEW_FLAG_THRESHOLD = 2      # fallback when claim count is unavailable
# S.8.49.7 — the fixed threshold under-publishes long articles.
# At 30 claims, 8 flags is 22 of 30 grounded but 8 > 2 drafts
# it. Scale tolerance to the article: publish a view article
# when verify flags are at or below this fraction of its
# claims. Matches the empirical flag rate (0.20-0.30).
_VIEW_FLAG_RATE = 0.35
# S.8.49.11 — registers whose articles tolerate verify flags.
# Includes every *-view register plus the pre-existing
# opinionated registers that predate the view-register set.
_VIEW_REGISTERS = frozenset({
    # S.8.90 — collapsed to six registers. Every register now gets
    # the view-register tolerance: the articles carry real trades,
    # first-person reactions, and third-person facts in a tight
    # shape that verify treats with D-1 tolerance.
    "market-daily",
    "viewpoint",
    "screen-view",
    "insider-log",
    "macro-view",
    "concept-explainer",
})

# S.8.90.10 — the trader-voice registers produce short pieces.
# The register is tight (three numbers, no filler, first person).
# Typical output is 150-500 words. The 630 floor drafted every
# article. Lower the floor for the view registers to 350 — the
# shortest legitimate piece for market-daily is around 150-200
# words, and 350 is the safe boundary that lets real pieces
# publish while still drafting stubs.
_VIEW_REGISTER_FLOOR = 600


def _floor_for(register: str | None) -> int:
    """Return the minimum word count for a register.

    View registers (the trader-voice set) get a lower floor
    because the register's shape produces short pieces.
    practitioner-process produces short pieces on Groq — 280-560
    words typical. Its floor is 350 so real pieces publish.
    Everything else uses the default 630.
    """
    if register in ("practitioner-process", "ticker-note"):
        return 250   # ticker notes are short pieces
    if register == "practitioner-strategies":
        # Groq gpt-oss-120b writes 400-570 words on the
        # practitioner-* registers. The 650 target is aspirational;
        # the floor matches the model's real output.
        return 450
    if register == "practitioner-indicators":
        # Same family, same model ceiling. Floor 450.
        return 450
    if register == "practitioner-screen":
        # Same family. Floor 450.
        return 450
    if register == "practitioner-risk":
        # Groq gpt-oss-120b writes 450-600 words on this register.
        # Floor 450 so real pieces publish.
        return 450
    if register == "market-structure":
        return 400
    if register == "market-regime":
        # Target 700. Floor 450. The model writes 400-600 words
        # on this register before post-processing; the
        # post-processor adds the checklist.
        return 450
    if register == "market-breadth":
        # Same family. Floor 450.
        return 450
    if register == "market-signals":
        # The model writes 300-500 words on this register.
        # Floor 350.
        return 350
    if register == "market-cap":
        # Target 700. Floor 500.
        return 500
    if register == "stock-lists":
        # Target 700. Floor 500.
        return 500
    if register == "fundamental-metrics":
        # Target 700. Floor 500.
        return 500
    if register == "market-event":
        # Target 700. Floor 600.
        return 600
    if register == "asset-classes":
        # Target 700. Floor 550. The model writes 550-650 on
        # this register.
        return 550
    if register == "psychology-note":
        # S.9.25 — Groq writes 500-650 on this register. 525 was
        # arbitrary and drafted articles at 521-524 words. Floor
        # at 500, matching the other LLM subblocks.
        return 500
    if register == "concept-explainer":
        # Groq writes 450-600 words on this register. The 600
        # default floor drafted every piece. Floor at 500.
        return 500
    if register == "concept-style":
        # Groq writes 350-550 words on this register. The 450
        # floor drafted every piece. Floor at 350.
        return 350
    if register == "concept-class":
        # Same family. Floor 400.
        return 400
    if register == "concept-tools":
        # Same family. Floor 400.
        return 400
    if register == "insider-log":
        # Same family. Floor 400.
        return 400
    if register and register in _VIEW_REGISTERS:
        return _VIEW_REGISTER_FLOOR
    return _MIN_WORDS


# S.6.4.7 — output token budget for reasoning-model subblock generation.
# gpt-oss-20b is a reasoning model: max_tokens covers the hidden
# reasoning trace AND the visible content. At 3000, a 350-word target
# often consumed the whole budget on reasoning and returned empty
# content with finish_reason='length'. 6000 gives headroom for the
# reasoning trace plus the post. Do NOT lower this without checking
# the observed out_tokens of the longest subblock (class_room ~4600).
_MAX_TOKENS_LONG = 16000
_CEILING_RATIO = _MAX_WORDS / 900   # 1.333 — legacy, unused

# S.6.4.8 — verify output budget. The verifier returns
# 'UNSUPPORTED: NONE' or 'UNSUPPORTED: 1, 3, 7' — small. But the
# model may emit reasoning tokens before the answer, and 200
# truncated mid-response. When it truncates, the previous code
# returned (True, []) — fail-open. 800 gives headroom; the retry
# in verify_post uses 2x this if the first attempt is unparseable.
_MAX_TOKENS_VERIFY = 6000

# S.6.4.9 — sentinel returned by verify_post when both verify attempts
# failed. call_llm_json_verified checks for this BEFORE the regen
# branch: the regen prompt would otherwise include the literal string
# "verification_incomplete" as a "forbidden claim", which is
# meaningless to the model. Instead, mark the post unverified and let
# persist_post write it as a draft.
_VERIFICATION_INCOMPLETE = "__verification_incomplete__"


def _strip_label_prefix(s: str) -> str:
    """S.6.4.6 — drop a leading 'TITLE:' or 'BODY:' the model may echo."""
    t = (s or "").lstrip()
    for prefix in ("TITLE:", "BODY:"):
        if t.upper().startswith(prefix):
            t = t[len(prefix):].lstrip()
    return t


class DuplicatePostError(Exception):
    """Raised by persist_post when an identical post already exists
    within the dedup window. Callers treat this as a successful no-op:
    do not charge, do not write a second row, report 'duplicate' to the
    user."""


@dataclass
class TradingPost:
    title: str
    body: str
    word_count: int
    # Verification metadata — populated by verify_and_maybe_regenerate
    verification_ok: bool = True
    unsupported_claims: list[str] = None  # type: ignore[assignment]
    regenerated: bool = False
    # S.7.8b-4 — editorial metadata carried with the post so persist_post
    # can write form/tone/voice into source_payload without every
    # subblock having to remember to add them.
    form: str | None = None
    tone: str | None = None
    voice: str | None = None
    # S.8.49.6 — carried so persist_post can re-check length when
    # D-1 tolerance publishes a post that verify short-circuited.
    target_words: int | None = None
    # Subject rotation — the pipeline writes one subject per
    # article and reads the last N subjects to avoid repeats.
    subject: str | None = None
    # Coordinate — the composite (block/form) key that determines
    # the article. Recorded so the UI and the LRU selector can
    # see which coordinate was picked.
    coordinate: str | None = None

    def __post_init__(self) -> None:
        if self.unsupported_claims is None:
            self.unsupported_claims = []


# ─────────────────────────────────────────────────────────────────────
# Load subblock config
# ─────────────────────────────────────────────────────────────────────

async def load_subblock(
    session: AsyncSession,
    *,
    slug: str,
    org_id: str | None = None,
) -> dict[str, Any] | None:
    """Load a subblock row. Prefers org-specific override, falls back to global."""
    row = (await session.execute(sql_text("""
        SELECT
            ts.id::text, ts.org_id::text, ts.block_id::text,
            ts.slug, ts.name, ts.description, ts.category, ts.cadence,
            ts.source_kind, ts.theme, ts.style_guide, ts.training_config,
            ts.enabled, ts.sort_order
        FROM trading_subblocks ts
        WHERE ts.slug = :slug
          AND (ts.org_id = :org_id OR ts.org_id IS NULL)
        ORDER BY ts.org_id NULLS LAST
        LIMIT 1
    """), {"slug": slug, "org_id": org_id})).first()

    if not row:
        return None
    return {
        "id": row[0],
        "org_id": row[1],
        "block_id": row[2],
        "slug": row[3],
        "name": row[4],
        "description": row[5],
        "category": row[6],
        "cadence": row[7],
        "source_kind": row[8],
        "theme": row[9],
        "style_guide": row[10],
        "training_config": row[11],
        "enabled": row[12],
        "sort_order": row[13],
    }


# ─────────────────────────────────────────────────────────────────────
# Prompt helpers
# ─────────────────────────────────────────────────────────────────────

def render_markdown_table(
    rows: list[dict[str, Any]],
    columns: list[str],
    *,
    title: str | None = None,
) -> str:
    """Render a list of dicts as a markdown table for the LLM prompt."""
    if not rows:
        return f"### {title or 'Table'}\n(no data)"
    lines = [f"### {title}" if title else "", "", "| " + " | ".join(columns) + " |",
             "|" + "|".join(["---"] * len(columns)) + "|"]
    for r in rows:
        cells = [str(r.get(c, "") or "").replace("|", "\\|") for c in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join([line for line in lines if line])


def _extract_json_block(text: str) -> str:
    """Pull the first balanced JSON object out of a possibly-chatty response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("unbalanced JSON")


# ─────────────────────────────────────────────────────────────────────
# Text-protocol parsers (avoid Groq's JSON-mode token cap)
# ─────────────────────────────────────────────────────────────────────

_TITLE_RE = re.compile(r"^\s*TITLE\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_BODY_RE = re.compile(r"^\s*BODY\s*:\s*$", re.IGNORECASE | re.MULTILINE)


def _parse_title_body(text: str) -> tuple[str, str]:
    """Parse a 'TITLE: ...\nBODY:\n...' response.

    Fallbacks:
      - No TITLE: use the first non-empty line
      - No BODY: everything after the first blank line is the body
      - Neither: use the whole text as body with a generic title
    """
    text = text.strip()
    # Strip markdown fences if the model wrapped everything
    text = re.sub(r"^```(?:markdown|text)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)

    body_match = _BODY_RE.search(text)
    title_match = _TITLE_RE.search(text)

    if title_match and body_match and body_match.start() > title_match.end():
        title = title_match.group(1).strip().strip('"').strip("'")
        body = text[body_match.end():].lstrip("\n").strip()
        return title or "Untitled", body

    # Fallback: first line = title, rest = body
    lines = text.split("\n")
    if len(lines) >= 2 and lines[0].strip():
        title = lines[0].strip().lstrip("#").strip()
        # Find first blank line to split heading from body
        try:
            split_at = next(i for i in range(1, len(lines)) if not lines[i].strip())
            body = "\n".join(lines[split_at + 1:]).strip()
        except StopIteration:
            import traceback as _TBI
            _TBI.print_exc()
            body = "\n".join(lines[1:]).strip()
        return title[:500] or "Untitled", body

    return "Untitled", text


def _parse_unsupported_nums(text: str) -> list[int] | None:
    """Parse 'UNSUPPORTED: 1, 3, 7' or 'UNSUPPORTED: NONE'.

    Returns:
      - []          when the model explicitly said 'UNSUPPORTED: NONE'
      - [1, 3, 7]   when the model listed unsupported claim numbers
      - None        when the response does not contain an UNSUPPORTED:
                    marker at all (parse failure)

    S.6.4.8 — the None return is the fix. Previously, any response
    without the marker fell through to a digit-scan of the whole text,
    which almost always produced numbers that were out of range for
    the claim list. Those were filtered out downstream, and the result
    was (True, []) — a silent pass. Now an unparseable response is
    indistinguishable from a failure at the caller, so verify_post can
    retry or fail closed.
    """
    text = (text or "").strip()
    if not text:
        return None
    m = re.search(r"UNSUPPORTED\s*:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if not m:
        return None
    payload = m.group(1).strip()
    if not payload:
        return None
    upper = payload.upper()
    if upper.startswith("NONE") or upper.startswith("NO "):
        return []
    nums = re.findall(r"\b(\d+)\b", payload)
    if not nums:
        # 'UNSUPPORTED:' with a non-numeric, non-NONE payload — treat as
        # a parse failure. An empty list here would mean 'all supported',
        # which is a silent pass on garbage.
        return None
    return [int(n) for n in nums]


# ─────────────────────────────────────────────────────────────────────
# Verification — mirrors news chain D7
# ─────────────────────────────────────────────────────────────────────

_CLAIM_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


# ─────────────────────────────────────────────────────────────────────
# Ticker validation — reject preferreds, warrants, units, foreign junk
# ─────────────────────────────────────────────────────────────────────

_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z]{1,2})?$")


def is_valid_us_ticker(sym: str) -> bool:
    """Return True if `sym` looks like a normal US common-stock ticker.

    Accepts:
      AAPL, MSFT, BRK.B, NVDA, F

    Rejects:
      AAAPL, AAACG, AAAUC, AABAT     (preferred share variants)
      anything with 6+ letters       (usually warrants/units/preferreds)
      anything with non-alpha chars  (except a single dot for class shares)
    """
    if not sym:
        return False
    s = sym.strip().upper()
    if len(s) > 6:                       # BRK.B is 5 chars, allow up to 6 for safety
        return False
    if not _TICKER_RE.match(s):
        return False
    # Reject 3+ repeated leading letters (AAAxx pattern of preferreds)
    root = s.split(".")[0]
    if len(root) >= 4 and len(set(root[:3])) == 1:
        return False
    return True


# S.8.9.4 — opinion/advice markers. Sentences containing these are
# the writer's conclusions, not factual claims, and are skipped
# before verification.
_OPINION_MARKERS = (
    "investors should",
    "caution is warranted",
    "it is worth",
    "the takeaway",
    "in short",
    "in sum",
    "the bottom line",
    "watch for",
    "keep an eye",
    "monitor whether",
    "may be worth",
    "could be worth",
)

# A line that is entirely wrapped in **bold** is a subhead, not prose.
_BOLD_SUBHEAD_RE = None  # lazy-initialized below to keep import-time cheap


def _split_claims(body: str) -> list[str]:
    """Split a markdown body into atomic sentences for verification.

    Strips markdown artifacts (tables, headers, bullets, bold subheads)
    and drops opinion/advice sentences — the verifier only checks
    factual claims. Table cells are already checked against
    source_payload by their nature (they are copied).
    """
    import re as _re

    # Remove markdown table rows, code fences, headings, and bold subheads.
    lines: list[str] = []
    in_table = False
    in_code = False
    # S.8.85 — buffer the current table so each row can be
    # emitted as a claim. The pre-S.8.85 splitter dropped
    # every table row on the assumption that table cells were
    # copied verbatim from the source. They are not: subblocks
    # like insider-log synthesise a table by aggregating
    # individual Form 4 filings, and every row is a claim that
    # verify needs to check.
    table_header: list[str] = []
    table_rows: list[list[str]] = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            # Separator row (---|---|---) — skip, not data.
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                in_table = True
                table_header = cells
                continue
            table_rows.append(cells)
            continue
        if in_table:
            # Table block ended — emit one claim per row.
            for _row in table_rows:
                _pairs = [
                    f"{_h}: {_v}"
                    for _h, _v in zip(table_header, _row)
                    if _v and _v.lower() not in {"n/a", "—", "-"}
                ]
                if _pairs:
                    lines.append("; ".join(_pairs) + ".")
                    lines.append("\x00")
            table_header = []
            table_rows = []
            in_table = False
            # Fall through: this non-table line still needs processing.
        if s.startswith("#"):
            continue
        # A line that is entirely **bold** (optionally with a trailing
        # colon or question mark) is a subhead. Drop it.
        if _re.match(r"^\*\*[^*]+\*\*[?:]?\s*$", s):
            continue
        # Strip a leading bold label, keeping the content.
        # "**Thesis** - content" -> "content". Without this the
        # label sticks to the first sentence and verify sees it
        # as part of the claim.
        s = _re.sub(r"^\*\*[^*]+\*\*[\s–—:\-]*", "", s)
        if not s:
            continue
        if s.startswith("- ") or s.startswith("* "):
            s = s[2:]
        if not s:
            continue
        lines.append(s)
        # Preserve a paragraph break as a hard sentence boundary.
        # Without this, a **Counterpoint** paragraph merges with the
        # claim above it, producing one multi-paragraph "sentence".
        lines.append("\x00")

    # S.8.85 — flush a trailing table if the body ended inside one.
    if in_table and table_header and table_rows:
        for _row in table_rows:
            _pairs = [
                f"{_h}: {_v}"
                for _h, _v in zip(table_header, _row)
                if _v and _v.lower() not in {"n/a", "—", "-"}
            ]
            if _pairs:
                lines.append("; ".join(_pairs) + ".")
                lines.append("\x00")

    text = " ".join(lines)
    # Split into sentences. Paragraph-break sentinels force a
    # boundary even when the next paragraph starts with **bold**.
    sentences = [
        x.strip() for x in _re.split(r"\s*\x00\s*|(?<=[.!?])\s+(?=[A-Z])", text)
        if x.strip()
    ]

    # S.8.87 — re-join fragments split mid-abbreviation. "Corp. II"
    # is one sentence, not two; the period inside the abbreviation
    # is not a sentence boundary. Without this, a table row written
    # in prose ("Symbol: XRPN; Company: Armada Acquisition Corp. II;
    # % Change: +68.25%.") becomes four meaningless fragments, each
    # of which verify flags because it is not a complete claim.
    _ABBREV_ENDINGS = (
        "Corp.", "Inc.", "Ltd.", "Co.", "LLC.", "L.L.C.",
        "Jr.", "Sr.", "St.", "Dr.", "Mr.", "Mrs.", "Ms.",
        "U.S.", "U.K.", "E.U.", "No.", "Vol.", "vs.", "etc.",
    )
    _joined: list[str] = []
    for _s in sentences:
        if _joined and _joined[-1].rstrip().endswith(_ABBREV_ENDINGS):
            _joined[-1] = _joined[-1].rstrip() + " " + _s
        else:
            _joined.append(_s)
    sentences = _joined

    # Drop very short fragments.
    sentences = [s for s in sentences if len(s) > 20]
    # Drop opinion/advice sentences — they are not factual claims.
    sentences = [
        s for s in sentences
        if not any(m in s.lower() for m in _OPINION_MARKERS)
    ]
    return sentences


# ─────────────────────────────────────────────────────────────────────
# Verify directive (S.8.47 — short form)
# ─────────────────────────────────────────────────────────────────────
# The directive is deliberately one short rule. Earlier it had grown
# to 7 positive clauses and a negative test. At that length, a small
# verify model stops applying it and returns a sweeping verdict
# ("UNSUPPORTED: 1, 2, 3, ..., N") on every article with more than
# ~10 claims. The short form says the same thing once.

_VERIFY_SYSTEM = (
    "You are a fact-checking verifier. You will receive SOURCE DATA "
    "and a numbered list of CLAIMS. For each claim, decide whether "
    "it is GROUNDED or FABRICATED.\n"
    "\n"
    "GROUNDED means the claim is consistent with the source data. "
    "This includes:\n"
    "  - restating a figure, ticker, or name from the source "
    "(formatting differences are allowed: markdown asterisks, "
    "thousands separators, one extra decimal place)\n"
    "  - arithmetic on source figures (a spread, gap, difference, "
    "ratio, sum, average, or percentage-point change)\n"
    "  - interpreting a pattern using source figures\n"
    "  - stating what the numbers imply about positioning, risk, or "
    "regime, PROVIDED the sentence names the figures it draws on\n"
    "  - stating a falsification condition (what would prove a view "
    "wrong on a future reading)\n"
    "  - generic framing statements\n"
    "  - citations of headlines from headlines_md\n"
    "\n"
    "FABRICATED means the claim introduces a fact, figure, name, "
    "event, or causal mechanism that does not appear in the source "
    "and cannot be derived from it. Examples: an invented number, an "
    "invented ticker or company name, an invented event ('after the "
    "Fed cut rates'), or an asserted cause with no basis in the data "
    "('due to a contract win').\n"
    "\n"
    "OUTPUT FORMAT (strict):\n"
    "UNSUPPORTED: <comma-separated claim numbers, e.g. '2, 5'>\n"
    "If every claim is grounded, output exactly: UNSUPPORTED: NONE\n"
    "Do not include any other text."
)

_VERIFY_BATCH_SIZE = 5


def _parse_unsupported_nums(raw: str) -> list[int]:
    """Parse 'UNSUPPORTED: 2, 5' -> [2, 5]. Empty list for 'NONE'.
    Returns [1] as a fail-closed sentinel on unparseable input."""
    import re as _re
    if not raw:
        return [1]  # sentinel — one claim marked unsupported
    m = _re.search(r"UNSUPPORTED\s*:\s*(.+)", raw, _re.IGNORECASE | _re.DOTALL)
    if not m:
        return [1]
    payload = m.group(1).strip()
    if payload.upper().startswith("NONE"):
        return []
    nums: list[int] = []
    for tok in payload.replace(" ", "").split(","):
        if not tok:
            continue
        try:
            n = int(tok)
        except ValueError:
            import traceback as _TBI
            _TBI.print_exc()
            continue
        nums.append(n)
    return nums


    return [claims[i - 1] for i in bad_local if 1 <= i <= len(claims)]


# Module-level cache for the payload string across a batch run.
_batch_payload_cache: dict[str, str] = {}


# ─────────────────────────────────────────────────────────────────────
# Verify directive (S.8.47 — short form)
# ─────────────────────────────────────────────────────────────────────

_VERIFY_SYSTEM = (
    "You are a fact-checking verifier. You will receive SOURCE DATA "
    "and a numbered list of CLAIMS. For each claim, decide whether "
    "it is GROUNDED or FABRICATED.\n"
    "\n"
    "GROUNDED means the claim is consistent with the source data. "
    "This includes:\n"
    "  - restating a figure, ticker, or name from the source "
    "(formatting differences are allowed: markdown asterisks, "
    "thousands separators, one extra decimal place)\n"
    "  - arithmetic on source figures (a spread, gap, difference, "
    "ratio, sum, average, or percentage-point change)\n"
    "  - interpreting a pattern using source figures\n"
    "  - stating what the numbers imply about positioning, risk, or "
    "regime, PROVIDED the sentence names the figures it draws on\n"
    "  - stating a falsification condition (what would prove a view "
    "wrong on a future reading)\n"
    "  - generic framing statements\n"
    "  - citations of headlines from headlines_md\n"
    "\n"
    "FABRICATED means the claim introduces a fact, figure, name, "
    "event, or causal mechanism that does not appear in the source "
    "and cannot be derived from it. Examples: an invented number, an "
    "invented ticker or company name, an invented event ('after the "
    "Fed cut rates'), or an asserted cause with no basis in the data "
    "('due to a contract win').\n"
    "\n"
    "OUTPUT FORMAT (strict):\n"
    "UNSUPPORTED: <comma-separated claim numbers, e.g. '2, 5'>\n"
    "If every claim is grounded, output exactly: UNSUPPORTED: NONE\n"
    "Do not include any other text."
)

_VERIFY_BATCH_SIZE = 5


def _parse_unsupported_nums(raw: str) -> list[int] | None:
    """Parse 'UNSUPPORTED: 2, 5' -> [2, 5]. Empty list for 'NONE'.

    Returns None when the response is unparseable — meaning the
    verifier produced something other than a verdict (a paragraph,
    a reasoning trace, an empty string). The caller treats None as
    a batch failure, not as "no flags". This is the fail-closed
    change: earlier a paragraph-length response was silently
    accepted as "no unsupported claims", which is a false pass.
    """
    import re as _re
    if not raw or not raw.strip():
        return None
    m = _re.search(r"UNSUPPORTED\s*:\s*(.+)", raw, _re.IGNORECASE | _re.DOTALL)
    if not m:
        return None
    payload = m.group(1).strip()
    if payload.upper().startswith("NONE"):
        return []
    nums: list[int] = []
    for tok in payload.replace(" ", "").split(","):
        if not tok:
            continue
        try:
            n = int(tok)
        except ValueError:
            import traceback as _TBI
            _TBI.print_exc()
            # A non-integer token after UNSUPPORTED: is unparseable.
            return None
        nums.append(n)
    return nums


async def _verify_batch(
    session: AsyncSession,
    *,
    org_id: str,
    claims: list[str],
    payload_str: str,
) -> list[str]:
    """Verify a small batch (<=_VERIFY_BATCH_SIZE) of claims against
    the payload string. Returns the flagged claim strings."""
    from packages.ai.router import complete
    from packages.ai.prompts.loader import render_with_version

    claims_block = "\n".join(f"{i+1}. {c}" for i, c in enumerate(claims))

    user, _pv_verify = render_with_version(
        "trading.verify",
        payload_str=payload_str,
        claims_block=claims_block,
    )

    try:
        raw = await complete(
            role="verification",
            system=_VERIFY_SYSTEM,
            user=user,
            org_id=org_id,
            prompt_version_id=_pv_verify,
            purpose="trading.verify",
            temperature=0.0,
            max_tokens=3000,
            json_mode=False,
            use_cache=False,
            # S.8.49.13 — medium effort so the verifier reliably
            # applies its directive's permitted clauses
            # (falsification conditions, in-sentence figures).
            # At "low" the flag rate varied 0.08–0.50 across
            # identical articles.
            reasoning_effort="medium",
        )
    except Exception as exc:
        _TB.print_exc()
        log.warning("verify.batch_failed", batch_size=len(claims),
                    error=str(exc)[:200])
        # S.9.38 — rollback clears app.current_org_id because
        # SQLAlchemy resets the connection on return to the pool.
        # If we rollback, the next persist_post INSERT runs RLS
        # with an empty GUC and fails with "new row violates
        # row-level security policy". Do NOT rollback here.
        # The provider failure is a soft skip; the transaction
        # stays open and clean.
        # Provider failure is a SKIP, not a failure. Returning None
        # tells verify_post the batch could not be checked. The
        # article publishes with a __verify_skipped__ marker rather
        # than drafting on provider noise.
        return None

    bad_local = _parse_unsupported_nums(raw or "")
    if bad_local is None:
        # S.8.90 — _parse_unsupported_nums returns None when the
        # model answered with a non-numeric token (e.g. a truncation
        # that returned "UNSUPPORTED: NONE" as prose). Iterating
        # None raised "'NoneType' object is not iterable" and
        # aborted the entire generate. Fail closed instead: mark
        # every claim in the batch unsupported.
        log.warning("verify.batch_unparseable", batch_size=len(claims),
                    raw=(raw or "")[:200])
        return list(claims)
    return [claims[i - 1] for i in bad_local if 1 <= i <= len(claims)]


async def verify_post(
    session: AsyncSession,
    *,
    org_id: str,
    post: TradingPost,
    source_payload: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Ask the reasoning LLM which claims in the post are NOT grounded.

    S.8.47 — claims are verified in batches of _VERIFY_BATCH_SIZE. A
    single call with 20+ claims overwhelms a small verify model: it
    returns a generic 'everything is unsupported' verdict without
    reading each claim. Batching makes each call small enough that
    the model reads each claim.

    Returns (all_supported, unsupported_claims).
    """
    import asyncio as _asyncio

    claims = _split_claims(post.body)
    if not claims:
        return True, []

    payload_str = json.dumps(source_payload, default=str)
    if len(payload_str) > 12_000:
        payload_str = payload_str[:12_000] + "… (truncated)"

    batches = [
        claims[i:i + _VERIFY_BATCH_SIZE]
        for i in range(0, len(claims), _VERIFY_BATCH_SIZE)
    ]

    # S.8.49.8 — run batches sequentially. asyncio.gather fired
    # every verify call at once, which multiplied DB connections
    # (each batch opens its own) and LLM calls (which trigger the
    # provider 429 storm). Sequential is slower per article but
    # keeps the pool and the rate limit healthy.
    results = []
    skipped = 0
    for batch in batches:
        r = await _verify_batch(
            session,
            org_id=org_id,
            claims=batch,
            payload_str=payload_str,
        )
        if r is None:
            skipped += 1
            continue
        results.append(r)

    unsupported = [c for r in results for c in (r or [])]
    if skipped:
        log.warning("verify.batches_skipped", count=skipped,
                    batches=len(batches))
        # Record the coverage gap. It does not count against the
        # threshold — it is a "could not check", not a flag.
        unsupported.append(
            f"__verify_skipped__: {skipped} of {len(batches)} batches "
            "could not be checked (provider failure)"
        )
    if unsupported:
        log.info("verify.unsupported_found", count=len(unsupported),
                 batches=len(batches), claims=len(claims),
                 skipped=skipped)
    else:
        log.info("verify.all_supported", claims=len(claims),
                 batches=len(batches))

    # Count only real flags against the threshold. The
    # __verify_skipped__ marker is informational.
    _real_flags = [
        f for f in unsupported
        if not str(f).startswith("__verify_skipped__")
    ]
    _MAX_UNSUPPORTED = 3
    return (len(_real_flags) <= _MAX_UNSUPPORTED), unsupported


async def call_llm_json(
    session: AsyncSession,
    *,
    org_id: str,
    system: str,
    user: str,
    prompt_name: str,
    target_words: int = 400,
    use_cache: bool = True,
    skip_standard: bool = False,
    temperature: float = 0.0,
) -> TradingPost:
    """Call the reasoning LLM and parse {title, body} from the response.

    Uses packages.ai.router.complete with role='reasoning'.
    """
    from packages.ai.router import complete

    if skip_standard:
        # market_analysis and any future self-contained subblock:
        # the register + template carry everything. No STANDARD, no
        # OUTPUT FORMAT wrapper. The template declares its own
        # TITLE/BODY markers and its own rules.
        full_system = system
    else:
        full_system = (
            f"{STANDARD}\n\n"
            f"--- SUBBLOCK FORMAT ---\n\n"
            f"{system}\n\n"
            f"OUTPUT FORMAT (strict):\n"
            f"TITLE: <one line — the post title>\n"
            f"BODY:\n"
            f"<markdown body — tables, headings, short paragraphs>\n"
            f"\n"
            f"RULES:\n"
            f"- Start with the literal line 'TITLE: ' (nothing before it).\n"
            f"- Then a line with exactly 'BODY:' (nothing else on the line).\n"
            f"- The body is continuous prose. Short paragraphs. Do NOT use "
            f"markdown headings, tables, or bulleted lists unless the SUBBLOCK "
            f"FORMAT above explicitly requires one.\n"
            f"- Target length: {target_words} words "
            f"(acceptable range {_MIN_WORDS}-{_MAX_WORDS} words).\n"
            f"- Use ONLY numbers and facts present in the input data.\n"
            f"- Do not invent tickers, prices, percentages, or dates.\n"
            f"- If the data looks degraded or empty, say so plainly rather than fabricating."
        )
    from packages.ai.prompts.loader import render_with_version

    full_user, _pv_gen = render_with_version(
        "trading.subblock_generate",
        user=user,
        target_words=str(target_words),
    )

    log.info("subblock.llm_call_start", prompt=prompt_name, words=target_words)

    # DEBUG: dump the exact prompt sent to the model. Gated on
    # DEBUG_MA_PROMPT=1 in the environment. Used for diagnosing
    # refusals ("I'm sorry, but I can't complete this request").
    import os as _os_dbg
    if _os_dbg.environ.get("DEBUG_MA_PROMPT"):
        try:
            from pathlib import Path as _P_dbg
            _P_dbg("/tmp/ma_system.txt").write_text(full_system)
            _P_dbg("/tmp/ma_user.txt").write_text(full_user)
            log.info("subblock.prompt_dumped",
                     system_chars=len(full_system),
                     user_chars=len(full_user))
        except Exception as _exc_dbg:
            log.warning("subblock.prompt_dump_failed",
                        error=str(_exc_dbg)[:200])

    # Text protocol — avoids Groq's JSON-mode token cap that truncated
    # long responses and failed with json_validate_failed.
    # Retry the LLM call on transient network errors (DNS blips,
    # brief outages). Non-retryable errors (4xx from the provider)
    # propagate immediately.
    import asyncio as _asyncio

    _last_exc: Exception | None = None
    text: str | None = None
    for _attempt in range(3):
        try:
            text = await complete(
                role="reasoning",
                system=full_system,
                user=full_user,
                org_id=org_id,
                prompt_version_id=_pv_gen,
                purpose=prompt_name,
                temperature=temperature,
                max_tokens=min(_MAX_TOKENS_LONG, max(int(target_words * 1.4) + 600, 2000)),
                json_mode=False,
                use_cache=use_cache,
                # S.9.43 — generation uses low reasoning effort.
                # The Groq provider's own comment (groq_provider.py
                # S.8.44) documents that gpt-oss models at 'medium'
                # consume most of the max_tokens budget on hidden
                # reasoning before writing, leaving too little for
                # the visible article. 'low' preserves the reserve
                # for the article. Verification keeps 'medium'.
                reasoning_effort="low",
            )
            _last_exc = None
            break
        except Exception as _exc:
            import traceback as _TBI
            _TBI.print_exc()
            _last_exc = _exc
            _msg = str(_exc).lower()
            # Retry only on network-level failures. A 400/401/413
            # is not going to succeed on retry.
            _retryable = (
                "name resolution" in _msg
                or "connection failed" in _msg
                or "temporary failure" in _msg
                or "timeout" in _msg
            )
            if not _retryable or _attempt == 2:
                raise
            log.warning(
                "llm.retry",
                attempt=_attempt + 1,
                error=str(_exc)[:120],
            )
            await _asyncio.sleep(2 ** _attempt)

    if _last_exc is not None:
        raise _last_exc
    text = (text or "").strip()

    try:
        title, body = _parse_title_body(text)
    except Exception as exc:
        import traceback as _TBI
        _TBI.print_exc()
        log.warning("subblock.llm_parse_failed", error=str(exc)[:200], raw=text[:400])
        # S.6.4.6_strip — the parser bailed, so 'text' is the raw
        # model output including 'TITLE:' / 'BODY:' lines. Strip
        # them before persisting.
        _clean = _strip_label_prefix(text or "(empty response)")
        return TradingPost(
            title=_strip_label_prefix(prompt_name.replace(".", " ").title()),
            body=_clean,
            word_count=len(_clean.split()),
        )

    if not body:
        log.warning("subblock.llm_empty_body", raw=text[:200])
        body = text or "(empty response)"

    # S.6.4.6_strip — belt-and-braces: even if _parse_title_body
    # handed back a prefix, strip it here before constructing.
    return TradingPost(
        title=_strip_label_prefix((title or "Untitled")[:500]),
        body=_strip_label_prefix(body),
        word_count=len(_strip_label_prefix(body).split()),
    )


# ─────────────────────────────────────────────────────────────────────
# Persist
# ─────────────────────────────────────────────────────────────────────

async def persist_post(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
    post: TradingPost,
    source_payload: dict[str, Any],
    source_urls: list[str] | None = None,
    symbols: list[str] | None = None,
    template_id: str | None = None,
) -> str:
    # S.9.39 — the LLM path runs sync DB writes (ai_calls),
    # acquires advisory locks (pick_register), and handles
    # provider failures. Any of these can leave the async
    # transaction in an aborted state. Reset it and re-set the
    # RLS context BEFORE the first SQL of the persist. The
    # rollback clears app.current_org_id; set_current_org
    # puts it back. Without the re-set, the INSERT fails RLS
    # with "new row violates row-level security policy".
    try:
        await session.rollback()
    except Exception:
        pass
    from packages.db.tenant import set_current_org as _set_org
    await _set_org(session, org_id)

    """Insert a trading_posts row. Returns the new post id."""
    # S.8.19 — delivery check. Enforces the register's rules on the
    # body before persist: no meta-commentary, no summary close, no
    # leaked prompt fragments, no tables where the register forbids
    # them, first-person caps, etc. Violations become a draft with
    # __delivery__ flags, mirroring how verify_post fails.
    register = source_payload.get("register") if isinstance(source_payload, dict) else None
    if register:
        try:
            from ..registers import delivery_check, DeliveryCheckError
            delivery_check(post.body, register, template_id=template_id)
        except DeliveryCheckError as exc:
            import traceback as _TBI
            _TBI.print_exc()
            log.warning(
                "delivery_check.failed",
                register=register,
                violations=len(exc.violations),
                preview=str(exc.violations)[:200],
            )
            post.verification_ok = False
            existing = list(getattr(post, "unsupported_claims", []) or [])
            post.unsupported_claims = existing + [
                f"__delivery__: {v}" for v in exc.violations
            ]
        except Exception as exc:
            import traceback as _TBI
            _TBI.print_exc()
            # A crashed check is NOT a pass. The article could not
            # be verified, so it cannot be published. Flag it and
            # let the reviewer see what happened.
            log.error(
                "delivery_check.crash",
                register=register,
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
            post.verification_ok = False
            existing = list(getattr(post, "unsupported_claims", []) or [])
            post.unsupported_claims = existing + [
                f"__delivery__: checker crashed — "
                f"{type(exc).__name__}: {str(exc)[:100]}"
            ]

    # S.6.4.5 / S.8.27.3 — never publish a post with no content,
    # but do NOT crash the whole run over one. A 2-word post from a
    # truncated fallback model is garbage, not an error worth
    # raising on. Persist it as a draft with a __too_short__ flag;
    # the reviewer discards it from the drafts queue.
    _word_count = getattr(post, "word_count", 0)
    if _word_count < 50:
        log.warning(
            "persist_post.too_short_persisting_draft",
            words=_word_count,
            title=getattr(post, "title", "?")[:60],
        )
        post.verification_ok = False
        _existing = list(getattr(post, "unsupported_claims", []) or [])
        post.unsupported_claims = _existing + [
            f"__too_short__: {_word_count} words"
        ]
    # S.8.49.5 — D-1: view registers tolerate ≤ _VIEW_FLAG_THRESHOLD
    # verify-only flags. Hard failures still draft.
    _flags = list(getattr(post, "unsupported_claims", []) or [])
    _hard_prefixes = ("__length__", "__delivery__", "__too_short__", "__validator__", "__validator_error__", "__numerical__")
    _hard_fail = any(str(f).startswith(_hard_prefixes) for f in _flags)
    _verify_only = [
        f for f in _flags
        if not str(f).startswith(_hard_prefixes)
    ]
    _is_view = bool(
        register
        and (
            register.endswith("-view")
            or register in _VIEW_REGISTERS
        )
    )
    _verify_ok = getattr(post, "verification_ok", True)
    # S.8.49.7 — scale tolerance to article length. A fixed
    # threshold under-publishes long articles and over-publishes
    # short ones. Compute the claim count and use the rate; fall
    # back to the fixed threshold if the count is unavailable.
    _claim_count = 0
    try:
        _claim_count = len(_split_claims(post.body))
    except Exception:
        import traceback as _TBI
        _TBI.print_exc()
        pass
    if _claim_count > 0:
        _allow_verify = _is_view and (
            len(_verify_only) / _claim_count <= _VIEW_FLAG_RATE
        )
    else:
        _allow_verify = _is_view and len(_verify_only) <= _VIEW_FLAG_THRESHOLD
    # S.8.49.6 — verify short-circuits the length check. When
    # D-1 tolerance would publish, re-check length here so a
    # short article that failed verify first does not publish
    # below its register floor.
    if _allow_verify and not _hard_fail:
        _tw = getattr(post, "target_words", None)
        if _tw:
            _floor = _floor_for(register)
            _ceil = _MAX_WORDS
            if post.word_count < _floor or post.word_count > _ceil:
                _hard_fail = True
                _allow_verify = False
                post.unsupported_claims = list(post.unsupported_claims) + [
                    f"__length__: {post.word_count} words "
                    f"(floor {_floor}, target {_tw}, ceiling {_ceil})"
                ]
    if _hard_fail or (not _verify_ok and not _allow_verify):
        status = "draft"
    else:
        status = "published"
    # Compute published_at in Python to avoid bind-parameter type ambiguity
    from datetime import UTC, datetime
    published_at = datetime.now(UTC) if status == "published" else None

    # For drafts, attach the verification failure reason so the UI
    # can show a human why the post was flagged.
    if status == "draft":
        source_payload = dict(source_payload)
        source_payload["_rejection"] = {
            "unsupported_claims": list(getattr(post, "unsupported_claims", []) or []),
            "regenerated": bool(getattr(post, "regenerated", False)),
            "flagged_at": published_at or datetime.now(UTC).isoformat(),
        }
    # S.8.49.5 — a published view-register article with accepted
    # verify flags carries those flags as review notes, so the
    # reviewer sees which sentences to look at.
    elif _is_view and _verify_only:
        source_payload = dict(source_payload)
        source_payload["_review_notes"] = {
            "verify_flags": _verify_only,
            "accepted_under": "view-register tolerance",
            "threshold": _VIEW_FLAG_THRESHOLD,
        }

    # S.7.8b-4 — carry editorial metadata into source_payload. form,
    # tone, and voice travel on the post object so every subblock
    # gets them recorded without the caller remembering to add them
    # to their own source_payload dict.
    _editorial = {
        k: v
        for k, v in {
            "form": getattr(post, "form", None),
            "tone": getattr(post, "tone", None),
            "voice": getattr(post, "voice", None),
        }.items()
        if v is not None
    }
    if _editorial:
        source_payload = dict(source_payload)
        source_payload.update(_editorial)

    # S.7.5.2 — dedup. If an identical title+body already exists for
    # this org + subblock within the last 24 hours, refuse to write a
    # second row. Catches both shapes observed in production:
    #   - double-click on "Generate now" (seconds apart)
    #   - hourly re-run on unchanged market data (minutes apart)
    # S.8.31 — skip the dedup check for __too_short__ articles. An
    # empty (or near-empty) article collides trivially with the
    # previous empty article: same "Untitled" title, same empty body.
    # The dedup guard exists to prevent double-posting the same
    # *article*; an empty article is a failure artifact, not a post.
    # If an empty article already exists for this subblock in the
    # last hour, return its id instead of raising.
    _is_too_short = any(
        str(c).startswith("__too_short__")
        for c in (getattr(post, "unsupported_claims", []) or [])
    )
    if _is_too_short:
        recent_empty = (await session.execute(sql_text("""
            SELECT id::text FROM trading_posts
            WHERE org_id = :org_id
              AND subblock_id = :subblock_id
              AND word_count < 50
              AND created_at > now() - interval '1 hour'
            LIMIT 1
        """), {
            "org_id": org_id,
            "subblock_id": subblock["id"],
        })).first()
        if recent_empty:
            log.info(
                "persist_post.too_short_duplicate_skipped",
                existing=recent_empty[0][:8],
            )
            return recent_empty[0]

    dup = (await session.execute(sql_text("""
        SELECT id::text FROM trading_posts
        WHERE org_id = :org_id
          AND subblock_id = :subblock_id
          AND title = :title
          AND body = :body
          AND deleted_at IS NULL
          AND created_at > now() - interval '24 hours'
        LIMIT 1
    """), {
        "org_id": org_id,
        "subblock_id": subblock["id"],
        "title": post.title,
        "body": post.body,
    })).first()
    if dup:
        raise DuplicatePostError(
            f"identical post already exists for this subblock within 24h: {dup[0]}"
        )

    row = (await session.execute(sql_text("""
        INSERT INTO trading_posts (
            org_id, subblock_id, title, body, format, source_kind,
            source_payload, source_urls, symbols, status, word_count,
            published_at, unsupported_claims, subject, coordinate
        ) VALUES (
            :org_id, :subblock_id, :title, :body, 'long_form', :source_kind,
            CAST(:source_payload AS jsonb), :source_urls, :symbols,
            :status, :word_count, :published_at,
            CAST(:unsupported_claims AS jsonb), :subject, :coordinate
        )
        RETURNING id::text
    """), {
        "org_id": org_id,
        "subblock_id": subblock["id"],
        "title": post.title,
        "body": post.body,
        "source_kind": subblock["source_kind"],
        "source_payload": json.dumps(source_payload, default=str),
        "source_urls": source_urls or [],
        "symbols": symbols or [],
        "word_count": post.word_count,
        "status": status,
        "published_at": published_at,
        "unsupported_claims": json.dumps(
            getattr(post, "unsupported_claims", []) or [],
            default=str,
        ),
        "subject": getattr(post, "subject", None),
        "coordinate": getattr(post, "coordinate", None),
    })).first()

    if not row:
        raise RuntimeError("persist_post: no row returned")
    return row[0]


# ─────────────────────────────────────────────────────────────────────
# The generator interface
# ─────────────────────────────────────────────────────────────────────

async def generate_subblock(
    session: AsyncSession,
    *,
    org_id: str,
    slug: str,
) -> dict[str, Any]:
    """Load subblock, dispatch to its generator module, persist, return summary.

    Generator modules are imported lazily so a missing one doesn't break imports.
    """
    subblock = await load_subblock(session, slug=slug, org_id=org_id)
    if not subblock:
        raise ValueError(f"subblock not found: {slug}")
    # S.9.32 — the id is a uuid FK on trading_posts. If load_subblock
    # ever returns an empty string (bad row, RLS shadow, org-scoped
    # override with a NULL id), every downstream bind to subblock_id
    # fails with 'invalid input syntax for type uuid: ""'. Fail loudly
    # here, at the source.
    if not subblock.get("id"):
        raise ValueError(
            f"subblock {slug} has empty id — trading_subblocks row "
            f"is malformed or hidden by RLS"
        )
    if not subblock["enabled"]:
        return {"status": "disabled", "slug": slug}

    try:
        mod = __import__(
            f"packages.trading.subblocks.{slug}",
            fromlist=["generate"],
        )
    except ImportError as exc:
        import traceback as _TBI
        _TBI.print_exc()
        raise ValueError(f"no generator for subblock: {slug} ({exc})") from exc

    result = await mod.generate(session, org_id=org_id, subblock=subblock)
    if result is None:
        return {"status": "skipped", "slug": slug}
    return result

async def call_llm_json_verified(
    session: AsyncSession,
    *,
    org_id: str,
    system: str,
    user: str,
    prompt_name: str,
    source_payload: dict[str, Any],
    target_words: int = 400,
    use_cache: bool = True,
    form: str | None = None,
    tone: str | None = None,
    voice: str | None = None,
    skip_verify: bool = False,
    skip_standard: bool = False,
    temperature: float = 0.0,
) -> TradingPost:
    """Public entry point. Calls the impl, then stamps the returned
    post with editorial metadata (form, tone, voice). The metadata
    travels with the object into persist_post, which merges it into
    source_payload. This avoids every subblock having to remember
    to add form/tone/voice to its own source_payload dict.
    """
    post = await _call_llm_json_verified_impl(
        session,
        org_id=org_id,
        system=system,
        user=user,
        prompt_name=prompt_name,
        source_payload=source_payload,
        target_words=target_words,
        use_cache=use_cache,
        skip_verify=skip_verify,
        skip_standard=skip_standard,
        temperature=temperature,
    )
    post.form = form
    post.tone = tone
    post.voice = voice
    post.target_words = target_words
    return post


async def _call_llm_json_verified_impl(
    session: AsyncSession,
    *,
    org_id: str,
    system: str,
    user: str,
    prompt_name: str,
    source_payload: dict[str, Any],
    target_words: int = 400,
    use_cache: bool = True,
    skip_verify: bool = False,
    skip_standard: bool = False,
    temperature: float = 0.0,
) -> TradingPost:
    """Single-pass generation. Replaces the multi-retry impl (S.8.20).

    Three stages, no retries:

      1. One generation call.
      2. One verify call (accuracy is primary; runs first).
      3. One length check (after verify).

    A failure at any stage returns the post with
    verification_ok=False and a flag. persist_post writes it as a
    draft. The reviewer approves or discards in the existing
    review flow.

    This replaces an implementation that ran up to five LLM calls
    per article (generate -> undersized retry -> verify ->
    regeneration -> re-verify). That version was the source of
    every hard failure in this pipeline: ValueError on retry
    failure, empty-body from fallback-model truncation,
    cascading rate-limit exhaustion. It also inflated the daily
    token budget; verify alone was 78% of the spend, and the
    regen+re-verify path doubled each verify.

    Removing the retries does not remove the ability to save a
    failing article — it removes the attempt. Articles that
    fail verify or length land as drafts with a flag. The
    retries were masking the failure rate, not fixing it.
    """
    effective_prompt = prompt_name if use_cache else f"{prompt_name}.nocache"

    # ── 1. One generation call. No retries.
    post = await call_llm_json(
        session,
        org_id=org_id,
        system=system,
        user=user,
        prompt_name=effective_prompt,
        target_words=target_words,
        use_cache=use_cache,
        skip_standard=skip_standard,
        temperature=temperature,
    )

    # ── 2. Verify FIRST, before any length check. Accuracy is
    # primary; a short accurate article is a draft, a long
    # inaccurate one is not published.
    #
    # S.8.42 — education subblocks have no source data to verify
    # against (their input is a topic name, not a data payload).
    # Forcing verify on them flags every declarative sentence and
    # drafts the article permanently. skip_verify=True bypasses
    # the verify call for those subblocks; the delivery check and
    # length check still apply.
    if skip_verify:
        ok, unsupported = True, []
    else:
        ok, unsupported = await verify_post(
            session, org_id=org_id, post=post, source_payload=source_payload,
        )
    if not ok:
        log.info(
            "verify.rejected",
            prompt=prompt_name,
            words=post.word_count,
            unsupported=len(unsupported),
        )
        post.verification_ok = False
        post.unsupported_claims = list(unsupported)
        return post

    # ── 3. Length check AFTER verify.
    ceiling = _MAX_WORDS
    # S.8.94 — the register name lives in source_payload, not in
    # this function's scope. Read it here so _floor_for can pick
    # the right floor.
    _register = (
        source_payload.get("register")
        if isinstance(source_payload, dict) else None
    )
    floor = _floor_for(_register)
    if post.word_count > ceiling or post.word_count < floor:
        log.info(
            "llm.length_rejected",
            prompt=prompt_name,
            words=post.word_count,
            floor=floor,
            target=target_words,
            ceiling=ceiling,
        )
        post.verification_ok = False
        post.unsupported_claims = [
            f"__length__: {post.word_count} words "
            f"(floor {floor}, target {target_words}, ceiling {ceiling})"
        ]
        return post

    # ── All checks passed. The delivery check runs in persist_post
    #    because it needs source_payload["register"], which the
    #    subblock adds to source_payload before calling persist.
    post.verification_ok = True
    return post
