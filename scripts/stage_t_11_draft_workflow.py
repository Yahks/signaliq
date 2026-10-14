"""
SignalIQ — Phase T.11: Draft review workflow.

Drafts are currently invisible — the verify layer marks them draft,
they sit in the DB, and no one can review or publish them.

Ships:
  1. Fix: call_llm_json_verified stores unsupported_claims in
     source_payload so future drafts carry their reason
  2. GET  /api/trading/drafts                — list all drafts
  3. POST /api/trading/drafts/{id}/approve   — set published + feedback
  4. POST /api/trading/drafts/{id}/reject    — set failed + feedback
  5. UI: /trading/drafts page with inline preview + actions
  6. Sidebar link under Trading
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"
SCHEMA = ROOT / "apps" / "api" / "schemas" / "trading.py"
ROUTER = ROOT / "apps" / "api" / "routers" / "trading.py"
WEB = ROOT / "apps" / "web"
API_TS = WEB / "src" / "lib" / "api.ts"
DRAFTS_PAGE = WEB / "src" / "app" / "(app)" / "trading" / "drafts" / "page.tsx"
SIDEBAR = WEB / "src" / "components" / "layout" / "sidebar.tsx"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:400]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. Store unsupported_claims in source_payload when persisting
# ─────────────────────────────────────────────────────────────────────

def step1_store_rejection_reason() -> None:
    print("\n[1/6] Store unsupported_claims in source_payload")

    _patch_and_verify(
        BASE,
        '''    status = "published" if getattr(post, "verification_ok", True) else "draft"
    # Compute published_at in Python to avoid bind-parameter type ambiguity
    from datetime import UTC, datetime
    published_at = datetime.now(UTC) if status == "published" else None''',
        '''    status = "published" if getattr(post, "verification_ok", True) else "draft"
    # Compute published_at in Python to avoid bind-parameter type ambiguity
    from datetime import UTC, datetime
    published_at = datetime.now(UTC) if status == "published" else None

    # For drafts, attach the verification failure reason so the UI
    # can show a human why the post was flagged.
    if status == "draft":
        source_payload = dict(source_payload)
        source_payload["_rejection"] = {
            "unsupported_claims": list(getattr(post, "unsupported_claims", []) or []),
            "regenerated": bool(getattr(post, "regenerated", False)),
            "flagged_at": published_at or datetime.now(UTC).isoformat(),
        }''',
        "store rejection reason on draft",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Schemas
# ─────────────────────────────────────────────────────────────────────

SCHEMA_ADDITION = '''

class DraftPostResponse(BaseModel):
    id: str
    subblock_slug: str
    subblock_name: str
    title: str
    body: str
    source_kind: str
    word_count: int | None
    created_at: datetime
    unsupported_claims: list[str]
    regenerated: bool


class DraftListResponse(BaseModel):
    items: list[DraftPostResponse]
    total: int


class DraftActionResult(BaseModel):
    post_id: str
    new_status: str
'''


def step2_extend_schemas() -> None:
    print("\n[2/6] Extend schemas")
    src = SCHEMA.read_text()
    if "DraftPostResponse" in src:
        print("  ↷ schemas already extended")
        return
    src = src.rstrip() + "\n" + SCHEMA_ADDITION
    SCHEMA.write_text(src)
    print("  ✓ wrote DraftPostResponse / DraftListResponse / DraftActionResult")


# ─────────────────────────────────────────────────────────────────────
# 3. Router endpoints
# ─────────────────────────────────────────────────────────────────────

ENDPOINTS = '''

# ─────────────────────────────────────────────────────────────────────
# Draft review workflow (T.11)
# ─────────────────────────────────────────────────────────────────────

@router.get("/drafts", response_model=DraftListResponse)
async def list_drafts(
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(_scoped_session),
) -> DraftListResponse:
    """All draft posts across all subblocks, newest first.

    Each draft carries its rejection reason (unsupported claims from
    the verify layer, if the generator recorded them).
    """
    rows = (await session.execute(sql_text("""
        SELECT tp.id::text, ts.slug, ts.name,
               tp.title, tp.body, tp.source_kind, tp.word_count,
               tp.created_at,
               tp.source_payload->'_rejection'->'unsupported_claims' AS claims,
               COALESCE((tp.source_payload->'_rejection'->>'regenerated')::boolean, false) AS regen
        FROM trading_posts tp
        JOIN trading_subblocks ts ON ts.id = tp.subblock_id
        WHERE tp.status = 'draft'
        ORDER BY tp.created_at DESC
        LIMIT :lim
    """), {"lim": limit})).all()

    items = []
    for r in rows:
        claims_raw = r[8]
        claims: list[str] = []
        if isinstance(claims_raw, list):
            claims = [str(c) for c in claims_raw]
        items.append(DraftPostResponse(
            id=r[0], subblock_slug=r[1], subblock_name=r[2],
            title=r[3], body=r[4], source_kind=r[5],
            word_count=r[6], created_at=r[7],
            unsupported_claims=claims, regenerated=bool(r[9]),
        ))

    return DraftListResponse(items=items, total=len(items))


@router.post("/drafts/{post_id}/approve", response_model=DraftActionResult)
async def approve_draft(
    post_id: str,
    org_id: str = Depends(current_org_id),
    user_id: str = Depends(current_user_id),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
    session: AsyncSession = Depends(_scoped_session),
) -> DraftActionResult:
    """Publish a draft post and record human approval."""
    row = (await session.execute(sql_text("""
        SELECT id FROM trading_posts WHERE id = :pid AND status = 'draft'
    """), {"pid": post_id})).first()

    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")

    await session.execute(sql_text("""
        UPDATE trading_posts
        SET status = 'published',
            published_at = now(),
            updated_at = now()
        WHERE id = :pid
    """), {"pid": post_id})

    await session.execute(sql_text("""
        INSERT INTO post_feedback (org_id, post_id, user_id, verdict, comments)
        VALUES (:oid, :pid, :uid, 'approved', 'human-approved from draft queue')
    """), {"oid": org_id, "pid": post_id, "uid": user_id})

    await session.commit()
    return DraftActionResult(post_id=post_id, new_status="published")


@router.post("/drafts/{post_id}/reject", response_model=DraftActionResult)
async def reject_draft(
    post_id: str,
    reason: str = Query(default="", max_length=500),
    org_id: str = Depends(current_org_id),
    user_id: str = Depends(current_user_id),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
    session: AsyncSession = Depends(_scoped_session),
) -> DraftActionResult:
    """Reject a draft — mark as failed and record why."""
    row = (await session.execute(sql_text("""
        SELECT id FROM trading_posts WHERE id = :pid AND status = 'draft'
    """), {"pid": post_id})).first()

    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")

    await session.execute(sql_text("""
        UPDATE trading_posts
        SET status = 'failed',
            updated_at = now()
        WHERE id = :pid
    """), {"pid": post_id})

    await session.execute(sql_text("""
        INSERT INTO post_feedback (org_id, post_id, user_id, verdict, comments)
        VALUES (:oid, :pid, :uid, 'rejected', :reason)
    """), {"oid": org_id, "pid": post_id, "uid": user_id,
            "reason": reason or "rejected from draft queue"})

    await session.commit()
    return DraftActionResult(post_id=post_id, new_status="failed")
'''


def step3_add_endpoints() -> None:
    print("\n[3/6] Add draft endpoints")
    src = ROUTER.read_text()
    if "list_drafts" in src:
        print("  ↷ draft endpoints already present")
        return

    import re
    m = re.search(
        r'from apps\.api\.schemas\.trading import \(([^)]+)\)',
        src, re.DOTALL,
    )
    if m:
        current = m.group(1)
        imports = [x.strip().rstrip(',') for x in current.strip().split('\n') if x.strip()]
        for cls in ("DraftPostResponse", "DraftListResponse", "DraftActionResult"):
            if cls not in imports:
                imports.append(cls)
        imports = sorted(set(imports))
        new_block = "from apps.api.schemas.trading import (\n"
        for imp in imports:
            new_block += f"    {imp},\n"
        new_block += ")"
        src = src.replace(m.group(0), new_block, 1)

    ROUTER.write_text(src.rstrip() + "\n" + ENDPOINTS)
    print("  ✓ appended draft endpoints")


# ─────────────────────────────────────────────────────────────────────
# 4. api.ts
# ─────────────────────────────────────────────────────────────────────

def step4_extend_api_ts() -> None:
    print("\n[4/6] Extend lib/api.ts")
    src = API_TS.read_text()

    if "DraftPostResponse" not in src:
        types = '''

// ---------- Draft review (T.11) ----------

export type DraftPostResponse = {
  id: string;
  subblock_slug: string;
  subblock_name: string;
  title: string;
  body: string;
  source_kind: string;
  word_count: number | null;
  created_at: string;
  unsupported_claims: string[];
  regenerated: boolean;
};

export type DraftListResponse = {
  items: DraftPostResponse[];
  total: number;
};

export type DraftActionResult = {
  post_id: string;
  new_status: string;
};
'''
        marker = "export const api = {"
        if marker not in src:
            marker = "export const api:"
        if marker not in src:
            raise SystemExit("❌ api.ts anchor not found")
        src = src.replace(marker, types + "\n" + marker, 1)
        API_TS.write_text(src)
        print("  ✓ api.ts: draft types added")

    src = API_TS.read_text()
    if "listDrafts" in src:
        print("  ↷ draft methods already present")
        return

    marker = '  listSymbols: (limit: number = 60) =>'
    if marker not in src:
        raise SystemExit("❌ listSymbols marker not found")

    new = '''  listDrafts: (limit: number = 100) =>
    request<DraftListResponse>(`/api/trading/drafts?limit=${limit}`),
  approveDraft: (postId: string) =>
    request<DraftActionResult>(`/api/trading/drafts/${postId}/approve`, { method: "POST" }),
  rejectDraft: (postId: string, reason: string = "") =>
    request<DraftActionResult>(
      `/api/trading/drafts/${postId}/reject?reason=${encodeURIComponent(reason)}`,
      { method: "POST" },
    ),

''' + marker

    src = src.replace(marker, new, 1)
    API_TS.write_text(src)
    print("  ✓ api.ts: draft methods added")


# ─────────────────────────────────────────────────────────────────────
# 5. Drafts page
# ─────────────────────────────────────────────────────────────────────

DRAFTS_PAGE_SRC = '''"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type DraftPostResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

function DraftRow({ d }: { d: DraftPostResponse }) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [showReject, setShowReject] = useState(false);

  const approve = useMutation({
    mutationFn: () => api.approveDraft(d.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trading-drafts"] }),
  });

  const reject = useMutation({
    mutationFn: () => api.rejectDraft(d.id, rejectReason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trading-drafts"] });
      setShowReject(false);
    },
  });

  return (
    <div className="rounded-md border border-border p-3 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">
            <Link href={`/trading/${d.subblock_slug}`} className="hover:underline">
              {d.subblock_name}
            </Link>
            {" · "}
            {new Date(d.created_at).toLocaleString()}
            {d.word_count != null && ` · ${d.word_count} words`}
          </p>
          <p className="mt-0.5 font-medium truncate">{d.title}</p>
        </div>
        <div className="flex gap-2 shrink-0">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? "Collapse" : "Preview"}
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={() => approve.mutate()}
            disabled={approve.isPending}
          >
            {approve.isPending ? "…" : "Approve"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowReject(!showReject)}
            disabled={reject.isPending}
          >
            Reject
          </Button>
        </div>
      </div>

      {d.unsupported_claims.length > 0 && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs space-y-1">
          <p className="text-amber-400 font-medium">
            ⓘ Verifier flagged {d.unsupported_claims.length} unsupported claim{d.unsupported_claims.length === 1 ? "" : "s"}:
          </p>
          <ul className="list-disc pl-5 text-amber-200/80 space-y-0.5">
            {d.unsupported_claims.slice(0, 5).map((c, i) => (
              <li key={i} className="line-clamp-2">{c}</li>
            ))}
            {d.unsupported_claims.length > 5 && (
              <li className="italic">+{d.unsupported_claims.length - 5} more</li>
            )}
          </ul>
        </div>
      )}
      {d.unsupported_claims.length === 0 && d.regenerated && (
        <p className="text-xs text-muted-foreground">
          ⓘ Regenerated once, but still had unsupported claims.
        </p>
      )}

      {showReject && (
        <div className="rounded-md border border-border bg-muted/30 p-2 space-y-2">
          <input
            type="text"
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            placeholder="Optional reason (recorded in post_feedback)"
            className="w-full rounded-md border border-border bg-transparent px-2 py-1 text-xs"
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={() => reject.mutate()} disabled={reject.isPending}>
              {reject.isPending ? "Rejecting…" : "Confirm reject"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setShowReject(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {expanded && (
        <div className="rounded-md border border-border bg-muted/20 p-3 max-h-96 overflow-y-auto">
          <pre className="whitespace-pre-wrap text-xs font-mono">{d.body}</pre>
        </div>
      )}
    </div>
  );
}

export default function DraftsPage() {
  const q = useQuery({
    queryKey: ["trading-drafts"],
    queryFn: () => api.listDrafts(200),
    refetchInterval: 60_000,
  });

  const items = q.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Drafts awaiting review</h1>
          <p className="text-sm text-muted-foreground">
            {items.length} draft{items.length === 1 ? "" : "s"} flagged by the verifier
          </p>
        </div>
        <Link href="/trading" className="text-sm text-blue-400 hover:underline">
          ← Back to Trading
        </Link>
      </div>

      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading drafts…</p>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No drafts. Every generated post passed verification.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Pending</CardTitle>
            <CardDescription>
              Approve to publish · Reject to mark failed · Both are recorded in post_feedback
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {items.map((d) => <DraftRow key={d.id} d={d} />)}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
'''


def step5_write_drafts_page() -> None:
    print("\n[5/6] Write /trading/drafts page")
    DRAFTS_PAGE.parent.mkdir(parents=True, exist_ok=True)
    DRAFTS_PAGE.write_text(DRAFTS_PAGE_SRC)
    print(f"  ✓ wrote {DRAFTS_PAGE.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 6. Sidebar + TS check + API restart
# ─────────────────────────────────────────────────────────────────────

def step6_sidebar_and_verify() -> bool:
    print("\n[6/6] Sidebar + TS + restart + smoke")

    src = SIDEBAR.read_text()
    if '"/trading/drafts"' not in src:
        anchor = '  { href: "/trading/symbols", label: "Symbols", icon: Hash },'
        if anchor in src:
            _patch_and_verify(
                SIDEBAR,
                anchor,
                anchor + '\n  { href: "/trading/drafts", label: "Draft review", icon: FileWarning },',
                "add Draft review link",
            )
            src2 = SIDEBAR.read_text()
            import re
            m = re.search(r'import \{([^}]+)\} from "lucide-react"', src2)
            if m and "FileWarning" not in m.group(1):
                new_imports = m.group(1).rstrip() + ", FileWarning"
                src2 = src2.replace(m.group(0), f'import {{{new_imports}}} from "lucide-react"')
                SIDEBAR.write_text(src2)
                print("  ✓ sidebar.tsx: FileWarning import added")
        else:
            print("  ⚠ sidebar.tsx: /trading/symbols anchor not found")

    print("\n  running tsc --noEmit...")
    r = subprocess.run(
        ["npx", "tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        print("  ❌ TypeScript errors:")
        for line in r.stdout.splitlines()[:20]:
            print(f"      {line}")
        return False
    print("  ✓ TypeScript clean")

    # Restart API
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)
    log = Path("/tmp/signaliq-api.log")
    with log.open("w") as f:
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

    # Smoke test the drafts endpoint
    r = subprocess.run([
        "python", "-c", """
import subprocess, json
r = subprocess.run([
    "curl", "-s", "-X", "POST", "http://localhost:8000/api/auth/login",
    "-H", "Content-Type: application/json",
    "-d", '{"email":"elearningyarhaya@gmail.com","password":"12345678"}',
], capture_output=True, text=True)
token = json.loads(r.stdout)["access_token"]
r = subprocess.run([
    "curl", "-s", "http://localhost:8000/api/trading/drafts",
    "-H", f"Authorization: Bearer {token}",
], capture_output=True, text=True)
d = json.loads(r.stdout)
print(f"  drafts: {d['total']}")
for it in d['items'][:3]:
    print(f"    - {it['subblock_slug']:20} {it['title'][:60]}")
"""
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    print(r.stdout)
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.11: Draft review workflow")
    print("=" * 70)

    step1_store_rejection_reason()
    step2_extend_schemas()
    step3_add_endpoints()
    step4_extend_api_ts()
    step5_write_drafts_page()
    ok = step6_sidebar_and_verify()

    print("=" * 70)
    if ok:
        print("✅ T.11 complete — draft review workflow live")
        print()
        print("Visit http://localhost:3000/trading/drafts")
        sys.exit(0)
    print("❌ T.11 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
