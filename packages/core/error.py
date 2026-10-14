"""
Domain exception hierarchy.

Routers catch these and translate to HTTP responses.
Services raise these — never raise HTTPException outside the API layer.
"""


class SignalIQError(Exception):
    """Base class for all SignalIQ domain errors."""

    code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str = "An internal error occurred", **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "context": self.context,
            }
        }


class ValidationError(SignalIQError):
    code = "validation_error"
    http_status = 422


class NotFoundError(SignalIQError):
    code = "not_found"
    http_status = 404


class ConflictError(SignalIQError):
    code = "conflict"
    http_status = 409


class AuthError(SignalIQError):
    code = "unauthorized"
    http_status = 401


class PermissionError(SignalIQError):
    code = "forbidden"
    http_status = 403


class QuotaExceededError(SignalIQError):
    code = "quota_exceeded"
    http_status = 429


class ExternalServiceError(SignalIQError):
    code = "external_service_error"
    http_status = 502


# ─────────────────────────────────────────────────────────────────────
# BYOK / context errors (S.5.2)
# ─────────────────────────────────────────────────────────────────────

class NoContextError(SignalIQError):
    """Raised when an LLM call has no current_org_id set.

    Every LLM call must belong to a user. There is no admin fallback.
    """
    code = "no_context"
    http_status = 500


class BlockDisabledError(SignalIQError):
    """Raised when the block that triggered an LLM call is off for this org."""
    code = "block_disabled"
    http_status = 403


class NoCredentialsError(SignalIQError):
    """Raised when the current org has no API credentials for this role."""
    code = "no_credentials"
    http_status = 402


class CredentialsExhaustedError(SignalIQError):
    """Raised when all of the org's credentials for a role are rate-limited."""
    code = "credentials_exhausted"
    http_status = 429


class CredentialVerificationError(SignalIQError):
    """A provider rejected a key, or couldn't be reached.

    reason ∈ {'invalid_key','rate_limited','model_unavailable',
              'provider_unreachable','provider_error','unknown_provider'}
    """
    def __init__(self, reason: str, message: str, provider: str | None = None):
        super().__init__(message)
        self.reason = reason
        self.provider = provider
