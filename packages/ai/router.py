"""AI router: pick the right model for a role, cache results, track cost.

Uses the OWNER DB session because ai_calls / ai_models are global (not
tenant-scoped) — one LLM call may be served from cache across orgs.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from sqlalchemy import create_engine, select, text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from packages.ai.providers.base import ModelProvider, ProviderError
from packages.ai.budget.cost_tracker import check_budget, record_usage
from packages.ai.providers.registry import get_provider
from packages.core.config import get_settings
from packages.core.logging import get_logger
from packages.core.credentials import (
    get_next_credential_sync,
    list_credentials_sync,
    parse_retry_after,
    mark_exhausted_sync,
    mark_success_sync,
    any_credentials_exist_sync,
    count_active_credentials_sync,
)
from packages.core.error import (
    NoContextError,
    BlockDisabledError,
    NoCredentialsError,
    CredentialsExhaustedError,
)

log = get_logger(__name__)


# S.9.22 — one sync engine for the whole process. The old version
# created a new engine on every call and never disposed it, leaking
# one Postgres connection per LLM call. Under load, the pool filled
# up and Postgres refused new clients.
_SYNC_ENGINE = None
_SYNC_SESSION_FACTORY = None


def _sync_engine():
    global _SYNC_ENGINE
    if _SYNC_ENGINE is None:
        settings = get_settings()
        _SYNC_ENGINE = create_engine(
            settings.database_url_sync,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=1800,
            pool_timeout=30,
        )
    return _SYNC_ENGINE


def _sync_session() -> Session:
    global _SYNC_SESSION_FACTORY
    if _SYNC_SESSION_FACTORY is None:
        _SYNC_SESSION_FACTORY = sessionmaker(
            bind=_sync_engine(), expire_on_commit=False,
        )
    return _SYNC_SESSION_FACTORY()


def _hash_request(provider: str, model: str, system: str | None, user: str, json_mode: bool) -> str:
    """Stable hash of the call inputs for the ai_calls cache.

    S.8.11.4 — the hash deliberately does NOT include image bytes.
    Vision calls (images != None) bypass the cache entirely: the
    router checks `images` before the cache lookup and skips it.
    This avoids cross-format hash collisions (PNG vs JPEG of the
    same chart, metadata differences) and matches the requirement
    that vision analyses always run fresh.
    """
    h = hashlib.sha256()
    h.update(provider.encode())
    h.update(b"|")
    h.update(model.encode())
    h.update(b"|")
    h.update((system or "").encode())
    h.update(b"|")
    h.update(user.encode())
    h.update(b"|")
    h.update(b"json" if json_mode else b"text")
    return h.hexdigest()


async def complete(
    *,
    role: str,
    system: str | None,
    user: str,
    org_id: str | None = None,
    purpose: str = "generic",
    temperature: float = 0.0,
    max_tokens: int | None = None,
    json_mode: bool = False,
    use_cache: bool = True,
    prompt_version_id: str | None = None,
    images: list[bytes] | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """Complete a prompt using the model assigned to `role`.

    S.6.2.0: iterates the org's credentials for this role in priority order.
    On 429 the credential is marked exhausted and the next is tried. Any
    other error aborts the call (no rotation on 400/500).

    Every LLM call must have an org context (S.5.2 decision B).
    """
    session = _sync_session()
    try:
        # ─── Gate 1: org context ─────────────────────────────────────
        from packages.core.context import current_org_id as _org_ctx
        org_ctx = _org_ctx.get()
        if not org_ctx:
            raise NoContextError(
                "No organisation context — every LLM call must belong to a user"
            )

        # Set the RLS context on this connection. ai_credentials,
        # ai_calls, ai_models and other tenant-scoped tables are
        # FORCE ROW LEVEL SECURITY. Without app.current_org_id set,
        # the policies hide every row and count_active_credentials
        # returns 0. Session-scoped (false), because this Session
        # commits multiple times during the call.
        session.execute(
            sql_text("SELECT set_config('app.current_org_id', :org, false)"),
            {"org": str(org_ctx)},
        )
        session.commit()

        # ─── Gate 2: budget ──────────────────────────────────────────
        if org_id:
            check_budget(org_id)

        # ─── Gate 3: block enabled ───────────────────────────────────
        # S.7.2 — missing row means DISABLED. Under the onboarding model
        # a brand-new org has no org_block_settings rows until the picker
        # writes them, so the pre-S.7.2 "no row = enable" reading would
        # have allowed generation for orgs that had not selected the
        # block.
        block_slug = _block_for_purpose(purpose)
        if block_slug:
            from packages.core.blocks import is_block_enabled_sync
            if not is_block_enabled_sync(session, org_ctx, block_slug):
                raise BlockDisabledError(
                    f"The {block_slug} block is not enabled for your "
                    f"organisation. Add it in settings to generate."
                )

        # ─── Gate 4: platform tokens ─────────────────────────────────
        from packages.core.tokens import can_generate_sync
        from packages.core.context import current_user_id as _uid_ctx
        _uid = _uid_ctx.get()
        if _uid and not can_generate_sync(session, _uid):
            raise NoCredentialsError("Insufficient platform tokens")

        # ─── Gate 5: minimum 10 credentials ──────────────────────────
        # S.9.17 — the floor is 10, not 1. One key rate-limits and
        # stalls the org; ten give the router a rotation pool.
        from packages.core.config import get_settings as _get_settings
        _minimum = int(getattr(_get_settings(), "min_keys_for_generation", 10))
        _count = count_active_credentials_sync(session, org_ctx, role)
        if _count < _minimum:
            raise NoCredentialsError(
                f"{_count} of {_minimum} required API keys are configured "
                f"for this organisation. Add the rest at "
                f"/settings/credentials to enable generation."
            )

        # ─── Build the rotation list ─────────────────────────────────
        creds = list_credentials_sync(session, org_ctx, role)
        # S.9.34 — drop any credential with a falsy id. A malformed
        # or shadowed row can return an empty id; binding "" to the
        # uuid column in mark_exhausted aborts the transaction and
        # cascades into the caller's persist_post.
        creds = [c for c in creds if c.get("id")]
        if not creds:
            raise CredentialsExhaustedError(
                "All of your API keys are currently rate-limited. "
                "Add another key at /settings/credentials or wait for the limits to reset."
            )

        # ─── Try each credential until one succeeds or all fail ──────
        last_error: str | None = None
        for idx, cred in enumerate(creds):
            try:
                return await _try_with_credential(
                    session=session,
                    cred=cred,
                    role=role,
                    purpose=purpose,
                    org_id=org_id,
                    system=system,
                    user=user,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    use_cache=use_cache,
                    prompt_version_id=prompt_version_id,
                    images=images,
                    reasoning_effort=reasoning_effort,
                )
            except CredentialsExhaustedError as exc:
                # This credential 429'd. Mark exhausted (done inside
                # _try_with_credential) and try the next.
                last_error = str(exc)
                log.warning(
                    "ai.cred_rotate",
                    cred_id=cred["id"][:8],
                    provider=cred["provider"],
                    position=f"{idx+1}/{len(creds)}",
                    error=last_error[:150],
                )
                continue
            # ProviderError (non-429) propagates — no rotation on 400/500.

        raise CredentialsExhaustedError(
            f"All {len(creds)} credentials rate-limited. Last: {(last_error or '')[:200]}"
        )
    finally:
        # Roll back any pending transaction, then clear the RLS
        # context before the connection returns to the pool, so the
        # next caller doesn't inherit this org or an aborted tx.
        try:
            session.rollback()
        except Exception:
            pass
        try:
            session.execute(
                sql_text("SELECT set_config('app.current_org_id', '', false)"),
            )
            session.commit()
        except Exception:
            pass
        session.close()

async def embed(
    *,
    texts: list[str],
    org_id: str | None = None,
    purpose: str = "generic",
) -> list[list[float]]:
    """Generate embeddings using the model assigned to the `embedding` role."""
    session = _sync_session()
    try:
        row = session.execute(
            sql_text("""
                SELECT id, provider, model_name
                FROM ai_models
                WHERE role = 'embedding' AND is_active = true
                ORDER BY created_at
                LIMIT 1
            """),
        ).first()
        if not row:
            raise ProviderError("No active embedding model")

        model_id, provider_name, model_name = row
        provider: ModelProvider = get_provider(provider_name)

        t0 = time.monotonic()
        try:
            result = await provider.embed(model=model_name, texts=texts)
            latency_ms = int((time.monotonic() - t0) * 1000)
            status = "ok"
            err_msg = None
        except ProviderError as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            result = None
            status = "error"
            err_msg = str(exc)[:1000]

        # S.9.33 — the ai_calls write is auxiliary (billing). If it fails, do not abort the caller's transaction. Roll back the sync session and continue.

        try:

            session.execute(
            sql_text("""
            INSERT INTO ai_calls (org_id, model_id, purpose, latency_ms, status, error)
            VALUES (:org_id, :model_id, :purpose, :latency, :status, :error)
            """),
            {
            "org_id": org_id,
            "model_id": model_id,
            "purpose": purpose,
            "latency": latency_ms,
            "status": status,
            "error": err_msg,
            },
            )
            session.commit()

        except Exception as _exc:

            import traceback as _tb; _tb.print_exc()

            try:

                session.rollback()

            except Exception:

                pass
        session.commit()

        if status != "ok" or result is None:
            raise ProviderError(err_msg or "embed failed")

        log.info("ai.embed", count=len(texts), dim=result.dim, latency_ms=latency_ms)
        return result.vectors

    finally:
        session.close()

async def _try_with_credential(
    *,
    session: Session,
    cred: dict,
    role: str,
    purpose: str,
    org_id: str | None,
    system: str | None,
    user: str,
    temperature: float,
    max_tokens: int | None,
    json_mode: bool,
    use_cache: bool,
    prompt_version_id: str | None = None,
    images: list[bytes] | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """Call the provider with ONE credential. Raises:
      - CredentialsExhaustedError on 429 (outer loop rotates)
      - ProviderError on any other error (outer loop aborts)
    """
    provider_name = cred["provider"]
    model_name = cred["model_name"]
    cred_id = cred["id"]
    api_key = cred["api_key"]

    # Cache lookup (provider+model specific).
    # S.8.11.4 — vision calls bypass the cache entirely. The
    # request hash does not include image bytes (deliberately: image
    # hashing has cross-format footguns), so caching a vision call
    # would return a stale analysis for a different image. Fresh
    # every time.
    req_hash = _hash_request(provider_name, model_name, system, user, json_mode)
    if use_cache and not images:
        cached = session.execute(
            sql_text("""
                SELECT response_payload
                FROM ai_calls
                WHERE request_hash = :h AND status = 'ok'
                  AND response_payload IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"h": req_hash},
        ).first()
        if cached and cached[0]:
            log.info("ai.cache_hit", role=role, purpose=purpose, model=model_name)
            return cached[0].get("text", "")

    provider: ModelProvider = _build_provider(provider_name, api_key)
    t0 = time.monotonic()
    result = None
    err_msg = None
    try:
        result = await provider.complete(
            model=model_name,
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_json=json_mode,
            images=images,            reasoning_effort=reasoning_effort,
        )
        # S.6.4.7 — a response is only "ok" if it's complete and
        # non-empty. finish_reason='length' means the model hit
        # max_tokens and was truncated; an empty text means nothing
        # was produced. Both are uncacheable: the cache lookup filters
        # status='ok', so a truncated row will never be served as a
        # cached response, and the next call will retry fresh.
        _text = (result.text or "").strip() if result else ""
        _truncated = bool(result) and getattr(result, "finish_reason", None) == "length"
        if result and (_truncated or not _text):
            status = "truncated"
            log.warning(
                "ai.truncated",
                purpose=purpose,
                provider=provider_name,
                model=model_name,
                in_tokens=result.input_tokens,
                out_tokens=result.output_tokens,
                max_tokens=max_tokens,
                finish_reason=getattr(result, "finish_reason", None),
                text_len=len(_text),
            )
        else:
            status = "ok"
    except ProviderError as exc:
        err_msg = str(exc)[:1000]
        status = "error"

    latency_ms = int((time.monotonic() - t0) * 1000)

    # Record the attempt (success or failure) in ai_calls.
    import json as _json
    payload_json = None if not result else _json.dumps({"text": result.text})

    # S.6.2.0.1 — resolve model_id from the ai_models catalog so the row
    # is joinable. NULL when this credential's provider+model isn't in
    # the catalog (a newer model we haven't registered).
    model_row = session.execute(
        sql_text("""
            SELECT id::text FROM ai_models
            WHERE provider = :p AND model_name = :m
            ORDER BY created_at ASC
            LIMIT 1
        """),
        {"p": provider_name, "m": model_name},
    ).first()
    model_id = model_row[0] if model_row else None

    session.execute(
        sql_text("""
            INSERT INTO ai_calls (
                org_id, model_id, credential_id, prompt_version_id, purpose,
                input_tokens, output_tokens,
                latency_ms, status, error, request_hash, response_payload,
                rate_headers
            ) VALUES (
                :org_id, :model_id, :credential_id, :prompt_version_id, :purpose,
                :in_tok, :out_tok,
                :latency, :status, :error, :hash, CAST(:payload AS jsonb),
                CAST(:rate_headers AS jsonb)
            )
        """),
        {
            "org_id": org_id,
            "model_id": model_id,
            "credential_id": cred_id,
            # S.9.32 — the DB column is uuid. Empty string fails
            # the insert with 'invalid input syntax for type uuid: ""'.
            # Coerce to None — the column is nullable.
            "prompt_version_id": prompt_version_id or None,
            "purpose": purpose,
            "in_tok": result.input_tokens if result else None,
            "out_tok": result.output_tokens if result else None,
            "latency": latency_ms,
            "status": status,
            "error": err_msg,
            "hash": req_hash,
            "payload": payload_json,
            "rate_headers": (
                _json.dumps(result.rate_headers)
                if result and getattr(result, "rate_headers", None)
                else None
            ),
        },
    )
    session.commit()

    # ── Real rate limit → mark exhausted + rotate ────────────────
    # S.6.3.5a — the previous check matched the bare substring "rate",
    # which also appears in our own "Credential rate-limited" wrapper text
    # and in "Request too large" bodies. That caused 413 Payload Too Large
    # to exhaust a credential for 15 minutes on a request that was simply
    # too big, not rate-limited. Match only the actual rate-limit
    # signatures:
    if status == "error" and err_msg:
        msg_lower = err_msg.lower()
        # S.9.25 — reject non-429 4xx explicitly. The 413 (request
        # too large) body contains "tpm" inside JSON, which used to
        # match the substring check and mark the credential
        # exhausted for 15 minutes. A 413 is not a rate limit.
        _non_rate_status = any(
            f"http {code}" in msg_lower
            for code in (400, 401, 403, 404, 405, 409, 413, 415, 422)
        )
        is_rate_limit = (
            not _non_rate_status
            and (
                "http 429" in msg_lower
                or "rate_limit_exceeded" in msg_lower
                or "tokens per minute" in msg_lower
                or "requests per minute" in msg_lower
            )
        )
        if is_rate_limit:
            try:
                retry_after = parse_retry_after(err_msg, default_seconds=900)
                mark_exhausted_sync(session, cred_id, seconds=retry_after)
                log.info(
                    "ai.cred_exhausted",
                    cred_id=cred_id[:8],
                    provider=provider_name,
                    retry_after=retry_after,
                )
            except Exception as _exc:
                log.warning("router.mark_exhausted_failed", error=str(_exc)[:120])
            raise CredentialsExhaustedError(
                f"Credential rate-limited ({provider_name}): {err_msg[:200]}"
            )
        # Every other error (413, 400, 401, 500) is a real failure.
        # Do NOT rotate — a fresh credential won't fix a malformed request.
        raise ProviderError(err_msg)

    # ── Success ──
    try:
        mark_success_sync(session, cred_id)
    except Exception as exc:
        log.warning("router.mark_success_failed", error=str(exc)[:120])

    # S.6.4.3 — cache the last-known Groq rate state on the credential
    # row so list_credentials can serve it without a JOIN.
    if result is not None:
        rh = getattr(result, "rate_headers", None)
        if rh:
            try:
                remaining_str = rh.get("x-ratelimit-remaining-tokens")
                remaining = int(remaining_str) if remaining_str else None
                session.execute(sql_text("""
                    UPDATE ai_credentials
                    SET last_remaining_tokens = :rt,
                        last_rate_headers = CAST(:rh AS jsonb),
                        last_rate_at = now()
                    WHERE id = :cid
                """), {
                    "rt": remaining,
                    "rh": _json.dumps(rh),
                    "cid": cred_id,
                })
                session.commit()
            except Exception as exc:
                log.warning("router.rate_state_update_failed", error=str(exc)[:120])

    if org_id:
        record_usage(
            org_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=0.0,
        )

    log.info(
        "ai.complete",
        role=role,
        purpose=purpose,
        model=model_name,
        provider=provider_name,
        latency_ms=latency_ms,
        in_tokens=result.input_tokens,
        out_tokens=result.output_tokens,
    )
    return result.text

def _block_for_purpose(purpose: str) -> str | None:
    """Map a purpose string to the block that gates it.

    Every LLM call belongs to a block. Trading purposes → 'trading'.
    News-chain + enrichment → 'news'. Unknown → None (no block gate).
    """
    if purpose.startswith("trading."):
        return "trading"
    if purpose.startswith("chain.") or purpose.startswith("enrich."):
        return "news"
    return None


def _build_provider(provider_name: str, api_key: str):
    """Construct a provider with an explicit key, bypassing global settings."""
    from packages.ai.providers.groq_provider import GroqProvider
    from packages.ai.providers.gemini_provider import GeminiProvider
    from packages.ai.providers.openrouter_provider import OpenRouterProvider
    from packages.ai.providers.ollama_provider import OllamaProvider

    if provider_name == "groq":
        return GroqProvider(api_key=api_key)
    if provider_name == "gemini":
        return GeminiProvider(api_key=api_key)
    if provider_name == "openrouter":
        return OpenRouterProvider(api_key=api_key)
    if provider_name == "mock_429":
        # S.6.2.0.1 — deterministic 429 for the rotation test.
        # Not registered anywhere else; only reachable when a credential
        # has provider="mock_429" in the DB.
        from packages.ai.providers.base import ProviderError as _PE

        class _Always429:
            async def complete(self, **kwargs):
                raise _PE("Groq HTTP 429: rate limit reached for mock_429")

        return _Always429()
    if provider_name == "ollama":
        return OllamaProvider()
    raise ProviderError(f"Unknown provider: {provider_name}")
