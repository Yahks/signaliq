"""
SignalIQ — Phase S.5.3: Credential API endpoints.

Ships:
  GET    /api/settings/credentials
  POST   /api/settings/credentials
  DELETE /api/settings/credentials/{id}
  PATCH  /api/settings/credentials/{id}

All owner/admin-only for writes. All list responses mask the key.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "apps" / "api" / "schemas" / "credentials.py"
ROUTER = ROOT / "apps" / "api" / "routers" / "credentials.py"
MAIN = ROOT / "apps" / "api" / "main.py"


def _patch(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"❌ {path.name} not found")
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(f"\n❌ {path.name}: pattern not found for '{label}'")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


SCHEMAS_SRC = '''"""Credential API schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CredentialResponse(BaseModel):
    id: str
    provider: str
    model_name: str
    role: str
    label: str
    key_preview: str
    is_active: bool
    priority: int
    exhausted_until: datetime | None = None
    last_success_at: datetime | None = None
    created_at: datetime


class CredentialListResponse(BaseModel):
    items: list[CredentialResponse]


class CreateCredentialRequest(BaseModel):
    provider: str = Field(..., min_length=2, max_length=32)
    model_name: str = Field(..., min_length=2, max_length=128)
    role: str = Field(..., min_length=4, max_length=16)
    label: str = Field(..., min_length=2, max_length=64)
    api_key: str = Field(..., min_length=8, max_length=512)
    priority: int = Field(default=100, ge=1, le=1000)


class ToggleCredentialRequest(BaseModel):
    is_active: bool
'''

ROUTER_SRC = '''"""Credential management endpoints.

Users add their own LLM provider keys here. Every org has its own
credentials, encrypted at rest with Fernet (key derived from SECRET_KEY).

Writes are owner/admin-only. The plaintext key is never returned.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, require_role
from apps.api.schemas.credentials import (
    CreateCredentialRequest,
    CredentialListResponse,
    CredentialResponse,
    ToggleCredentialRequest,
)
from packages.core.credentials import (
    add_credential,
    delete_credential,
    list_credentials,
    toggle_credential,
)
from packages.db.session import get_session
from packages.db.tenant import set_current_org

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
        role=c["role"], label=c["label"], key_preview=c["key_preview"],
        is_active=c["is_active"], priority=c["priority"],
        exhausted_until=c.get("exhausted_until"),
        last_success_at=c.get("last_success_at"),
        created_at=c["created_at"],
    )


@router.get("", response_model=CredentialListResponse)
async def list_my_credentials(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped),
) -> CredentialListResponse:
    """List this org's credentials. Keys are masked."""
    items = await list_credentials(session, org_id, include_inactive=True)
    return CredentialListResponse(items=[_to_response(c) for c in items])


@router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(
    body: CreateCredentialRequest,
    org_id: str = Depends(current_org_id),
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped),
) -> CredentialResponse:
    """Add a new credential. Owner/admin only."""
    try:
        cred = await add_credential(
            session, org_id,
            provider=body.provider,
            model_name=body.model_name,
            role=body.role,
            label=body.label,
            api_key=body.api_key,
            priority=body.priority,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        msg = str(exc)[:200]
        if "uq_ai_credentials_org_label" in msg or "unique" in msg.lower():
            raise HTTPException(
                status_code=409,
                detail=f"A credential labeled '{body.label}' already exists",
            ) from exc
        raise HTTPException(status_code=500, detail="Failed to add credential") from exc

    return _to_response(cred)


@router.delete("/{cred_id}", status_code=204)
async def remove_credential(
    cred_id: str,
    org_id: str = Depends(current_org_id),
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped),
) -> None:
    """Delete a credential. Owner/admin only."""
    ok = await delete_credential(session, org_id, cred_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Credential not found")


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
'''


def step1_schemas() -> None:
    print("\n[1/4] Write credential schemas")
    SCHEMAS.parent.mkdir(parents=True, exist_ok=True)
    SCHEMAS.write_text(SCHEMAS_SRC)
    print(f"  ✓ wrote {SCHEMAS.relative_to(ROOT)}")


def step2_router() -> None:
    print("\n[2/4] Write credential router")
    ROUTER.write_text(ROUTER_SRC)
    print(f"  ✓ wrote {ROUTER.relative_to(ROOT)}")


def step3_register() -> None:
    print("\n[3/4] Register router in main.py")
    src = MAIN.read_text()
    if "credentials as credentials_router" in src:
        print("  ↷ already registered")
        return

    _patch(
        MAIN,
        "from apps.api.routers import settings as settings_router",
        "from apps.api.routers import settings as settings_router\n"
        "from apps.api.routers import credentials as credentials_router",
        "import",
    )
    _patch(
        MAIN,
        "app.include_router(settings_router.router)",
        "app.include_router(settings_router.router)\n"
        "app.include_router(credentials_router.router)",
        "include",
    )


def step4_verify() -> bool:
    print("\n[4/4] Restart API + smoke test")

    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)
    with Path("/tmp/signaliq-api.log").open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(5)

    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout} — see /tmp/signaliq-api.log")
        return False
    print("  ✓ API /health 200")

    # Smoke test all four endpoints
    r = subprocess.run([
        "bash", "-c",
        "TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login "
        "-H 'Content-Type: application/json' "
        "-d '{\"identifier\":\"demo\",\"password\":\"DemoPass123!\"}' "
        "| python -c \"import sys,json; print(json.load(sys.stdin)['access_token'])\") && "
        "echo '--- GET list (empty) ---' && "
        "curl -s http://localhost:8000/api/settings/credentials "
        "-H \"Authorization: Bearer $TOKEN\" && echo '' && "
        "echo '--- POST create ---' && "
        "curl -s -X POST http://localhost:8000/api/settings/credentials "
        "-H \"Authorization: Bearer $TOKEN\" "
        "-H 'Content-Type: application/json' "
        "-d '{\"provider\":\"groq\",\"model_name\":\"openai/gpt-oss-120b\","
        "\"role\":\"reasoning\",\"label\":\"test-from-api\","
        "\"api_key\":\"gsk_test_abcdef1234\"}' && echo '' && "
        "echo '--- GET list (1) ---' && "
        "curl -s http://localhost:8000/api/settings/credentials "
        "-H \"Authorization: Bearer $TOKEN\" && echo '' && "
        "echo '--- DELETE ---' && "
        "CRED_ID=$(curl -s http://localhost:8000/api/settings/credentials "
        "-H \"Authorization: Bearer $TOKEN\" "
        "| python -c \"import sys,json; print(json.load(sys.stdin)['items'][0]['id'])\") && "
        "curl -s -X DELETE http://localhost:8000/api/settings/credentials/$CRED_ID "
        "-H \"Authorization: Bearer $TOKEN\" -w 'HTTP %{http_code}\\n'"
    ], capture_output=True, text=True, timeout=30)
    print(r.stdout)
    return '"label":"test-from-api"' in r.stdout and "HTTP 204" in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.5.3: Credential API endpoints")
    print("=" * 70)

    step1_schemas()
    step2_router()
    step3_register()
    ok = step4_verify()

    print("=" * 70)
    if ok:
        print("✅ S.5.3 complete — credential CRUD endpoints live")
        print()
        print("Endpoints:")
        print("  GET    /api/settings/credentials")
        print("  POST   /api/settings/credentials")
        print("  DELETE /api/settings/credentials/{id}")
        print("  PATCH  /api/settings/credentials/{id}")
        print()
        print("Next: S.5.4 — BYOK UI (post-signup prompt + settings page).")
        sys.exit(0)
    print("❌ S.5.3 failed — see output")
    sys.exit(1)


if __name__ == "__main__":
    main()
