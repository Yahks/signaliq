"""Credential management endpoints.

Users add their own LLM provider keys here. Every org has its own
credentials, encrypted at rest with Fernet (key derived from SECRET_KEY).

Writes are owner/admin-only. The plaintext key is never returned.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, current_user_id, require_role
from apps.api.schemas.credentials import (
    CreateCredentialSimpleRequest,
    SupportRequest,
    CreateCredentialRequest,
    CredentialListResponse,
    CredentialResponse,
    ToggleCredentialRequest,
)
from packages.core.credentials import (
    clear_exhausted,
    count_active_credentials,
    add_provider_key,
    add_credential,
    delete_credential,
    list_credentials,
    toggle_credential,
)
from packages.db.session import get_session
from packages.db.tenant import set_current_org
from packages.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/settings/credentials", tags=["credentials"])


async def _scoped(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


def _to_response(c: dict) -> CredentialResponse:
    return CredentialResponse(
        id=c["id"], provider=c["provider"], model_name=c["model_name"],
        roles=c.get("roles") or [], label=c["label"], key_preview=c["key_preview"],
        is_active=c["is_active"], priority=c["priority"],
        exhausted_until=c.get("exhausted_until"),
        last_success_at=c.get("last_success_at"),
        created_at=c["created_at"],
        tokens_used=c.get("tokens_used") or 0,
        calls_made=c.get("calls_made") or 0,
        last_remaining_tokens=c.get("last_remaining_tokens"),
        last_rate_at=c.get("last_rate_at"),
    )


@router.get("", response_model=CredentialListResponse)
async def list_my_credentials(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped),
) -> CredentialListResponse:
    """List this org's credentials. Keys are masked."""
    items = await list_credentials(session, org_id, include_inactive=True)
    return CredentialListResponse(items=[_to_response(c) for c in items])




async def _maybe_enable_generation(
    session: AsyncSession, org_id: str, user_id: str,
) -> None:
    """S.9.17 — flip the schedule on when the org reaches 10 keys.

    Called after every successful credential add. Idempotent — if the
    schedule is already enabled, do nothing.
    """
    from packages.core.config import get_settings
    minimum = int(getattr(get_settings(), "min_keys_for_generation", 10))

    count = await count_active_credentials(session, org_id, "reasoning")
    if count < minimum:
        return

    row = (await session.execute(sql_text("""
        SELECT settings->'schedule'->>'enabled' FROM orgs WHERE id = :oid
    """), {"oid": org_id})).first()
    if row and row[0] == "true":
        return

    # Flip schedule on, set next_fire_at = now + 5 hours.
    await session.execute(sql_text("""
        UPDATE orgs
        SET settings = jsonb_set(
            jsonb_set(
                COALESCE(settings, '{}'::jsonb),
                '{schedule,enabled}', 'true'::jsonb
            ),
            '{schedule,next_fire_at}',
            to_jsonb((now() + interval '5 hours')::text)
        )
        WHERE id = :oid
    """), {"oid": org_id})

    await session.execute(sql_text("""
        INSERT INTO notifications (org_id, user_id, kind, title, body)
        VALUES (
            :oid, :uid, 'generation_enabled',
            'Generation is on',
            'You have 10 API keys. The pipeline will run every 5 hours. '
            'Your first articles will appear in the drafts queue.'
        )
    """), {"oid": org_id, "uid": user_id})

    # S.9.18.1 — commit. Without this, the caller's session closes
    # without flushing and the UPDATE + INSERT are rolled back.
    # The log line used to run before the commit, so it printed
    # "generation_enabled" while the DB still showed enabled=false.
    await session.commit()

    log.info("credentials.generation_enabled",
             org_id=org_id[:8], count=count)


@router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(
    body: CreateCredentialSimpleRequest,
    org_id: str = Depends(current_org_id),
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped),
) -> CredentialResponse:
    """Verify a provider key and add it. Owner/admin only.

    Returns the first created credential. All catalog entries for the
    provider are created in one call.

    On verification failure: 400 with
        {"detail": {"reason": "...", "message": "...", "provider": "..."}}
    """
    from packages.core.error import CredentialVerificationError

    try:
        created = await add_provider_key(
            session, org_id,
            provider=body.provider, api_key=body.api_key,
            label=getattr(body, "label", None),
        )
    except CredentialVerificationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": exc.reason,
                "message": str(exc),
                "provider": exc.provider,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # S.6.4.1e — log the real exception before returning the generic
        # 500. Without this, uvicorn swallows the traceback (Starlette
        # treats HTTPException as a normal response), and the crash is
        # invisible.
        import logging
        logging.getLogger(__name__).exception(
            "credentials.add_failed: %s: %s",
            type(exc).__name__, str(exc)[:500],
        )
        raise HTTPException(status_code=500, detail="Failed to add credential") from exc

    # S.9.17 — flip generation on when the org reaches the minimum.
    try:
        await _maybe_enable_generation(session, org_id, current_user_id())
    except Exception as exc:
        log.warning("credentials.maybe_enable_failed", error=str(exc)[:200])

    return _to_response(created[0])


@router.post("/{cred_id}/reset", response_model=CredentialResponse)
async def reset_credential_cooldown(
    cred_id: str,
    org_id: str = Depends(current_org_id),
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped),
) -> CredentialResponse:
    """Manually clear a credential's rate-limit cooldown.

    The router sets `exhausted_until` when a provider returns a 429. The
    cooldown is a courtesy — it prevents hammering a rate-limited key.
    If the user knows the limit has cleared, they can reset it here. If
    the provider still 429s, the router will mark it exhausted again.
    """
    ok = await clear_exhausted(session, org_id, cred_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Credential not found")

    # Fetch the (now updated) credential for the response
    items = await list_credentials(session, org_id, include_inactive=True)
    match = next((c for c in items if c["id"] == cred_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Credential not found")
    log.info("credentials.manual_reset", cred_id=cred_id[:8], org_id=org_id[:8])
    return _to_response(match)


@router.post("/support-request", status_code=201)
async def create_support_request(
    body: SupportRequest,
    org_id: str = Depends(current_org_id),
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_scoped),
) -> dict:
    """Log a user's support request as a notification for the org owner."""
    await session.execute(sql_text("""
        INSERT INTO notifications (org_id, user_id, kind, title, body)
        VALUES (:oid, :uid, 'support_request',
                'Support request — API key', :body)
    """), {"oid": org_id, "uid": user_id, "body": body.message})
    await session.commit()
    return {"status": "ok"}


@router.delete("/{cred_id}", status_code=204, response_class=Response)
async def remove_credential(
    cred_id: str,
    org_id: str = Depends(current_org_id),
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped),
) -> Response:
    """Delete a credential. Owner/admin only."""
    ok = await delete_credential(session, org_id, cred_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Credential not found")
    return Response(status_code=204)


@router.patch("/{cred_id}", response_model=CredentialResponse)
async def update_credential(
    cred_id: str,
    body: ToggleCredentialRequest,
    org_id: str = Depends(current_org_id),
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped),
) -> CredentialResponse:
    """Toggle active on/off. Owner/admin only."""
    ok = await toggle_credential(session, org_id, cred_id, body.is_active)
    if not ok:
        raise HTTPException(status_code=404, detail="Credential not found")

    # Fetch fresh
    items = await list_credentials(session, org_id, include_inactive=True)
    for c in items:
        if c["id"] == cred_id:
            return _to_response(c)
    raise HTTPException(status_code=404, detail="Credential not found")