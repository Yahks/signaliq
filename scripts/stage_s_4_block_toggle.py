"""
SignalIQ — Phase S.4: Per-block auto toggle.

Ships:
  1. Fix: dashboard "Getting started" $0.00 → computed from balance
  2. Migration 0035: org_block_settings table
  3. API: GET /api/settings/blocks + PATCH /api/settings/blocks/{slug}
  4. Celery: skip generation when block auto is off
  5. UI: Block automation card on the dashboard
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "alembic" / "versions" / "0035_org_block_settings.py"
SETTINGS_ROUTER = ROOT / "apps" / "api" / "routers" / "settings.py"
SCHEMAS = ROOT / "apps" / "api" / "schemas" / "settings.py"
MAIN = ROOT / "apps" / "api" / "main.py"
TRADING_TASK = ROOT / "apps" / "worker" / "tasks" / "trading.py"
DASHBOARD = ROOT / "apps" / "web" / "src" / "app" / "(app)" / "dashboard" / "page.tsx"
API_TS = ROOT / "apps" / "web" / "src" / "lib" / "api.ts"


def _patch(path: Path, old: str, new: str, label: str) -> None:
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


# ─────────────────────────────────────────────────────────────────────
# 1. Fix the Getting started card USD
# ─────────────────────────────────────────────────────────────────────

def step1_fix_dashboard_usd() -> None:
    print("\n[1/6] Fix 'Getting started' card USD computation")

    src = DASHBOARD.read_text()
    old = '{fmtUSD(tokens.data?.balance_usd ?? 0)}'
    new = '{fmtUSD(tokens.data?.balance_usd ?? ((tokens.data?.balance ?? 0) / 1_000_000))}'

    if new in src:
        print("  ↷ already fixed")
        return
    if old not in src:
        print("  ⚠ pattern not found — skipping")
        return
    DASHBOARD.write_text(src.replace(old, new, 1))
    print("  ✓ dashboard: USD fallback in Getting started")


# ─────────────────────────────────────────────────────────────────────
# 2. Migration 0035
# ─────────────────────────────────────────────────────────────────────

MIGRATION_SRC = '''"""Per-block auto toggle.

Each org can turn auto-generation on or off per block. When off,
Celery generator tasks for that block skip silently. Existing data
remains visible; only new generation is paused.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_block_settings",
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("block_slug", sa.String(64), nullable=False),
        sa.Column("auto_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("org_id", "block_slug", name="pk_org_block_settings"),
    )

    op.execute("ALTER TABLE org_block_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_block_settings FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_org_block_settings ON org_block_settings
            USING (
                NULLIF(current_setting('app.current_org_id', true), '') IS NULL
                OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
    """)

    # Backfill: every existing org gets toggles for 'trading' and 'news'
    op.execute("""
        INSERT INTO org_block_settings (org_id, block_slug, auto_enabled)
        SELECT o.id, b.slug, true
        FROM orgs o
        CROSS JOIN (VALUES ('trading'), ('news')) AS b(slug)
        ON CONFLICT (org_id, block_slug) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_org_block_settings ON org_block_settings")
    op.drop_table("org_block_settings")
'''


def step2_migration() -> None:
    print("\n[2/6] Write migration 0035")
    if MIGRATION.exists():
        print("  ↷ migration already exists")
        return
    MIGRATION.write_text(MIGRATION_SRC)
    print("  ✓ wrote 0035_org_block_settings.py")


# ─────────────────────────────────────────────────────────────────────
# 3. Schemas + router
# ─────────────────────────────────────────────────────────────────────

SCHEMAS_SRC = '''"""Settings API schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BlockSettingsItem(BaseModel):
    slug: str
    name: str
    auto_enabled: bool
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class BlockSettingsListResponse(BaseModel):
    items: list[BlockSettingsItem]


class UpdateBlockSettingsRequest(BaseModel):
    auto_enabled: bool
'''

ROUTER_SRC = '''"""Settings endpoints — per-block automation toggles."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, require_role
from apps.api.schemas.settings import (
    BlockSettingsItem,
    BlockSettingsListResponse,
    UpdateBlockSettingsRequest,
)
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Static registry of blocks we know about. Extend when new blocks land.
KNOWN_BLOCKS = [
    {"slug": "trading", "name": "Trading"},
    {"slug": "news",    "name": "News"},
]


async def _scoped(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.get("/blocks", response_model=BlockSettingsListResponse)
async def list_block_settings(
    session: AsyncSession = Depends(_scoped),
) -> BlockSettingsListResponse:
    """Current auto_enabled state for every known block."""
    rows = (await session.execute(sql_text("""
        SELECT block_slug, auto_enabled, updated_at
        FROM org_block_settings
    """))).all()
    by_slug = {r[0]: r for r in rows}

    items = []
    for b in KNOWN_BLOCKS:
        r = by_slug.get(b["slug"])
        items.append(BlockSettingsItem(
            slug=b["slug"],
            name=b["name"],
            auto_enabled=bool(r[1]) if r else True,
            updated_at=r[2] if r else None,
        ))
    return BlockSettingsListResponse(items=items)


@router.patch("/blocks/{slug}", response_model=BlockSettingsItem)
async def update_block_settings(
    slug: str,
    body: UpdateBlockSettingsRequest,
    org_id: str = Depends(current_org_id),
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped),
) -> BlockSettingsItem:
    """Flip auto-generation for one block. Owner/admin only."""
    from fastapi import HTTPException
    known = {b["slug"]: b["name"] for b in KNOWN_BLOCKS}
    if slug not in known:
        raise HTTPException(status_code=404, detail=f"Unknown block: {slug}")

    await session.execute(sql_text("""
        INSERT INTO org_block_settings (org_id, block_slug, auto_enabled)
        VALUES (:oid, :slug, :enabled)
        ON CONFLICT (org_id, block_slug) DO UPDATE
        SET auto_enabled = EXCLUDED.auto_enabled,
            updated_at = now()
    """), {"oid": org_id, "slug": slug, "enabled": body.auto_enabled})

    row = (await session.execute(sql_text("""
        SELECT block_slug, auto_enabled, updated_at
        FROM org_block_settings
        WHERE org_id = :oid AND block_slug = :slug
    """), {"oid": org_id, "slug": slug})).first()

    await session.commit()

    return BlockSettingsItem(
        slug=row[0], name=known[slug],
        auto_enabled=bool(row[1]), updated_at=row[2],
    )
'''


def step3_router() -> None:
    print("\n[3/6] Write settings router + schemas")
    SCHEMAS.parent.mkdir(parents=True, exist_ok=True)
    if not SCHEMAS.exists():
        SCHEMAS.write_text(SCHEMAS_SRC)
        print("  ✓ wrote apps/api/schemas/settings.py")
    else:
        print("  ↷ schemas already exist")
    if not SETTINGS_ROUTER.exists():
        SETTINGS_ROUTER.write_text(ROUTER_SRC)
        print("  ✓ wrote apps/api/routers/settings.py")
    else:
        print("  ↷ router already exists")

    # Register in main.py
    src = MAIN.read_text()
    if "settings_router" not in src:
        _patch(
            MAIN,
            "from apps.api.routers import trading as trading_router",
            "from apps.api.routers import trading as trading_router\n"
            "from apps.api.routers import settings as settings_router",
            "import",
        )
        _patch(
            MAIN,
            "app.include_router(trading_router.router)",
            "app.include_router(trading_router.router)\n"
            "app.include_router(settings_router.router)",
            "include",
        )


# ─────────────────────────────────────────────────────────────────────
# 4. Celery — skip when auto is off
# ─────────────────────────────────────────────────────────────────────

def step4_celery_gate() -> None:
    print("\n[4/6] Celery — skip generation when block auto is off")

    _patch(
        TRADING_TASK,
        '''        # Exhaustion gate — skip silently if the owner is out of tokens.
        if owner_id:
            from packages.core.tokens import can_generate
            if not await can_generate(session, owner_id):
                log.info("trading.generation_skipped_no_tokens",
                         org_id=org_id, slug=slug, user_id=owner_id[:8])
                return {"status": "skipped_no_tokens", "org_id": org_id, "slug": slug}''',
        '''        # Exhaustion gate — skip silently if the owner is out of tokens.
        if owner_id:
            from packages.core.tokens import can_generate
            if not await can_generate(session, owner_id):
                log.info("trading.generation_skipped_no_tokens",
                         org_id=org_id, slug=slug, user_id=owner_id[:8])
                return {"status": "skipped_no_tokens", "org_id": org_id, "slug": slug}

        # Auto-toggle gate — skip when the trading block is disabled for this org.
        auto_row = (await session.execute(sql_text("""
            SELECT auto_enabled FROM org_block_settings
            WHERE org_id = :oid AND block_slug = 'trading'
        """), {"oid": org_id})).first()
        # Missing row → treat as enabled (fail-open)
        if auto_row and not auto_row[0]:
            log.info("trading.generation_skipped_auto_disabled",
                     org_id=org_id, slug=slug)
            return {"status": "skipped_auto_disabled", "org_id": org_id, "slug": slug}''',
        "auto-disabled gate",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. API + UI — frontend integration
# ─────────────────────────────────────────────────────────────────────

def step5_frontend() -> None:
    print("\n[5/6] Frontend — types + method + dashboard card")

    src = API_TS.read_text()
    if "BlockSettingsItem" not in src:
        types = '''

// ---------- Block settings (S.4) ----------

export type BlockSettingsItem = {
  slug: string;
  name: string;
  auto_enabled: boolean;
  updated_at: string | null;
};

export type BlockSettingsListResponse = {
  items: BlockSettingsItem[];
};
'''
        marker = "export const api = {"
        if marker not in src:
            marker = "export const api:"
        src = src.replace(marker, types + "\n" + marker, 1)
        API_TS.write_text(src)
        print("  ✓ api.ts: BlockSettings types added")

    src = API_TS.read_text()
    if "listBlocks" not in src:
        marker = '  getTokenBalance: () =>'
        src = src.replace(
            marker,
            '  listBlocks: () =>\n'
            '    request<BlockSettingsListResponse>("/api/settings/blocks"),\n'
            '  updateBlock: (slug: string, autoEnabled: boolean) =>\n'
            '    request<BlockSettingsItem>(`/api/settings/blocks/${slug}`, {\n'
            '      method: "PATCH",\n'
            '      body: { auto_enabled: autoEnabled },\n'
            '    }),\n\n'
            + marker,
            1,
        )
        API_TS.write_text(src)
        print("  ✓ api.ts: listBlocks + updateBlock")

    # Add card to dashboard
    dash = DASHBOARD.read_text()
    if "Block automation" in dash:
        print("  ↷ Block automation card already present")
        return

    # Add the query
    if '"block-settings"' not in dash:
        _patch(
            DASHBOARD,
            '''  const tokens = useQuery({
    queryKey: ["token-balance"],
    queryFn: () => api.getTokenBalance(),
    refetchInterval: 30_000,
  });''',
            '''  const tokens = useQuery({
    queryKey: ["token-balance"],
    queryFn: () => api.getTokenBalance(),
    refetchInterval: 30_000,
  });

  const blocks = useQuery({
    queryKey: ["block-settings"],
    queryFn: () => api.listBlocks(),
    refetchInterval: 60_000,
  });

  const toggleBlock = useMutation({
    mutationFn: ({ slug, enabled }: { slug: string; enabled: boolean }) =>
      api.updateBlock(slug, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["block-settings"] }),
  });''',
            "block-settings query + mutation",
        )

    # Ensure useMutation + useQueryClient imports
    dash = DASHBOARD.read_text()
    if "useMutation" not in dash.split("\n")[4]:
        dash = dash.replace(
            'import { useQuery } from "@tanstack/react-query";',
            'import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";',
            1,
        )
    if "const qc = useQueryClient();" not in dash:
        dash = dash.replace(
            "export default function DashboardPage() {",
            "export default function DashboardPage() {\n  const qc = useQueryClient();",
            1,
        )
    DASHBOARD.write_text(dash)

    # Insert the card before "{/* Entities + Topics */}"
    _patch(
        DASHBOARD,
        "      {/* Entities + Topics */}",
        '''      {/* Block automation */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Block automation</CardTitle>
          <CardDescription>
            Turn off auto-generation to freeze a block. Existing content stays visible.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {blocks.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (blocks.data?.items ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">No blocks configured.</p>
          ) : (
            <div className="space-y-3">
              {(blocks.data?.items ?? []).map((b) => (
                <div key={b.slug} className="flex items-center justify-between gap-3">
                  <div>
                    <p className="font-medium">{b.name}</p>
                    <p className="text-xs text-muted-foreground font-mono">{b.slug}</p>
                  </div>
                  <label className="inline-flex items-center gap-2 cursor-pointer">
                    <span className={`text-xs ${b.auto_enabled ? "text-green-400" : "text-muted-foreground"}`}>
                      {b.auto_enabled ? "auto on" : "auto off"}
                    </span>
                    <input
                      type="checkbox"
                      checked={b.auto_enabled}
                      onChange={(e) => toggleBlock.mutate({ slug: b.slug, enabled: e.target.checked })}
                      className="h-4 w-8 cursor-pointer rounded-full appearance-none bg-muted checked:bg-green-500/70 transition-colors relative before:content-[''] before:absolute before:top-0.5 before:left-0.5 before:h-3 before:w-3 before:rounded-full before:bg-white before:transition-transform checked:before:translate-x-4"
                    />
                  </label>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Entities + Topics */}''',
        "Block automation card",
    )


# ─────────────────────────────────────────────────────────────────────
# 6. Migrate + restart + verify
# ─────────────────────────────────────────────────────────────────────

def step6_migrate_verify() -> bool:
    print("\n[6/6] Migrate + restart + verify")

    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"❌ alembic failed:\n{r.stderr}")
    print("  ✓ alembic upgrade head")

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
        print(f"  ❌ /health = {r.stdout}")
        return False
    print("  ✓ API /health 200")

    # TypeScript check
    r = subprocess.run(
        ["npx", "tsc", "--noEmit"],
        cwd=ROOT / "apps" / "web", capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        print("  ❌ TypeScript errors:")
        for line in r.stdout.splitlines()[:15]:
            print(f"      {line}")
        return False
    print("  ✓ TypeScript clean")

    # Test the API
    r = subprocess.run([
        "bash", "-c",
        "TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login "
        "-H 'Content-Type: application/json' "
        "-d '{\"identifier\":\"demo\",\"password\":\"DemoPass123!\"}' "
        "| python -c \"import sys,json; print(json.load(sys.stdin)['access_token'])\") && "
        "echo '--- GET /blocks ---' && "
        "curl -s http://localhost:8000/api/settings/blocks "
        "-H \"Authorization: Bearer $TOKEN\" && "
        "echo '' && "
        "echo '--- PATCH /blocks/trading ---' && "
        "curl -s -X PATCH http://localhost:8000/api/settings/blocks/trading "
        "-H \"Authorization: Bearer $TOKEN\" "
        "-H 'Content-Type: application/json' "
        "-d '{\"auto_enabled\": false}' && "
        "echo '' && "
        "echo '--- PATCH back on ---' && "
        "curl -s -X PATCH http://localhost:8000/api/settings/blocks/trading "
        "-H \"Authorization: Bearer $TOKEN\" "
        "-H 'Content-Type: application/json' "
        "-d '{\"auto_enabled\": true}'"
    ], capture_output=True, text=True, timeout=30)
    print(r.stdout)
    return '"slug":"trading"' in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.4: Per-block auto toggle")
    print("=" * 70)

    step1_fix_dashboard_usd()
    step2_migration()
    step3_router()
    step4_celery_gate()
    step5_frontend()
    ok = step6_migrate_verify()

    print("=" * 70)
    if ok:
        print("✅ S.4 complete — per-block auto toggle live")
        print()
        print("Refresh /dashboard: a new 'Block automation' card")
        print("Toggles for Trading + News.")
        sys.exit(0)
    print("❌ S.4 failed — see output")
    sys.exit(1)


if __name__ == "__main__":
    main()
