"""Broker credentials — read/write for the broker_credentials table.

Encryption reuses the same Fernet helpers as ai_credentials
(packages/core/credentials.py). One row per (org, broker) —
enforced by a unique constraint in the schema.

The plaintext api_key/api_secret never leave this module.
Callers get a dict with the decrypted credentials when they
need to call a broker API.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.credentials import (
    decrypt_key,
    encrypt_key,
    mask_key,
    _key_fingerprint,
)
from packages.core.logging import get_logger

log = get_logger(__name__)


async def save_broker_credentials(
    session: AsyncSession,
    org_id: str,
    *,
    broker: str,
    label: str,
    api_key: str,
    api_secret: str,
    paper: bool = True,
) -> dict[str, Any]:
    """Insert or update the org's credentials for a broker.

    Idempotent on (org_id, broker). Re-saving the same keys
    refreshes the encrypted values and clears last_sync_error.
    Returns metadata with key previews (no plaintext).
    """
    broker = broker.strip().lower()
    if broker not in ("alpaca", "ibkr", "tradier"):
        raise ValueError(f"Unsupported broker: {broker}")
    label = (label or broker).strip()[:64]
    api_key = api_key.strip()
    api_secret = api_secret.strip()
    if len(api_key) < 8:
        raise ValueError("API key looks too short")
    if len(api_secret) < 8:
        raise ValueError("API secret looks too short")

    enc_key = encrypt_key(api_key)
    enc_secret = encrypt_key(api_secret)
    fp = _key_fingerprint(api_key)

    row = (await session.execute(sql_text("""
        INSERT INTO broker_credentials
            (org_id, broker, label, api_key_enc, api_secret_enc,
             paper, monitor_enabled, key_fingerprint)
        VALUES
            (:oid, :broker, :label, :key_enc, :secret_enc,
             :paper, true, :fp)
        ON CONFLICT (org_id, broker) DO UPDATE
        SET label           = EXCLUDED.label,
            api_key_enc     = EXCLUDED.api_key_enc,
            api_secret_enc  = EXCLUDED.api_secret_enc,
            paper           = EXCLUDED.paper,
            key_fingerprint = EXCLUDED.key_fingerprint,
            last_sync_error = NULL,
            updated_at      = now()
        RETURNING id::text, monitor_enabled, created_at, updated_at
    """), {
        "oid": org_id, "broker": broker, "label": label,
        "key_enc": enc_key, "secret_enc": enc_secret,
        "paper": bool(paper), "fp": fp,
    })).first()

    await session.commit()
    log.info("broker_credentials.saved",
             org_id=org_id[:8], broker=broker, paper=paper)
    return {
        "id": row[0],
        "broker": broker,
        "label": label,
        "paper": bool(paper),
        "monitor_enabled": bool(row[1]),
        "key_preview": mask_key(api_key),
        "created_at": row[2],
        "updated_at": row[3],
    }


async def load_broker_credentials(
    session: AsyncSession, org_id: str, broker: str,
) -> dict[str, Any] | None:
    """Return the decrypted credentials for (org, broker), or None.

    Includes monitor_enabled so the caller can skip the fetch if
    monitoring is off. Callers that need the plaintext use
    api_key / api_secret. Callers that only need to display status
    use key_preview.
    """
    broker = broker.strip().lower()
    row = (await session.execute(sql_text("""
        SELECT id::text, label, api_key_enc, api_secret_enc,
               paper, monitor_enabled, last_sync_at, last_sync_error,
               key_fingerprint
        FROM broker_credentials
        WHERE org_id = :oid AND broker = :broker
        LIMIT 1
    """), {"oid": org_id, "broker": broker})).first()
    if not row:
        return None

    try:
        api_key = decrypt_key(row[2])
        api_secret = decrypt_key(row[3])
    except Exception as exc:
        log.warning("broker_credentials.decrypt_failed",
                    org_id=org_id[:8], broker=broker,
                    error=str(exc)[:120])
        return None

    return {
        "id": row[0],
        "broker": broker,
        "label": row[1],
        "api_key": api_key,
        "api_secret": api_secret,
        "paper": bool(row[4]),
        "monitor_enabled": bool(row[5]),
        "last_sync_at": row[6],
        "last_sync_error": row[7],
        "key_preview": mask_key(api_key),
        "key_fingerprint": row[8],
    }


async def delete_broker_credentials(
    session: AsyncSession, org_id: str, broker: str,
) -> bool:
    """Remove the org's credentials for a broker. Returns True if removed."""
    broker = broker.strip().lower()
    r = await session.execute(sql_text("""
        DELETE FROM broker_credentials
        WHERE org_id = :oid AND broker = :broker
    """), {"oid": org_id, "broker": broker})
    await session.commit()
    if (r.rowcount or 0) > 0:
        log.info("broker_credentials.deleted",
                 org_id=org_id[:8], broker=broker)
        return True
    return False


async def update_sync_status(
    session: AsyncSession,
    org_id: str,
    broker: str,
    *,
    error: str | None = None,
) -> None:
    """Record a successful sync (error=None) or the last error.

    Called after each fetch attempt. Sets last_sync_at on success
    and clears last_sync_error. Sets last_sync_error on failure
    and leaves last_sync_at unchanged.
    """
    broker = broker.strip().lower()
    if error is None:
        await session.execute(sql_text("""
            UPDATE broker_credentials
            SET last_sync_at = now(),
                last_sync_error = NULL,
                updated_at = now()
            WHERE org_id = :oid AND broker = :broker
        """), {"oid": org_id, "broker": broker})
    else:
        await session.execute(sql_text("""
            UPDATE broker_credentials
            SET last_sync_error = :err,
                updated_at = now()
            WHERE org_id = :oid AND broker = :broker
        """), {"oid": org_id, "broker": broker, "err": error[:500]})
    await session.commit()


async def set_monitor_enabled(
    session: AsyncSession, org_id: str, broker: str, enabled: bool,
) -> bool:
    """Toggle the monitor_enabled flag. Returns True if a row was updated."""
    broker = broker.strip().lower()
    r = await session.execute(sql_text("""
        UPDATE broker_credentials
        SET monitor_enabled = :enabled, updated_at = now()
        WHERE org_id = :oid AND broker = :broker
    """), {"oid": org_id, "broker": broker, "enabled": bool(enabled)})
    await session.commit()
    return (r.rowcount or 0) > 0


async def list_broker_credentials(
    session: AsyncSession, org_id: str,
) -> list[dict[str, Any]]:
    """List all broker credentials for an org. No plaintext keys."""
    rows = (await session.execute(sql_text("""
        SELECT id::text, broker, label, paper, monitor_enabled,
               last_sync_at, last_sync_error, created_at, updated_at
        FROM broker_credentials
        WHERE org_id = :oid
        ORDER BY created_at ASC
    """), {"oid": org_id})).all()
    return [
        {
            "id": r[0],
            "broker": r[1],
            "label": r[2],
            "paper": bool(r[3]),
            "monitor_enabled": bool(r[4]),
            "last_sync_at": r[5],
            "last_sync_error": r[6],
            "created_at": r[7],
            "updated_at": r[8],
        }
        for r in rows
    ]
