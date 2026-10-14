"""AI provider credentials — BYOK.

Each org stores its own LLM provider API keys. The router reads the
current org's credentials and calls the provider with the org's key,
not a global .env key.

Encryption:
  API keys are encrypted at rest with Fernet. The Fernet key is
  derived from `settings.secret_key` via SHA256. Rotating SECRET_KEY
  later means re-encrypting all stored credentials — a known cost.

Rotation:
  Multiple credentials per (org, role) are tried in priority order.
  When a provider returns 429, the router calls mark_exhausted() with
  a retry_after window; get_next_credential() skips exhausted keys
  until the window passes.

Cache:
  No caching. Credential reads are cheap (small table, indexed).
"""
from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import get_settings
from packages.core.logging import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Encryption
# ─────────────────────────────────────────────────────────────────────

_fernet_cache: Fernet | None = None


def _fernet() -> Fernet:
    global _fernet_cache
    if _fernet_cache is None:
        s = get_settings()
        raw = hashlib.sha256(s.secret_key.encode("utf-8")).digest()
        _fernet_cache = Fernet(base64.urlsafe_b64encode(raw))
    return _fernet_cache


def encrypt_key(plain: str) -> str:
    """Encrypt an API key for storage. Returns a URL-safe base64 string."""
    if not plain:
        raise ValueError("API key cannot be empty")
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_key(enc: str) -> str:
    """Decrypt a stored API key. Raises on tampering or wrong key."""
    return _fernet().decrypt(enc.encode("ascii")).decode("utf-8")


def mask_key(plain: str) -> str:
    """Return a mask like '****xyz4' for display. Never reveals the key."""
    if not plain or len(plain) < 8:
        return "****"
    return f"****{plain[-4:]}"

def _key_hash(plain: str) -> str:
    """Short deterministic hash suffix for a key — used in the default label.

    First 4 hex chars of sha256(plain). Distinct keys give distinct
    suffixes; the same key gives the same suffix across re-adds.
    """
    import hashlib
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()[:4]


def _key_fingerprint(plain: str) -> str:
    """Deterministic hash of the plaintext key, for equality checks.

    api_key_enc is Fernet ciphertext — random IV and timestamp, so two
    encryptions of the same plaintext never match. This fingerprint is
    stable across encrypts, so the pre-check and the unique constraint
    can compare keys reliably.
    """
    import hashlib
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()




# ─────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────

async def list_credentials(
    session: AsyncSession,
    org_id: str,
    *,
    role: str | None = None,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """List credentials for an org. Never returns the plaintext key.

    Returns rows with `key_preview` (masked) instead of `api_key_enc`.
    """
    where = ["org_id = :oid"]
    params: dict[str, Any] = {"oid": org_id}
    if role:
        where.append(":role = ANY(roles)")
        params["role"] = role
    if not include_inactive:
        where.append("is_active = true")

    rows = (await session.execute(sql_text(f"""
        SELECT id::text, provider, model_name, roles, label,
               api_key_enc, is_active, priority,
               exhausted_until, last_success_at, last_429_at,
               created_at, updated_at,
               last_remaining_tokens, last_rate_at,
               COALESCE((
                   SELECT SUM(COALESCE(input_tokens,0) + COALESCE(output_tokens,0))
                   FROM ai_calls
                   WHERE credential_id = ai_credentials.id
               ), 0)::int AS tokens_used,
               COALESCE((
                   SELECT COUNT(*)
                   FROM ai_calls
                   WHERE credential_id = ai_credentials.id
               ), 0)::int AS calls_made
        FROM ai_credentials
        WHERE {' AND '.join(where)}
        ORDER BY priority ASC, created_at ASC
    """), params)).all()

    out = []
    for r in rows:
        try:
            preview = mask_key(decrypt_key(r[5]))
        except Exception:
            preview = "****(invalid)"
        out.append({
            "id": r[0], "provider": r[1], "model_name": r[2], "roles": list(r[3] or []),
            "label": r[4], "key_preview": preview, "is_active": bool(r[6]),
            "priority": int(r[7]),
            "exhausted_until": r[8], "last_success_at": r[9], "last_429_at": r[10],
            "created_at": r[11], "updated_at": r[12],
            "last_remaining_tokens": r[13], "last_rate_at": r[14],
            "tokens_used": int(r[15] or 0), "calls_made": int(r[16] or 0),
        })
    return out


async def add_credential(
    session: AsyncSession,
    org_id: str,
    *,
    provider: str,
    model_name: str,
    roles: list[str],
    label: str,
    api_key: str,
    priority: int = 100,
) -> dict[str, Any]:
    """Insert a new credential. Encrypts the key at rest.

    `roles` is the list of roles this key serves (e.g. ['fast','reasoning']).
    Raises ValueError on invalid input, or on unique (org, label) conflict.
    """
    provider = provider.strip().lower()
    model_name = model_name.strip()
    roles = [r.strip().lower() for r in (roles or [])]
    label = label.strip()
    api_key = api_key.strip()

    if not provider or not model_name or not roles or not label or not api_key:
        raise ValueError("provider, model_name, roles, label, api_key are all required")
    for r in roles:
        if r not in ("fast", "reasoning", "embedding", "vision",
                     "verification"):
            raise ValueError(f"Invalid role: {r}")
    if len(label) < 2 or len(label) > 64:
        raise ValueError("Label must be 2-64 characters")
    if len(api_key) < 8:
        raise ValueError("API key looks too short")

    enc = encrypt_key(api_key)

    row = (await session.execute(sql_text("""
        INSERT INTO ai_credentials
            (org_id, provider, model_name, roles, label, api_key_enc, priority, key_fingerprint)
        VALUES
            (:oid, :prov, :model, :roles, :label, :enc, :prio, :fp)
        RETURNING id::text, created_at
    """), {
        "oid": org_id, "prov": provider, "model": model_name,
        "roles": roles, "label": label, "enc": enc, "prio": priority,
        "fp": _key_fingerprint(api_key),
    })).first()

    await session.commit()
    log.info("credentials.added", org_id=org_id[:8], provider=provider,
             roles=roles, label=label)
    return {
        "id": row[0], "provider": provider, "model_name": model_name,
        "roles": roles, "label": label, "key_preview": mask_key(api_key),
        "is_active": True, "priority": priority,
        "created_at": row[1],
    }


async def delete_credential(
    session: AsyncSession, org_id: str, cred_id: str,
) -> bool:
    """Delete a credential. Returns True if a row was removed."""
    r = await session.execute(sql_text("""
        DELETE FROM ai_credentials
        WHERE id = :cid AND org_id = :oid
    """), {"cid": cred_id, "oid": org_id})
    await session.commit()
    return (r.rowcount or 0) > 0


async def toggle_credential(
    session: AsyncSession, org_id: str, cred_id: str, is_active: bool,
) -> bool:
    """Enable/disable a credential without deleting it."""
    r = await session.execute(sql_text("""
        UPDATE ai_credentials
        SET is_active = :a, updated_at = now()
        WHERE id = :cid AND org_id = :oid
    """), {"cid": cred_id, "oid": org_id, "a": is_active})
    await session.commit()
    return (r.rowcount or 0) > 0


# ─────────────────────────────────────────────────────────────────────
# Rotation helpers (used by the router)
# ─────────────────────────────────────────────────────────────────────

async def get_next_credential(
    session: AsyncSession, org_id: str, role: str,
) -> dict[str, Any] | None:
    """Return the highest-priority active credential that isn't exhausted.

    Returns None if no usable credential exists.
    """
    now = datetime.now(UTC)
    row = (await session.execute(sql_text("""
        SELECT id::text, provider, model_name, api_key_enc, priority
        FROM ai_credentials
        WHERE org_id = :oid
          AND :role = ANY(roles)
          AND is_active = true
          AND (exhausted_until IS NULL OR exhausted_until <= :now)
        ORDER BY priority ASC, created_at ASC
        LIMIT 1
    """), {"oid": org_id, "role": role, "now": now})).first()

    if not row:
        return None
    try:
        plain = decrypt_key(row[3])
    except Exception as exc:
        log.warning("credentials.decrypt_failed", cred_id=row[0][:8],
                    error=str(exc)[:120])
        return None
    return {
        "id": row[0], "provider": row[1], "model_name": row[2],
        "api_key": plain, "priority": int(row[4]),
    }


async def mark_exhausted(
    session: AsyncSession, cred_id: str, seconds: int,
) -> None:
    """Mark a credential as rate-limited for `seconds`."""
    now = datetime.now(UTC)
    until = now + timedelta(seconds=seconds)
    await session.execute(sql_text("""
        UPDATE ai_credentials
        SET last_429_at = :now,
            exhausted_until = :until,
            updated_at = now()
        WHERE id = :cid
    """), {"cid": cred_id, "now": now, "until": until})
    await session.commit()
    log.info("credentials.marked_exhausted",
             cred_id=cred_id[:8], seconds=seconds)


async def mark_success(session: AsyncSession, cred_id: str) -> None:
    """Record a successful call for a credential."""
    await session.execute(sql_text("""
        UPDATE ai_credentials
        SET last_success_at = now(),
            exhausted_until = NULL,
            updated_at = now()
        WHERE id = :cid
    """), {"cid": cred_id})
    await session.commit()


# ─────────────────────────────────────────────────────────────────────
# Synchronous variants — used by the router (sync SQLAlchemy session)
# ─────────────────────────────────────────────────────────────────────

def get_next_credential_sync(session, org_id: str, role: str):
    """Sync version of get_next_credential."""
    now = datetime.now(UTC)
    row = session.execute(sql_text("""
        SELECT id::text, provider, model_name, api_key_enc, priority
        FROM ai_credentials
        WHERE org_id = :oid
          AND :role = ANY(roles)
          AND is_active = true
          AND (exhausted_until IS NULL OR exhausted_until <= :now)
        ORDER BY priority ASC, created_at ASC
        LIMIT 1
    """), {"oid": org_id, "role": role, "now": now}).first()

    if not row:
        return None
    try:
        plain = decrypt_key(row[3])
    except Exception as exc:
        log.warning("credentials.decrypt_failed", cred_id=row[0][:8],
                    error=str(exc)[:120])
        return None
    return {
        "id": row[0], "provider": row[1], "model_name": row[2],
        "api_key": plain, "priority": int(row[4]),
    }


def count_active_credentials_sync(session, org_id: str, role: str) -> int:
    """Return the number of active credentials for the org that serve
    the given role. Used to enforce the 10-key minimum before any
    generation runs (S.9.17)."""
    # RLS: ai_credentials is FORCE ROW LEVEL SECURITY. Set the org
    # context on whatever session we were given, so the policy can
    # see the org's rows even if the caller hasn't set it yet.
    session.execute(
        sql_text("SELECT set_config('app.current_org_id', :org, false)"),
        {"org": str(org_id)},
    )
    session.commit()
    row = session.execute(sql_text("""
        SELECT COUNT(*) FROM ai_credentials
        WHERE org_id = :oid AND :role = ANY(roles) AND is_active = true
    """), {"oid": org_id, "role": role}).first()
    return int(row[0]) if row else 0


async def count_active_credentials(
    session: AsyncSession, org_id: str, role: str,
) -> int:
    """Async variant for use in the API router."""
    row = (await session.execute(sql_text("""
        SELECT COUNT(*) FROM ai_credentials
        WHERE org_id = :oid AND :role = ANY(roles) AND is_active = true
    """), {"oid": org_id, "role": role})).first()
    return int(row[0]) if row else 0


def any_credentials_exist_sync(session, org_id: str, role: str) -> bool:
    """True if the org has any active credentials for this role (exhausted or not)."""
    session.execute(
        sql_text("SELECT set_config('app.current_org_id', :org, false)"),
        {"org": str(org_id)},
    )
    session.commit()
    row = session.execute(sql_text("""
        SELECT 1 FROM ai_credentials
        WHERE org_id = :oid AND :role = ANY(roles) AND is_active = true
        LIMIT 1
    """), {"oid": org_id, "role": role}).first()
    return row is not None


def mark_exhausted_sync(session, cred_id: str, seconds: int) -> None:
    """Sync version of mark_exhausted."""
    now = datetime.now(UTC)
    until = now + timedelta(seconds=seconds)
    session.execute(sql_text("""
        UPDATE ai_credentials
        SET last_429_at = :now,
            exhausted_until = :until,
            updated_at = now()
        WHERE id = :cid
    """), {"cid": cred_id, "now": now, "until": until})
    session.commit()
    log.info("credentials.marked_exhausted", cred_id=cred_id[:8], seconds=seconds)


def mark_success_sync(session, cred_id: str) -> None:
    """Sync version of mark_success."""
    session.execute(sql_text("""
        UPDATE ai_credentials
        SET last_success_at = now(), exhausted_until = NULL, updated_at = now()
        WHERE id = :cid
    """), {"cid": cred_id})
    session.commit()

# ─────────────────────────────────────────────────────────────────────
# Provider catalog + verify-then-save (S.5.4c)
# ─────────────────────────────────────────────────────────────────────

# Hardcoded per-provider catalog. One pasted key → the backend creates
# the right set of credentials (role + model + priority).
# Lower priority = tried first, matching ai_credentials.priority.
PROVIDER_CATALOG: dict[str, list[tuple[str, str, int]]] = {
    # S.8.50.3 — Groq keys serve all roles with gpt-oss-120b.
    # The old catalog listed gpt-oss-20b for "fast" as the first
    # entry, so add_provider_key stored every key as 20b with the
    # union of roles {fast, reasoning, verification}. The DB
    # CHECK constraint (S.8.50.2) forbids 20b+reasoning, so every
    # Groq key add failed. 20b also produces shorter, flatter
    # articles — the reviewer's exact complaint. 120b is the
    # generation model; all three roles map to it.
    "groq": [
        ("reasoning",    "openai/gpt-oss-120b", 10),
        ("verification", "openai/gpt-oss-120b", 10),
        ("fast",         "openai/gpt-oss-120b", 10),
    ],
    "gemini": [
        # S.8.37 — 3.8-flash 503s on high demand. 3.6-flash is the
        # newest model that responds reliably on both credentials.
        ("vision",       "gemini-3.6-flash",     35),
        ("verification", "gemini-3.6-flash",     36),
        ("reasoning",    "gemini-3.6-flash",     37),
    ],
    "openrouter": [
        ("fast",         "qwen/qwen3.8-27b:free",                  40),
        ("reasoning",    "nvidia/nemotron-3-super-120b-a12b:free", 50),
        ("verification", "qwen/qwen3.8-27b:free",                  55),
    ],
    "ollama": [
        ("fast",         "qwen2.5:0.5b-instruct", 60),
        ("verification", "qwen2.5:0.5b-instruct", 65),
        ("embedding",    "nomic-embed-text",      70),
    ],
}


def _classify_provider_error(err: str) -> tuple[str, str]:
    e = err.lower()
    if "401" in e or "unauthor" in e or "invalid api key" in e or "invalid key" in e:
        return "invalid_key", "The provider rejected this key. Check that you copied it correctly."
    if "429" in e or "rate" in e:
        return "rate_limited", "The provider is rate-limiting this key. Wait a few minutes and try again."
    if "404" in e or "not found" in e or "model" in e:
        return "model_unavailable", "The provider doesn't recognise the configured model. Contact admin."
    if "timeout" in e or "timed out" in e:
        return "provider_unreachable", "The provider didn't respond in time. Try again or contact admin."
    if "connection" in e or "network" in e or "dns" in e:
        return "provider_unreachable", "Couldn't reach the provider. Check your internet connection and try again."
    return "provider_error", "The provider returned an error. Try again or contact admin."


async def verify_key(
    provider: str, api_key: str, timeout_seconds: int = 30,
) -> tuple[bool, str | None, str | None]:
    """Test a provider key with a tiny call.

    Returns (ok, reason, message). reason/message are None on success.

    S.8.11.13 — default raised from 10s to 30s. Gemini flash models
    are thinking models; even with thinking_budget=0 attempted, a
    cold call can queue behind other traffic. A 30s budget absorbs
    normal queueing without turning a valid key into a false
    "provider_unreachable" error.
    """
    import asyncio

    provider = provider.strip().lower()

    if provider not in PROVIDER_CATALOG:
        return False, "unknown_provider", f"Unknown provider: {provider}"

    # Ollama has no key; a successful ping to the local server = verified.
    if provider == "ollama":
        try:
            from packages.ai.providers.ollama_provider import OllamaProvider
            p = OllamaProvider()
            await asyncio.wait_for(
                p.complete(model="qwen2.5:0.5b-instruct", system=None,
                           user="ok", temperature=0.0, max_tokens=5,
                           response_format_json=False),
                timeout=timeout_seconds,
            )
            return True, None, None
        except Exception as exc:
            r, m = _classify_provider_error(str(exc))
            return False, r, "Local Ollama server isn't running. Contact admin."

    # Cloud providers: build the provider with the candidate key, call.
    try:
        from packages.ai.providers.groq_provider import GroqProvider
        from packages.ai.providers.gemini_provider import GeminiProvider
        from packages.ai.providers.openrouter_provider import OpenRouterProvider

        if provider == "groq":
            p = GroqProvider(api_key=api_key)
            model = "openai/gpt-oss-20b"
        elif provider == "gemini":
            p = GeminiProvider(api_key=api_key)
            # S.8.11.11 — verify with the same model the catalog
            # specifies for this provider, not a hardcoded alias.
            # Google's "latest" alias sometimes routes to a heavily
            # loaded model, and the catalog's explicit name
            # (gemini-3.8-flash) is more reliable.
            model = PROVIDER_CATALOG[provider][0][1]
        elif provider == "openrouter":
            p = OpenRouterProvider(api_key=api_key)
            model = "nvidia/nemotron-3-super-120b-a12b:free"
        else:
            return False, "unknown_provider", f"Unknown provider: {provider}"

        await asyncio.wait_for(
            p.complete(model=model, system="Reply with one word.",
                       user="ok", temperature=0.0, max_tokens=5,
                       response_format_json=False),
            timeout=timeout_seconds,
        )
        return True, None, None
    except asyncio.TimeoutError:
        return False, "provider_unreachable", f"{provider.title()} didn't respond in {timeout_seconds}s. Try again or contact admin."
    except Exception as exc:
        r, m = _classify_provider_error(str(exc))
        return False, r, m


async def add_provider_key(
    session: AsyncSession, org_id: str, *,
    provider: str, api_key: str, label: str | None = None,
) -> list[dict[str, Any]]:
    """Verify the key, then create ONE credential row for it.

    S.6.4.1b — one row per key. `roles` is the set of roles from the
    catalog for this provider. A second key of the same provider gets a
    distinct label (`{provider}-{hash4}` or the user's label), so it
    augments the rotation pool instead of overwriting.

    Raises CredentialVerificationError on verification failure.
    """
    from packages.core.error import CredentialVerificationError

    provider = provider.strip().lower()
    if provider not in PROVIDER_CATALOG:
        raise CredentialVerificationError(
            "unknown_provider", f"Unknown provider: {provider}", provider,
        )

    ok, reason, message = await verify_key(provider, api_key)
    if not ok:
        raise CredentialVerificationError(reason or "provider_error",
                                           message or "Verification failed.",
                                           provider)

    catalog = PROVIDER_CATALOG[provider]
    # Distinct set of roles this key serves
    all_roles = sorted({r for r, _m, _p in catalog})
    # Lowest priority from the catalog is the credential's priority
    min_priority = min(p for _r, _m, p in catalog)
    # Recorded model — informational; the router picks per role at call time
    first_model = catalog[0][1]

    # User-supplied label wins if present. Otherwise hash-derive from the key.
    if label and label.strip():
        final_label = label.strip()[:64]
    else:
        final_label = f"{provider}-{_key_hash(api_key)}"

    # ── S.6.4.1g — reject duplicate keys by fingerprint ──────────────
    # api_key_enc is Fernet ciphertext (non-deterministic). The
    # fingerprint is sha256(plaintext) and IS deterministic.
    enc = encrypt_key(api_key)
    fp = _key_fingerprint(api_key)

    existing_key = (await session.execute(sql_text("""
        SELECT label FROM ai_credentials
        WHERE org_id = :oid AND key_fingerprint = :fp
        LIMIT 1
    """), {"oid": org_id, "fp": fp})).first()
    if existing_key:
        raise CredentialVerificationError(
            "duplicate_key",
            f"This key is already registered as '{existing_key[0]}'. "
            f"Delete that row if you want to re-add it.",
            provider,
        )

    # No label-collision check: labels are display names, not identities.

    # ── No fingerprint collision — safe to upsert ────────────────────
    cred = await upsert_credential(
        session, org_id,
        provider=provider, model_name=first_model, roles=all_roles,
        label=final_label, api_key=api_key, priority=min_priority,
    )
    return [cred]

async def upsert_credential(
    session: AsyncSession,
    org_id: str,
    *,
    provider: str,
    model_name: str,
    roles: list[str],
    label: str,
    api_key: str,
    priority: int = 100,
) -> dict[str, Any]:
    """Insert a credential, or update it if (org_id, label) already exists.

    Single statement, no exception handling. Idempotent — safe to call
    repeatedly with the same arguments. Clears exhausted_until on update
    (a re-added key should start fresh).
    """
    provider = provider.strip().lower()
    model_name = model_name.strip()
    roles = [r.strip().lower() for r in (roles or [])]
    label = label.strip()
    api_key = api_key.strip()

    if not provider or not model_name or not roles or not label or not api_key:
        raise ValueError("provider, model_name, roles, label, api_key are all required")
    for r in roles:
        if r not in ("fast", "reasoning", "embedding", "vision",
                     "verification"):
            raise ValueError(f"Invalid role: {r}")
    if len(label) < 2 or len(label) > 64:
        raise ValueError("Label must be 2-64 characters")
    if len(api_key) < 8:
        raise ValueError("API key looks too short")

    enc = encrypt_key(api_key)

    row = (await session.execute(sql_text("""
        INSERT INTO ai_credentials
            (org_id, provider, model_name, roles, label, api_key_enc, priority, key_fingerprint)
        VALUES
            (:oid, :prov, :model, :roles, :label, :enc, :prio, :fp)
        ON CONFLICT (org_id, key_fingerprint) DO UPDATE
        SET provider        = EXCLUDED.provider,
            model_name      = EXCLUDED.model_name,
            roles           = EXCLUDED.roles,
            label           = EXCLUDED.label,
            priority        = EXCLUDED.priority,
            is_active       = true,
            exhausted_until = NULL,
            updated_at      = now()
        RETURNING id::text, created_at
    """), {
        "oid": org_id, "prov": provider, "model": model_name,
        "roles": roles, "label": label, "enc": enc, "prio": priority,
        "fp": _key_fingerprint(api_key),
    })).first()

    await session.commit()
    log.info("credentials.upserted", org_id=org_id[:8], provider=provider,
             roles=roles, label=label)
    return {
        "id": row[0], "provider": provider, "model_name": model_name,
        "roles": roles, "label": label, "key_preview": mask_key(api_key),
        "is_active": True, "priority": priority,
        "created_at": row[1],
    }

def list_credentials_sync(session, org_id: str, role: str) -> list[dict]:
    """Return ALL non-exhausted active credentials for (org, role),
    ordered by priority ASC, then created_at ASC. Decrypted.

    Used by the router's rotation loop. Each item is a dict with
    id, provider, model_name, api_key, priority.
    """
    session.execute(
        sql_text("SELECT set_config('app.current_org_id', :org, false)"),
        {"org": str(org_id)},
    )
    session.commit()
    now = datetime.now(UTC)
    rows = session.execute(sql_text("""
        SELECT id::text, provider, model_name, api_key_enc, priority
        FROM ai_credentials
        WHERE org_id = :oid
          AND :role = ANY(roles)
          AND is_active = true
          AND (exhausted_until IS NULL OR exhausted_until <= :now)
        ORDER BY priority ASC, created_at ASC
    """), {"oid": org_id, "role": role, "now": now}).all()

    out: list[dict] = []
    for r in rows:
        try:
            plain = decrypt_key(r[3])
        except Exception as exc:
            log.warning("credentials.decrypt_failed_in_list",
                        cred_id=r[0][:8], error=str(exc)[:120])
            continue
        out.append({
            "id": r[0], "provider": r[1], "model_name": r[2],
            "api_key": plain, "priority": int(r[4]),
        })
    return out


def parse_retry_after(err_msg: str, default_seconds: int = 60) -> int:
    """Extract the rate-limit reset window from a Groq error string.

    Order of preference:
      1. "try again in 5.6s"         — RPM/TPM window (seconds)
      2. "retry after 30"            — header-style hint
      3. "try again at 4:15:00 PM"   — RPD/TPD window (until a clock time)
      4. default_seconds             — no hint found; 60s, not 900s
    Capped at 3600s so a malformed message can't lock the key for a day.

    S.9.19 — the previous default was 900s (15 minutes). A single
    unparseable 429 locked all keys for 15 minutes, so a burst
    exhausted the pool. 60s clears a per-minute window without
    wasting the key.
    """
    import re
    from datetime import datetime, timedelta, timezone

    if not err_msg:
        return default_seconds

    # 1. "try again in 5.6s"
    m = re.search(r"try again in ([\d.]+)s", err_msg)
    if m:
        try:
            secs = float(m.group(1)) + 1.0
            return max(1, min(int(secs), 3600))
        except ValueError:
            pass

    # 2. "retry after 30"
    m = re.search(r"retry.{0,5}after[^\d]*([\d.]+)", err_msg, re.IGNORECASE)
    if m:
        try:
            secs = float(m.group(1)) + 1.0
            return max(1, min(int(secs), 3600))
        except ValueError:
            pass

    # 3. "try again at 4:15:00 PM" / "resets at 12:00:00 AM"
    m = re.search(
        r"(?:try again|resets?)\s+at\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?",
        err_msg,
        re.IGNORECASE,
    )
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        ss = int(m.group(3) or 0)
        ampm = (m.group(4) or "").upper()
        if ampm == "PM" and hh < 12:
            hh += 12
        elif ampm == "AM" and hh == 12:
            hh = 0
        now = datetime.now(timezone.utc)
        try:
            target = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
        except ValueError:
            return default_seconds
        if target <= now:
            target += timedelta(days=1)
        secs = int((target - now).total_seconds()) + 1
        return max(1, min(secs, 3600))

    # 4. No hint → short fallback
    return default_seconds

async def clear_exhausted(
    session: AsyncSession, org_id: str, cred_id: str,
) -> bool:
    """S.6.4.2 — clear exhausted_until for one credential.

    Called from the manual "reset cooldown" endpoint. The router will
    then include this credential again in its rotation pool. If the
    provider still rate-limits it, the router will mark it exhausted
    again on the next 429 — so this is safe.

    Returns True if a row was updated, False if no matching credential
    for this org.
    """
    r = await session.execute(sql_text("""
        UPDATE ai_credentials
        SET exhausted_until = NULL,
            last_429_at     = NULL,
            updated_at      = now()
        WHERE id = :cid AND org_id = :oid
    """), {"cid": cred_id, "oid": org_id})
    await session.commit()
    return (r.rowcount or 0) > 0
