"""Turn provider error text into user-facing messages.

Provider errors arrive as JSON blobs:

  "Groq HTTP 429: {\"error\":{\"message\":\"Rate limit reached for model
   `openai/gpt-oss-120b` in organization `org_01m2fqs2...` service tier
   `on_demand` on tokens per minute (TPM): Limit 8000, Used 6354,
   Requested 1910. Please try again in 1.98s...\",\"type\":\"...\",
   \"code\":\"rate_limit_exceeded\"}}"

This module recognises the common shapes and returns a structured form
that the frontend can render directly. Falls back to the raw text when
nothing matches.
"""
from __future__ import annotations

import re
from typing import TypedDict


class ClassifiedError(TypedDict, total=False):
    kind: str              # rate_limit | invalid_key | json_validate |
                           # quota | timeout | unreachable | unknown
    message: str           # user-facing one-liner
    action: str            # what the user should do
    provider: str | None
    retry_after_seconds: int | None


_PROVIDERS = ("groq", "gemini", "openrouter", "ollama")


def _provider_from_text(text: str) -> str | None:
    low = text.lower()
    for p in _PROVIDERS:
        if p in low:
            return p
    return None


def _parse_retry_after(text: str) -> int | None:
    """Extract 'try again in X.XXs' or 'retry after X'."""
    m = re.search(r"try again in ([\d.]+)\s*s", text, re.IGNORECASE)
    if not m:
        m = re.search(r"retry.{0,5}after[^\d]*([\d.]+)", text, re.IGNORECASE)
    if not m:
        return None
    try:
        return max(1, int(float(m.group(1)) + 1))
    except ValueError:
        return None


def classify(raw: str | None) -> ClassifiedError:
    """Turn a raw error string into a user-facing structured error."""
    if not raw:
        return {
            "kind": "unknown",
            "message": "The chain build failed.",
            "action": "Try again. If it keeps failing, contact support.",
        }

    text = str(raw)
    lower = text.lower()
    provider = _provider_from_text(text)
    provider_label = provider.title() if provider else "Your provider"

    # Quota / credits exhausted (distinct from a rate limit)
    if ("quota" in lower
            or "insufficient_quota" in lower
            or "exceeded your current quota" in lower
            or "billing" in lower):
        return {
            "kind": "quota",
            "message": f"{provider_label} reports your account is out of quota or credits.",
            "action": (
                "Check your billing with the provider, or add a different "
                "provider's key at /settings/credentials."
            ),
            "provider": provider,
        }

    # Rate limit
    if ("429" in text
            or "rate limit" in lower
            or "rate_limit_exceeded" in lower):
        secs = _parse_retry_after(text)
        if secs:
            retry_msg = f"Your {provider_label} rate limit is reached. Try again in about {secs} seconds."
        else:
            retry_msg = f"Your {provider_label} rate limit is reached."
        return {
            "kind": "rate_limit",
            "message": retry_msg,
            "action": (
                "You can wait for the limit to reset, or add another key at "
                "/settings/credentials to spread the load across providers."
            ),
            "provider": provider,
            "retry_after_seconds": secs,
        }

    # Invalid key
    if ("401" in text
            or "invalid api key" in lower
            or "invalid_api_key" in lower
            or "unauthorized" in lower):
        return {
            "kind": "invalid_key",
            "message": f"{provider_label} rejected your API key.",
            "action": (
                "Check the key at /settings/credentials. If it looks right, "
                "the key may have been rotated or revoked by the provider."
            ),
            "provider": provider,
        }

    # JSON validation failure (LLM output didn't parse)
    if "json_validate_failed" in lower or "failed to validate json" in lower:
        return {
            "kind": "json_validate",
            "message": (
                "The AI produced a response we couldn't parse. "
                "This is usually a transient model issue."
            ),
            "action": "Try again. If it keeps failing, the prompt may need tuning.",
            "provider": provider,
        }

    # Timeout / unreachable
    if ("timeout" in lower
            or "timed out" in lower
            or "connection" in lower
            or "unreachable" in lower):
        return {
            "kind": "unreachable",
            "message": f"Couldn't reach {provider_label}.",
            "action": "Check your internet connection and try again.",
            "provider": provider,
        }

    # All credentials exhausted (from our own router)
    if "all 1 credentials rate-limited" in lower or "all credentials rate-limited" in lower:
        secs = _parse_retry_after(text)
        return {
            "kind": "rate_limit",
            "message": (
                f"Your {provider_label} rate limit is reached."
                + (f" Try again in about {secs} seconds." if secs else "")
            ),
            "action": (
                "Add another key at /settings/credentials so future builds "
                "can use a second provider when this one is busy."
            ),
            "provider": provider,
            "retry_after_seconds": secs,
        }

    # No credentials configured
    if "no api credentials" in lower or "no credentials configured" in lower:
        return {
            "kind": "no_credentials",
            "message": "You haven't added an API key yet.",
            "action": "Add one at /settings/credentials to enable generation.",
            "provider": None,
        }

    # ── Content-pipeline outcomes (not provider errors) ──────────────
    # These are the failures users hit most often in normal use: the
    # pipeline ran cleanly but the input didn't fit the shape it needs.
    # They deserve a specific message and a specific action.

    if "timeline_not_chain" in lower:
        return {
            "kind": "timeline_not_chain",
            "message": (
                "The events in that text happen in sequence but don't "
                "causally connect. A chain needs events that cause, "
                "escalate, or contradict each other — not just things "
                "that happened one after another."
            ),
            "action": (
                "Try an article where one event leads to another — "
                "a policy decision and its consequences, a conflict and "
                "its escalation, or a claim and its rebuttal."
            ),
            "provider": None,
        }

    # S.6.4.1e — credentials-specific reasons. These come from
    # add_provider_key via CredentialVerificationError, and the detail
    # is already a user-facing message ("This key is already registered
    # as X"). Just pass it through.
    # S.6.4.1e.1 — match the reason code OR the message text. The
    # frontend often has only the message (error_message), not the reason.
    if ("duplicate_key" in lower
            or "already registered as" in lower):
        return {
            "kind": "duplicate_key",
            "message": (
                "This key is already registered for your organisation. "
                "Check your existing keys below."
            ),
            "action": (
                "Delete the existing entry first if you want to replace it, "
                "or use a different key."
            ),
            "provider": provider,
        }

    if "no_coherent_chain" in lower:
        return {
            "kind": "no_chain",
            "message": (
                "The AI couldn't find a coherent story in that text. "
                "It looks like the content describes a single event or "
                "observation, but a chain needs a sequence of related "
                "events to build from."
            ),
            "action": (
                "Try pasting a longer article or a news story with multiple "
                "events. Chains work best with 4-8 distinct things that "
                "happened over time."
            ),
            "provider": None,
        }

    if "too_few_events" in lower or "too few" in lower:
        return {
            "kind": "too_few_events",
            "message": "The AI found fewer than 2 distinct events in your text.",
            "action": (
                "Try pasting a longer article, or one that covers several "
                "related happenings."
            ),
            "provider": None,
        }

    if "verify_failed" in lower or "too_many_unsupported" in lower:
        return {
            "kind": "verify_failed",
            "message": (
                "The article was written, but the fact-checker couldn't "
                "confirm enough of its claims."
            ),
            "action": (
                "This is usually temporary — try again. If it persists, "
                "contact your admin."
            ),
            "provider": None,
        }

    # ── Default — unknown but not raw ────────────────────────────────
    return {
        "kind": "unknown",
        "message": "The chain build failed.",
        "action": (
            "Try again. If it keeps failing, the details below may help "
            "your admin diagnose it."
        ),
        "provider": provider,
    }
