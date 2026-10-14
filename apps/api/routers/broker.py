"""Broker API — connect, status, disconnect, account, positions,
orders, fills, settings.

The router decrypts the org's credentials and calls the broker
client (packages/trading/brokers). It never returns the plaintext
key — only the masked preview.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id
from apps.api.schemas.broker import (
    BrokerAccountResponse,
    BrokerConnectRequest,
    BrokerCredentialResponse,
    BrokerFillResponse,
    BrokerOrderRequest,
    BrokerOrderResponse,
    BrokerPositionResponse,
    BrokerSettingsRequest,
    BrokerStatusResponse,
)
from packages.core.logging import get_logger
from packages.db.session import get_session
from packages.db.tenant import set_current_org
from packages.trading.brokers import get_broker_client, supported_brokers
from packages.trading.brokers.credentials import (
    delete_broker_credentials,
    list_broker_credentials,
    load_broker_credentials,
    save_broker_credentials,
    set_monitor_enabled,
    update_sync_status,
)

log = get_logger(__name__)

router = APIRouter(prefix="/api/broker", tags=["broker"])


async def _scoped(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    """A DB session with app.current_org_id already set."""
    await set_current_org(session, org_id)
    return session


# ─────────────────────────────────────────────────────────────────────
# Connect / status / disconnect
# ─────────────────────────────────────────────────────────────────────

@router.post("/connect", response_model=BrokerCredentialResponse)
async def connect(
    payload: BrokerConnectRequest,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    """Verify the broker keys with a live call, then save them.

    Verification calls fetch_account with the candidate keys.
    If it fails, returns 400 with the broker's error message.
    Only verified keys are stored.
    """
    broker = payload.broker.strip().lower()
    if broker not in supported_brokers():
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported broker: {broker}. "
                   f"Supported: {', '.join(supported_brokers())}",
        )

    client = get_broker_client(broker)
    try:
        acct = await client.fetch_account(
            payload.api_key, payload.api_secret, paper=payload.paper,
        )
    except Exception as exc:
        msg = str(exc)[:300]
        log.warning("broker.connect.verify_failed",
                    broker=broker, org_id=org_id[:8], error=msg)
        raise HTTPException(status_code=400, detail={
            "reason": "broker_verification_failed",
            "message": f"The broker rejected these keys: {msg}",
        })

    saved = await save_broker_credentials(
        session, org_id,
        broker=broker,
        label=payload.label or f"{broker} account",
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        paper=payload.paper,
    )
    log.info("broker.connect.ok", broker=broker, org_id=org_id[:8],
             status=acct.get("status"))
    return BrokerCredentialResponse(**saved)


@router.get("/status", response_model=BrokerStatusResponse)
async def status(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    """Return the org's connection status. Connected if any row exists."""
    rows = await list_broker_credentials(session, org_id)
    if not rows:
        return BrokerStatusResponse(connected=False)
    r = rows[0]
    loaded = await load_broker_credentials(session, org_id, r["broker"])
    return BrokerStatusResponse(
        connected=True,
        broker=r["broker"],
        label=r["label"],
        paper=r["paper"],
        monitor_enabled=r["monitor_enabled"],
        key_preview=(loaded or {}).get("key_preview"),
        last_sync_at=r["last_sync_at"],
        last_sync_error=r["last_sync_error"],
    )


@router.delete("/disconnect")
async def disconnect(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> dict[str, bool]:
    """Remove the org's broker credentials. Idempotent."""
    rows = await list_broker_credentials(session, org_id)
    removed = False
    for r in rows:
        if await delete_broker_credentials(session, org_id, r["broker"]):
            removed = True
    return {"removed": removed}


# ─────────────────────────────────────────────────────────────────────
# Account / positions / orders / fills
# ─────────────────────────────────────────────────────────────────────

async def _get_creds_or_404(
    session: AsyncSession, org_id: str,
) -> dict[str, Any]:
    """Return decrypted creds for the org's first connected broker."""
    rows = await list_broker_credentials(session, org_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No broker connected")
    broker = rows[0]["broker"]
    creds = await load_broker_credentials(session, org_id, broker)
    if creds is None:
        raise HTTPException(
            status_code=500,
            detail="Credentials exist but could not be decrypted",
        )
    return creds


@router.get("/account", response_model=BrokerAccountResponse)
async def account(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    creds = await _get_creds_or_404(session, org_id)
    client = get_broker_client(creds["broker"])
    try:
        data = await client.fetch_account(
            creds["api_key"], creds["api_secret"], paper=creds["paper"],
        )
        await update_sync_status(session, org_id, creds["broker"], error=None)
    except Exception as exc:
        await update_sync_status(
            session, org_id, creds["broker"], error=str(exc)[:500],
        )
        raise HTTPException(status_code=502, detail={
            "reason": "broker_unreachable",
            "message": str(exc)[:300],
        })
    return BrokerAccountResponse(**data)


@router.get("/positions", response_model=list[BrokerPositionResponse])
async def positions(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    creds = await _get_creds_or_404(session, org_id)
    client = get_broker_client(creds["broker"])
    try:
        rows = await client.fetch_positions(
            creds["api_key"], creds["api_secret"], paper=creds["paper"],
        )
        await update_sync_status(session, org_id, creds["broker"], error=None)
    except Exception as exc:
        await update_sync_status(
            session, org_id, creds["broker"], error=str(exc)[:500],
        )
        raise HTTPException(status_code=502, detail={
            "reason": "broker_unreachable",
            "message": str(exc)[:300],
        })
    return [BrokerPositionResponse(**p) for p in rows]


@router.get("/orders", response_model=list[BrokerOrderResponse])
async def orders(
    limit: int = 50,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    creds = await _get_creds_or_404(session, org_id)
    client = get_broker_client(creds["broker"])
    try:
        rows = await client.fetch_orders(
            creds["api_key"], creds["api_secret"],
            paper=creds["paper"], limit=limit,
        )
        await update_sync_status(session, org_id, creds["broker"], error=None)
    except Exception as exc:
        await update_sync_status(
            session, org_id, creds["broker"], error=str(exc)[:500],
        )
        raise HTTPException(status_code=502, detail={
            "reason": "broker_unreachable",
            "message": str(exc)[:300],
        })
    return [BrokerOrderResponse(**o) for o in rows]


@router.get("/fills", response_model=list[BrokerFillResponse])
async def fills(
    day: str | None = None,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    creds = await _get_creds_or_404(session, org_id)
    client = get_broker_client(creds["broker"])
    try:
        rows = await client.fetch_fills(
            creds["api_key"], creds["api_secret"],
            paper=creds["paper"], day=day,
        )
        await update_sync_status(session, org_id, creds["broker"], error=None)
    except Exception as exc:
        await update_sync_status(
            session, org_id, creds["broker"], error=str(exc)[:500],
        )
        raise HTTPException(status_code=502, detail={
            "reason": "broker_unreachable",
            "message": str(exc)[:300],
        })
    return [BrokerFillResponse(**f) for f in rows]


# ─────────────────────────────────────────────────────────────────────
# Place order — paper only
# ─────────────────────────────────────────────────────────────────────

@router.post("/orders", response_model=BrokerOrderResponse)
async def place_order(
    payload: BrokerOrderRequest,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    """Place a market or limit order.

    Refuses when the connected account is live. This is the
    paper-account tab; real-money orders are not allowed here.
    """
    creds = await _get_creds_or_404(session, org_id)
    if not creds["paper"]:
        raise HTTPException(status_code=400, detail={
            "reason": "live_account",
            "message": "This tab places paper orders only. "
                       "Reconnect with a paper account.",
        })

    client = get_broker_client(creds["broker"])
    try:
        raw = await client.place_order(
            creds["api_key"], creds["api_secret"],
            paper=True,
            symbol=payload.symbol,
            side=payload.side,
            qty=payload.qty,
            type=payload.type,
            limit_price=payload.limit_price,
            time_in_force=payload.time_in_force,
        )
        await update_sync_status(session, org_id, creds["broker"], error=None)
    except Exception as exc:
        await update_sync_status(
            session, org_id, creds["broker"], error=str(exc)[:500],
        )
        raise HTTPException(status_code=502, detail={
            "reason": "broker_rejected",
            "message": str(exc)[:300],
        })

    return BrokerOrderResponse(
        id=raw.get("id") or "",
        symbol=raw.get("symbol") or "",
        side=raw.get("side") or "",
        qty=raw.get("qty") or "0",
        filled_qty=raw.get("filled_qty") or "0",
        type=raw.get("type") or "market",
        status=raw.get("status") or "",
        submitted_at=raw.get("submitted_at"),
        filled_avg_price=(
            float(raw["filled_avg_price"])
            if raw.get("filled_avg_price") else None
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# Settings — toggle monitor_enabled
# ─────────────────────────────────────────────────────────────────────

@router.patch("/settings", response_model=BrokerStatusResponse)
async def update_settings(
    payload: BrokerSettingsRequest,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    rows = await list_broker_credentials(session, org_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No broker connected")
    broker = rows[0]["broker"]
    ok = await set_monitor_enabled(
        session, org_id, broker, payload.monitor_enabled,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Broker not found")
    return await status(session=session, org_id=org_id)
