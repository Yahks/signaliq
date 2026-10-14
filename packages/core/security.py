"""Security primitives: password hashing and JWT creation/verification.

Design rules:
- Passwords: argon2id via passlib. Never log, never return in responses.
- Access tokens: short-lived (15 min), carry user_id + org_id + role.
- Refresh tokens: long-lived (7 days), opaque, only carry user_id + type.
- All tokens signed HS256 with SECRET_KEY from settings.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from packages.core.config import get_settings
from packages.core.error import AuthError

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


# ---------------------------------------------------------------- passwords
def hash_password(plain: str) -> str:
    """Hash a plaintext password with argon2id."""
    if not plain or len(plain) < 8:
        raise AuthError("Password must be at least 8 characters")
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verify a password against its hash."""
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:
        return False


# ---------------------------------------------------------------- tokens
TokenType = Literal["access", "refresh"]


def _now() -> datetime:
    return datetime.now(UTC)


def _encode(payload: dict[str, Any]) -> str:
    settings = get_settings()
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def _decode(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise AuthError("Invalid or expired token") from exc


def create_access_token(
    *,
    user_id: str,
    org_id: str | None,
    role: str | None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Create a short-lived access token."""
    settings = get_settings()
    now = _now()
    payload: dict[str, Any] = {
        "sub": user_id,
        "org": org_id,
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_ttl_min)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return _encode(payload)


def create_refresh_token(*, user_id: str) -> str:
    """Create a long-lived refresh token."""
    settings = get_settings()
    now = _now()
    payload = {
        "sub": user_id,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_refresh_ttl_days)).timestamp()),
    }
    return _encode(payload)


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a token. Enforces token type."""
    payload = _decode(token)
    if payload.get("type") != expected_type:
        raise AuthError(f"Expected {expected_type} token")
    return payload
