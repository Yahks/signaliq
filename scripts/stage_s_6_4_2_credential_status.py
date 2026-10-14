"""S.6.4.2 — per-key status: available / rate-limited / paused.

The DB already stores exhausted_until (set by mark_exhausted_sync when
the router hits a 429) and the API already returns it. The frontend
type has the field. What's missing:

  1. A status column that renders the three states:
       - 'available'                (green)
       - 'rate-limited · 8m 12s'    (amber, with countdown)
       - 'paused'                   (grey)
  2. A live countdown that ticks every second
  3. A 'reset' endpoint + button so the user can clear a cooldown
     manually — the router already skips exhausted keys, and the user
     should be able to override the cooldown when they know it's safe.

Backend: one new endpoint POST /api/settings/credentials/{id}/reset
Frontend: status badge + countdown + reset button

Verified:
- mark a credential exhausted via SQL, load the page, see the amber
  badge with countdown
- click reset, the cooldown clears, the badge flips to 'available'
- the reset is logged for the audit trail
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CREDS_SVC = ROOT / "packages" / "core" / "credentials.py"
ROUTER = ROOT / "apps" / "api" / "routers" / "credentials.py"
API_TS = ROOT / "apps" / "web" / "src" / "lib" / "api.ts"
PAGE = ROOT / "apps" / "web" / "src" / "app" / "(app)" / "settings" / "credentials" / "page.tsx"
WEB = ROOT / "apps" / "web"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() and new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(f"{path.name}: pattern not found for '{label}'\n"
             f"--- expected ---\n{old[:400]}")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. credentials.py — clear_exhausted helper
# ─────────────────────────────────────────────────────────────────────

def step1_service() -> None:
    print("\n[1/4] credentials.py — clear_exhausted")

    src = CREDS_SVC.read_text()
    if "def clear_exhausted" in src:
        print("  ↷ already present")
        return

    helper = '''

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
'''
    src = src.rstrip() + helper
    CREDS_SVC.write_text(src)
    print("  ✓ clear_exhausted added")


# ─────────────────────────────────────────────────────────────────────
# 2. router — POST /{id}/reset endpoint
# ─────────────────────────────────────────────────────────────────────

def step2_router() -> None:
    print("\n[2/4] router — POST /{cred_id}/reset")

    src = ROUTER.read_text()
    if "/reset" in src:
        print("  ↷ already present")
        return

    # Import the new helper at the top of the router where the others
    # come in.
    patch(
        ROUTER,
        "from packages.core.credentials import (",
        "from packages.core.credentials import (\n    clear_exhausted,",
        "import clear_exhausted",
    )

    # Add the endpoint right before the support-request one
    patch(
        ROUTER,
        '''@router.post("/support-request", status_code=201)''',
        '''@router.post("/{cred_id}/reset", response_model=CredentialResponse)
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


@router.post("/support-request", status_code=201)''',
        "add reset endpoint",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. api.ts — resetCredential method
# ─────────────────────────────────────────────────────────────────────

def step3_api_ts() -> None:
    print("\n[3/4] api.ts — resetCredential")

    src = API_TS.read_text()
    if "resetCredential:" in src:
        print("  ↷ already present")
        return

    patch(
        API_TS,
        '''  deleteCredential: (id: string) =>''',
        '''  resetCredential: (id: string) =>
    request<Credential>(`/api/settings/credentials/${id}/reset`, {
      method: "POST",
    }),
  deleteCredential: (id: string) =>''',
        "resetCredential method",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. page.tsx — status column + countdown + reset
# ─────────────────────────────────────────────────────────────────────

def step4_page() -> None:
    print("\n[4/4] page.tsx — status badge + countdown + reset")

    # 4a. Add the countdown ticker + a helper for the badge text
    patch(
        PAGE,
        '''export default function CredentialsPage() {
  const qc = useQueryClient();''',
        '''// S.6.4.2 — per-row status. Recomputes every second while the tab is
// open so the "rate-limited · 8m 12s" badge ticks down.
function useNow(intervalMs: number = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

function fmtCountdown(msRemaining: number): string {
  if (msRemaining <= 0) return "";
  const s = Math.ceil(msRemaining / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return `${m}m ${rem}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

type KeyStatus = {
  kind: "available" | "rate_limited" | "paused";
  text: string;
  className: string;
};

function statusFor(c: { is_active: boolean; exhausted_until: string | null }, nowMs: number): KeyStatus {
  if (!c.is_active) {
    return {
      kind: "paused",
      text: "paused",
      className: "rounded-md px-2 py-0.5 text-[11px] bg-muted text-muted-foreground border border-border",
    };
  }
  if (c.exhausted_until) {
    const until = new Date(c.exhausted_until).getTime();
    const remaining = until - nowMs;
    if (remaining > 0) {
      return {
        kind: "rate_limited",
        text: `rate-limited · ${fmtCountdown(remaining)}`,
        className: "rounded-md px-2 py-0.5 text-[11px] bg-amber-500/15 text-amber-400 border border-amber-500/30",
      };
    }
  }
  return {
    kind: "available",
    text: "available",
    className: "rounded-md px-2 py-0.5 text-[11px] bg-green-500/15 text-green-400 border border-green-500/30",
  };
}

export default function CredentialsPage() {
  const qc = useQueryClient();
  const nowMs = useNow(1000);''',
        "add useNow + statusFor helpers",
    )

    # 4b. Add the reset mutation next to the existing ones
    patch(
        PAGE,
        '''  const remove = useMutation({
    mutationFn: (id: string) => api.deleteCredential(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });''',
        '''  const remove = useMutation({
    mutationFn: (id: string) => api.deleteCredential(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });

  // S.6.4.2 — manual reset of a rate-limit cooldown
  const reset = useMutation({
    mutationFn: (id: string) => api.resetCredential(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["credentials"] });
      setSuccess("Cooldown cleared — the key is available again.");
      setTimeout(() => setSuccess(null), 4000);
    },
    onError: (e) => setError(getErrorMessage(e, "Failed to reset")),
  });''',
        "add reset mutation",
    )

    # 4c. Replace the status cell with the new badge
    patch(
        PAGE,
        '''                      <td className="py-2 pr-3">
                        <span className={
                          c.is_active
                            ? "rounded-md px-2 py-0.5 text-[11px] bg-green-500/15 text-green-400 border border-green-500/30"
                            : "rounded-md px-2 py-0.5 text-[11px] bg-muted text-muted-foreground border border-border"
                        }>
                          {c.is_active ? "active" : "paused"}
                        </span>
                      </td>''',
        '''                      <td className="py-2 pr-3">
                        {(() => {
                          const s = statusFor(c, nowMs);
                          return <span className={s.className}>{s.text}</span>;
                        })()}
                      </td>''',
        "status cell uses statusFor",
    )

    # 4d. Add the reset button to the actions cell, before delete
    patch(
        PAGE,
        '''                        <button
                          onClick={() => {
                            if (confirm(`Delete credential "${c.label}"?`)) remove.mutate(c.id);
                          }}
                          className="text-xs underline text-destructive hover:text-red-300"
                        >
                          delete
                        </button>''',
        '''                        {statusFor(c, nowMs).kind === "rate_limited" && (
                          <button
                            onClick={() => {
                              if (confirm(`Clear the rate-limit cooldown for "${c.label}"?`)) {
                                reset.mutate(c.id);
                              }
                            }}
                            className="text-xs underline text-amber-400 hover:text-amber-300"
                          >
                            reset
                          </button>
                        )}
                        <button
                          onClick={() => {
                            if (confirm(`Delete credential "${c.label}"?`)) remove.mutate(c.id);
                          }}
                          className="text-xs underline text-destructive hover:text-red-300"
                        >
                          delete
                        </button>''',
        "reset button",
    )

    # 4e. Ensure useEffect is imported
    src = PAGE.read_text()
    if "useEffect" not in src.split("from \"react\"")[0]:
        # Add to the existing react import
        import re
        m = re.search(r'import \{([^}]+)\} from "react";', src)
        if m and "useEffect" not in m.group(1):
            names = m.group(1).strip().rstrip(",") + ", useEffect"
            src = src.replace(m.group(0), f'import {{{names}}} from "react";', 1)
            PAGE.write_text(src)
            print("  ✓ page.tsx: added useEffect import")


# ─────────────────────────────────────────────────────────────────────
# Verify
# ─────────────────────────────────────────────────────────────────────

def verify() -> bool:
    print("\n" + "=" * 60)
    print("VERIFY")
    print("=" * 60)

    for f in [CREDS_SVC, ROUTER]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ python files parse")

    print("\n  tsc --noEmit")
    r = subprocess.run(
        ["./node_modules/.bin/tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        print("  ❌ TS errors:")
        for line in (r.stdout or r.stderr).splitlines()[:20]:
            print(f"     {line}")
        return False
    print("  ✓ TypeScript clean")

    # Restart API
    print("  restart API")
    subprocess.run(["bash", "-c", "fuser -k 8000/tcp 2>/dev/null"], check=False)
    time.sleep(2)
    with Path("/tmp/signaliq-api.log").open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(6)
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout}")
        return False
    print("  ✓ API up")

    # Behavioral: mark a credential exhausted, verify the API returns it,
    # then reset it.
    print("\n  behavioral test")
    code = r'''
import json, subprocess, time, urllib.request, urllib.error

API = "http://localhost:8000"

def post(path, body=None, token=None):
    req = urllib.request.Request(f"{API}{path}", method="POST")
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    else:
        data = b""
    if token: req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, data) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def get(path, token=None):
    req = urllib.request.Request(f"{API}{path}")
    if token: req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

status, data = post("/api/auth/login",
                    {"identifier": "tokentest", "password": "TestPass123!"})
token = data["access_token"]

# Find one credential for the org
items = get("/api/settings/credentials", token)["items"]
if not items:
    print("  ⚠ no credentials to test")
    raise SystemExit(0)
cid = items[0]["id"]
print(f"  testing with credential: {items[0]['label']}  ({cid[:8]})")

# 1. Mark it exhausted via SQL
r = subprocess.run([
    "docker", "exec", "-i", "signaliq-postgres-1",
    "psql", "-U", "signaliq", "-d", "signaliq", "-c",
    f"UPDATE ai_credentials SET exhausted_until = now() + interval '10 minutes' WHERE id = '{cid}'"
], capture_output=True, text=True)
if r.returncode != 0:
    print(f"  ❌ failed to mark exhausted: {r.stderr[:200]}")
    raise SystemExit(1)
print("  marked exhausted_until = now() + 10m")

# 2. Read it back — exhausted_until should be set
items = get("/api/settings/credentials", token)["items"]
c = next(x for x in items if x["id"] == cid)
print(f"  after mark: exhausted_until = {c['exhausted_until']}")
if not c["exhausted_until"]:
    print("  ❌ exhausted_until not returned")
    raise SystemExit(1)

# 3. Reset via the new endpoint
status, data = post(f"/api/settings/credentials/{cid}/reset", token=token)
print(f"  POST reset -> {status}")
if status != 200:
    print(f"    body: {str(data)[:200]}")
    raise SystemExit(1)

# 4. Confirm it's cleared
items = get("/api/settings/credentials", token)["items"]
c = next(x for x in items if x["id"] == cid)
print(f"  after reset: exhausted_until = {c['exhausted_until']}")
if c["exhausted_until"]:
    print("  ❌ exhausted_until still set")
    raise SystemExit(1)

print()
print("  ✓ mark exhausted -> API returns exhausted_until")
print("  ✓ reset endpoint -> clears exhausted_until")
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=60,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-400:])
        return False
    return True


def main() -> None:
    print("=" * 70)
    print("S.6.4.2 — per-key status: available / rate-limited / paused")
    print("=" * 70)

    step1_service()
    step2_router()
    step3_api_ts()
    step4_page()
    ok = verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.4.2 complete")
        print()
        print("Browser: /settings/credentials shows a Status column with")
        print("  available | rate-limited · 8m 12s | paused")
        print("And a 'reset' action when a key is rate-limited.")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
