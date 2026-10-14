"""S.6.4.3 — code patches for per-key Groq token visibility.

Six files, each patched in sequence with a syntax check after every
edit. No 800-line monolith; if a patch fails to match, this script
stops at that file and reports the exact pattern it expected.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        fail(f"{path.relative_to(ROOT)} does not exist")
    src = path.read_text()
    if new in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(
            f"{path.name}: pattern not found for '{label}'\n"
            f"--- expected (first 400 chars) ---\n{old[:400]}"
        )
    path.write_text(src.replace(old, new, 1))
    # Confirm the replacement landed
    if new not in path.read_text():
        fail(f"{path.name}: wrote the patch but new text not found after re-read")
    # Confirm the file still parses
    try:
        ast.parse(path.read_text())
    except SyntaxError as e:
        fail(f"{path.name}: syntax error after '{label}': {e}")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. providers/base.py — CompletionResult.rate_headers
# ─────────────────────────────────────────────────────────────────────

def step1_base() -> None:
    print("\n[1/6] providers/base.py")
    p = ROOT / "packages" / "ai" / "providers" / "base.py"
    patch(
        p,
        '''class CompletionResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)''',
        '''class CompletionResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    # S.6.4.3 — provider rate-limit headers when available. Groq returns
    # x-ratelimit-* on every response; we store them so the UI can show
    # the user's remaining quota per key. None for providers that don't.
    rate_headers: dict[str, str] | None = None''',
        "CompletionResult.rate_headers",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. groq_provider.py — capture x-ratelimit-* from the response
# ─────────────────────────────────────────────────────────────────────

def step2_groq() -> None:
    print("\n[2/6] groq_provider.py")
    p = ROOT / "packages" / "ai" / "providers" / "groq_provider.py"
    src = p.read_text()
    if "x-ratelimit-" in src:
        print("  ↷ already captured")
        return
    old = '''        return CompletionResult(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            raw={},
        )'''
    new = '''        rate_headers = {
            k.lower(): v for k, v in r.headers.items()
            if k.lower().startswith("x-ratelimit-")
        }
        return CompletionResult(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            raw={},
            rate_headers=rate_headers or None,
        )'''
    patch(p, old, new, "capture x-ratelimit-*")


# ─────────────────────────────────────────────────────────────────────
# 3. router.py — _try_with_credential INSERT + success hook
# ─────────────────────────────────────────────────────────────────────

def step3_router_insert() -> None:
    print("\n[3/6] router.py — INSERT")
    p = ROOT / "packages" / "ai" / "router.py"
    patch(
        p,
        '''            INSERT INTO ai_calls (
                org_id, model_id, prompt_version_id, purpose, input_tokens, output_tokens,
                latency_ms, status, error, request_hash, response_payload
            ) VALUES (
                :org_id, :model_id, :prompt_version_id, :purpose, :in_tok, :out_tok,
                :latency, :status, :error, :hash, CAST(:payload AS jsonb)
            )
        """),
        {
            "org_id": org_id,
            "model_id": model_id,
            "prompt_version_id": prompt_version_id,
            "purpose": purpose,
            "in_tok": result.input_tokens if result else None,
            "out_tok": result.output_tokens if result else None,
            "latency": latency_ms,
            "status": status,
            "error": err_msg,
            "hash": req_hash,
            "payload": payload_json,
        },
    )
    session.commit()''',
        '''            INSERT INTO ai_calls (
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
            "prompt_version_id": prompt_version_id,
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
    session.commit()''',
        "credential_id + rate_headers",
    )


def step4_router_success() -> None:
    print("\n[4/6] router.py — success hook")
    p = ROOT / "packages" / "ai" / "router.py"
    patch(
        p,
        '''    # ── Success ──
    try:
        mark_success_sync(session, cred_id)
    except Exception as exc:
        log.warning("router.mark_success_failed", error=str(exc)[:120])''',
        '''    # ── Success ──
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
                log.warning("router.rate_state_update_failed", error=str(exc)[:120])''',
        "cache rate state on success",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. credentials.py — list_credentials subqueries + mapping
# ─────────────────────────────────────────────────────────────────────

def step5_list_credentials() -> None:
    print("\n[5/6] credentials.py — list_credentials")
    p = ROOT / "packages" / "core" / "credentials.py"
    patch(
        p,
        '''        SELECT id::text, provider, model_name, roles, label,
               api_key_enc, is_active, priority,
               exhausted_until, last_success_at, last_429_at,
               created_at, updated_at
        FROM ai_credentials
        WHERE {' AND '.join(where)}
        ORDER BY priority ASC, created_at ASC
    """), params)).all()''',
        '''        SELECT id::text, provider, model_name, roles, label,
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
    """), params)).all()''',
        "aggregate subqueries",
    )
    patch(
        p,
        '''        out.append({
            "id": r[0], "provider": r[1], "model_name": r[2], "roles": list(r[3] or []),
            "label": r[4], "key_preview": preview, "is_active": bool(r[6]),
            "priority": int(r[7]),
            "exhausted_until": r[8], "last_success_at": r[9], "last_429_at": r[10],
            "created_at": r[11], "updated_at": r[12],''',
        '''        out.append({
            "id": r[0], "provider": r[1], "model_name": r[2], "roles": list(r[3] or []),
            "label": r[4], "key_preview": preview, "is_active": bool(r[6]),
            "priority": int(r[7]),
            "exhausted_until": r[8], "last_success_at": r[9], "last_429_at": r[10],
            "created_at": r[11], "updated_at": r[12],
            "last_remaining_tokens": r[13], "last_rate_at": r[14],
            "tokens_used": int(r[15] or 0), "calls_made": int(r[16] or 0),''',
        "mapping",
    )


# ─────────────────────────────────────────────────────────────────────
# 6. schema + _to_response
# ─────────────────────────────────────────────────────────────────────

def step6_schema() -> None:
    print("\n[6/6] schemas + _to_response")
    p = ROOT / "apps" / "api" / "schemas" / "credentials.py"
    patch(
        p,
        '''class CredentialResponse(BaseModel):
    id: str
    provider: str
    model_name: str
    roles: list[str]
    label: str
    key_preview: str
    is_active: bool
    priority: int
    exhausted_until: datetime | None = None
    last_success_at: datetime | None = None
    created_at: datetime''',
        '''class CredentialResponse(BaseModel):
    id: str
    provider: str
    model_name: str
    roles: list[str]
    label: str
    key_preview: str
    is_active: bool
    priority: int
    exhausted_until: datetime | None = None
    last_success_at: datetime | None = None
    created_at: datetime
    # S.6.4.3 — per-key Groq token visibility
    tokens_used: int = 0
    calls_made: int = 0
    last_remaining_tokens: int | None = None
    last_rate_at: datetime | None = None''',
        "add four fields",
    )

    p2 = ROOT / "apps" / "api" / "routers" / "credentials.py"
    patch(
        p2,
        '''        exhausted_until=c.get("exhausted_until"),
        last_success_at=c.get("last_success_at"),
        created_at=c["created_at"],
    )''',
        '''        exhausted_until=c.get("exhausted_until"),
        last_success_at=c.get("last_success_at"),
        created_at=c["created_at"],
        tokens_used=c.get("tokens_used") or 0,
        calls_made=c.get("calls_made") or 0,
        last_remaining_tokens=c.get("last_remaining_tokens"),
        last_rate_at=c.get("last_rate_at"),
    )''',
        "_to_response maps new fields",
    )


# ─────────────────────────────────────────────────────────────────────
# Frontend — api.ts type + page columns
# ─────────────────────────────────────────────────────────────────────

def step7_frontend_type() -> None:
    print("\n[+1] api.ts — Credential type")
    p = ROOT / "apps" / "web" / "src" / "lib" / "api.ts"
    patch(
        p,
        '''export type Credential = {
  id: string;
  provider: string;
  model_name: string;
  roles: string[];
  label: string;
  key_preview: string;
  is_active: boolean;
  priority: number;
  exhausted_until: string | null;
  last_success_at: string | null;
  created_at: string;
};''',
        '''export type Credential = {
  id: string;
  provider: string;
  model_name: string;
  roles: string[];
  label: string;
  key_preview: string;
  is_active: boolean;
  priority: number;
  exhausted_until: string | null;
  last_success_at: string | null;
  created_at: string;
  // S.6.4.3
  tokens_used: number;
  calls_made: number;
  last_remaining_tokens: number | null;
  last_rate_at: string | null;
};''',
        "Credential type",
    )


def step8_frontend_page() -> None:
    print("\n[+2] page.tsx — helpers + columns")
    p = ROOT / "apps" / "web" / "src" / "app" / "(app)" / "settings" / "credentials" / "page.tsx"

    # Add helpers before statusFor
    patch(
        p,
        "function statusFor(c: { is_active: boolean; exhausted_until: string | null }, nowMs: number): KeyStatus {",
        '''function fmtRelative(iso: string | null, nowMs: number): string {
  if (!iso) return "\\u2014";
  const ms = nowMs - new Date(iso).getTime();
  if (ms < 60_000) return "just now";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function fmtTokens(n: number | null): string {
  if (n == null) return "\\u2014";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function statusFor(c: { is_active: boolean; exhausted_until: string | null }, nowMs: number): KeyStatus {''',
        "add fmtRelative + fmtTokens",
    )

    # Header columns
    patch(
        p,
        '''                    <th className="text-left py-2 pr-3 font-medium">Key</th>
                    <th className="text-left py-2 pr-3 font-medium">Status</th>''',
        '''                    <th className="text-left py-2 pr-3 font-medium">Key</th>
                    <th className="text-right py-2 pr-3 font-medium">Groq tokens</th>
                    <th className="text-right py-2 pr-3 font-medium">Groq remaining</th>
                    <th className="text-left py-2 pr-3 font-medium">Status</th>''',
        "table header",
    )

    # Row cells
    patch(
        p,
        '''                      <td className="py-2 pr-3 font-mono text-xs">{c.key_preview}</td>
                      <td className="py-2 pr-3">
                        {(() => {
                          const s = statusFor(c, nowMs);
                          return <span className={s.className}>{s.text}</span>;
                        })()}
                      </td>''',
        '''                      <td className="py-2 pr-3 font-mono text-xs">{c.key_preview}</td>
                      <td className="py-2 pr-3 text-right font-mono text-xs">
                        {fmtTokens(c.tokens_used)}
                        <div className="text-[10px] text-muted-foreground">
                          {c.calls_made} call{c.calls_made === 1 ? "" : "s"}
                        </div>
                      </td>
                      <td className="py-2 pr-3 text-right font-mono text-xs">
                        {fmtTokens(c.last_remaining_tokens)}
                        <div className="text-[10px] text-muted-foreground">
                          {fmtRelative(c.last_rate_at, nowMs)}
                        </div>
                      </td>
                      <td className="py-2 pr-3">
                        {(() => {
                          const s = statusFor(c, nowMs);
                          return <span className={s.className}>{s.text}</span>;
                        })()}
                      </td>''',
        "table row cells",
    )


def main() -> None:
    print("=" * 70)
    print("S.6.4.3 — code patches")
    print("=" * 70)

    step1_base()
    step2_groq()
    step3_router_insert()
    step4_router_success()
    step5_list_credentials()
    step6_schema()
    step7_frontend_type()
    step8_frontend_page()

    print()
    print("=" * 70)
    print("All patches applied and every file still parses.")
    print("Next: run scripts/verify_s_6_4_3.py (File 3).")
    print("=" * 70)


if __name__ == "__main__":
    main()
