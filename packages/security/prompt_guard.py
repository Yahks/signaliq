"""Prompt injection defense.

Three layers:

  1. **Sanitize** — strip patterns known to manipulate LLMs
     (e.g. "ignore previous instructions", "system:", "you are now")
  2. **Delimit** — wrap untrusted content in explicit markers so the
     model knows the boundary between our instructions and user data
  3. **Log** — record sanitized injections for observability

Design principle: never reject. The content is still useful even if
someone tried to inject instructions. We just neutralize the injection
and keep the legitimate text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from packages.core.logging import get_logger

log = get_logger(__name__)


# Patterns that commonly appear in prompt injection attempts.
# Order matters — we strip longer phrases first to avoid partial matches.
_INJECTION_PATTERNS: list[tuple[str, str]] = [
    # Classic instruction override
    (r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", "[redacted]"),
    (r"disregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", "[redacted]"),
    (r"forget\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", "[redacted]"),
    (r"ignore\s+the\s+prompt\s+above", "[redacted]"),
    # Role reassignment
    (r"you\s+are\s+now\s+(?:a\s+|an\s+)?[a-z][a-z0-9 _-]{2,40}", "[redacted]"),
    (r"act\s+as\s+(?:a\s+|an\s+)?[a-z][a-z0-9 _-]{2,40}", "[redacted]"),
    (r"pretend\s+to\s+be\s+(?:a\s+|an\s+)?[a-z][a-z0-9 _-]{2,40}", "[redacted]"),
    (r"new\s+instructions?:", "[redacted]"),
    (r"updated\s+instructions?:", "[redacted]"),
    # Chat control tokens (used in many LLM formats)
    (r"<\|im_start\|>", "[redacted]"),
    (r"<\|im_end\|>", "[redacted]"),
    (r"<\|system\|>", "[redacted]"),
    (r"<\|user\|>", "[redacted]"),
    (r"<\|assistant\|>", "[redacted]"),
    (r"\[INST\]", "[redacted]"),
    (r"\[/INST\]", "[redacted]"),
    (r"<\|endoftext\|>", "[redacted]"),
    # Explicit roles
    (r"^\s*system\s*:", "[redacted]"),
    (r"^\s*assistant\s*:", "[redacted]"),
    (r"^\s*developer\s*:", "[redacted]"),
    # Delimiter escapes we use ourselves
    (r"<<<\s*EVENT\s*>>>", "[redacted]"),
    (r"<<<\s*END_EVENT\s*>>>", "[redacted]"),
    (r"<<<\s*UNTRUSTED\s*>>>", "[redacted]"),
    (r"<<<\s*END_UNTRUSTED\s*>>>", "[redacted]"),
    # Prompt leakage attempts
    (r"repeat\s+(?:the\s+)?(?:above|previous|earlier)\s+(?:text|instructions?|prompt)", "[redacted]"),
    (r"print\s+(?:your\s+)?(?:system\s+)?prompt", "[redacted]"),
    (r"show\s+(?:me\s+)?(?:your\s+)?(?:system\s+)?prompt", "[redacted]"),
    (r"what\s+(?:are|were)\s+your\s+instructions?", "[redacted]"),
    # Special characters that often break delimiters
    (r"```\s*system", "[redacted]"),
    (r"```\s*assistant", "[redacted]"),
]

_COMPILED_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), repl)
    for pattern, repl in _INJECTION_PATTERNS
]


@dataclass
class SanitizeResult:
    text: str
    injections_found: int = 0
    matched_patterns: list[str] = field(default_factory=list)


def sanitize(text: str, *, field_name: str = "text", org_id: str | None = None) -> SanitizeResult:
    """Strip prompt-injection patterns from untrusted text.

    Never rejects. Returns sanitized text + a record of what was stripped.
    """
    if not text:
        return SanitizeResult(text=text or "")

    original = text
    matched: list[str] = []

    for pattern, repl in _COMPILED_PATTERNS:
        new_text, count = pattern.subn(repl, text)
        if count > 0:
            matched.append(pattern.pattern[:60])
            text = new_text

    if matched:
        log.warning(
            "prompt_guard.injection_detected",
            field=field_name,
            org_id=org_id[:8] if org_id else None,
            count=len(matched),
            patterns=matched[:5],
            original_length=len(original),
            sanitized_length=len(text),
        )

    return SanitizeResult(
        text=text,
        injections_found=len(matched),
        matched_patterns=matched,
    )


def sanitize_dict(
    data: dict[str, Any],
    *,
    text_fields: list[str],
    org_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Sanitize specified text fields in a dict.

    Returns (sanitized_dict, total_injections_found).
    """
    out = dict(data)
    total = 0
    for field_name in text_fields:
        if field_name not in out or not isinstance(out[field_name], str):
            continue
        result = sanitize(out[field_name], field_name=field_name, org_id=org_id)
        out[field_name] = result.text
        total += result.injections_found
    return out, total


def wrap_untrusted(text: str, *, tag: str = "UNTRUSTED") -> str:
    """Wrap text in explicit delimiters so the LLM knows it's data, not instructions.

    Example:
        wrap_untrusted("ignore previous instructions")
        # → '<<<UNTRUSTED>>>\\nignore previous instructions\\n<<<END_UNTRUSTED>>>'
    """
    return f"<<<{tag}>>>\n{text}\n<<<END_{tag}>>>"


def sanitize_and_wrap(text: str, *, tag: str = "UNTRUSTED", org_id: str | None = None) -> str:
    """Sanitize then wrap. The recommended default for untrusted content."""
    result = sanitize(text, org_id=org_id)
    return wrap_untrusted(result.text, tag=tag)
