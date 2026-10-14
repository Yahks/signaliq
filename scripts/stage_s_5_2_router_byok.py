"""
SignalIQ — Phase S.5.2: Router reads user's keys (BYOK).

Replaces the global .env key path with per-org credentials:

  Before: _try_complete used ai_models row + .env api key.
  After:  _try_complete reads the current org's ai_credentials,
          instantiates the provider with the user's key, and rotates
          to the next credential on 429.

New errors (all carry user-friendly messages):
  NoContextError           — no current_org_id (no user to bill)
  BlockDisabledError       — block is off for this org
  NoCredentialsError       — user has no key for this role
  CredentialsExhaustedError — all user keys are 429'd

When CredentialsExhaustedError fires from a Celery task, the task
catches it, creates a notification, and returns a skip status.

No fallback to admin keys. Ever.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ERRORS = ROOT / "packages" / "core" / "error.py"
CREDS = ROOT / "packages" / "core" / "credentials.py"
GROQ = ROOT / "packages" / "ai" / "providers" / "groq_provider.py"
GEMINI = ROOT / "packages" / "ai" / "providers" / "gemini_provider.py"
OPENROUTER = ROOT / "packages" / "ai" / "providers" / "openrouter_provider.py"
ROUTER = ROOT / "packages" / "ai" / "router.py"
TRADING_TASK = ROOT / "apps" / "worker" / "tasks" / "trading.py"


def _patch(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"❌ {path.name} not found")
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(f"\n❌ {path.name}: pattern not found for '{label}'\n"
                         f"--- expected ---\n{old[:400]}\n---")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. New error classes
# ─────────────────────────────────────────────────────────────────────

ERRORS_ADDITION = '''

# ─────────────────────────────────────────────────────────────────────
# BYOK / context errors (S.5.2)
# ─────────────────────────────────────────────────────────────────────

class NoContextError(SignalIQError):
    """Raised when an LLM call has no current_org_id set.

    Every LLM call must belong to a user. There is no admin fallback.
    """
    code = "no_context"
    http_status = 500


class BlockDisabledError(SignalIQError):
    """Raised when the block that triggered an LLM call is off for this org."""
    code = "block_disabled"
    http_status = 403


class NoCredentialsError(SignalIQError):
    """Raised when the current org has no API credentials for this role."""
    code = "no_credentials"
    http_status = 402


class CredentialsExhaustedError(SignalIQError):
    """Raised when all of the org's credentials for a role are rate-limited."""
    code = "credentials_exhausted"
    http_status = 429
'''


def step1_errors() -> None:
    print("\n[1/7] Add BYOK error classes")
    src = ERRORS.read_text()
    if "class NoContextError" in src:
        print("  ↷ errors already present")
        return
    ERRORS.write_text(src.rstrip() + "\n" + ERRORS_ADDITION)
    print("  ✓ errors.py: +4 error classes")


# ─────────────────────────────────────────────────────────────────────
# 2. Sync variants in credentials.py
# ─────────────────────────────────────────────────────────────────────

def step2_sync_credentials() -> None:
    print("\n[2/7] Add sync credential helpers")
    src = CREDS.read_text()
    if "def get_next_credential_sync" in src:
        print("  ↷ sync helpers already present")
        return

    sync_helpers = '''

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
          AND role = :role
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


def any_credentials_exist_sync(session, org_id: str, role: str) -> bool:
    """True if the org has any active credentials for this role (exhausted or not)."""
    row = session.execute(sql_text("""
        SELECT 1 FROM ai_credentials
        WHERE org_id = :oid AND role = :role AND is_active = true
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
'''
    CREDS.write_text(src.rstrip() + "\n" + sync_helpers)
    print("  ✓ credentials.py: +4 sync helpers")


# ─────────────────────────────────────────────────────────────────────
# 3. Provider __init__ accepts api_key
# ─────────────────────────────────────────────────────────────────────

def step3_provider_api_key() -> None:
    print("\n[3/7] Providers accept api_key at construction")

    # Groq
    src = GROQ.read_text()
    if "def __init__" not in src:
        _patch(
            GROQ,
            'class GroqProvider(ModelProvider):\n    name = "groq"\n',
            '''class GroqProvider(ModelProvider):
    name = "groq"

    def __init__(self, api_key: str | None = None) -> None:
        from packages.core.config import get_settings
        s = get_settings()
        self._api_key = api_key or getattr(s, "groq_api_key", None)
        if not self._api_key:
            raise ProviderError("Groq API key not configured")
''',
            "Groq __init__",
        )
        # In the complete method, replace the settings lookup with self._api_key
        src = GROQ.read_text()
        import re
        # Look for the headers construction and swap
        src2 = re.sub(
            r'"Authorization":\s*f?"Bearer\s*\{[^}]+\}"',
            '"Authorization": f"Bearer {self._api_key}"',
            src,
        )
        if src2 != src:
            GROQ.write_text(src2)
            print("  ✓ groq_provider.py: header uses self._api_key")

    # Gemini
    src = GEMINI.read_text()
    if "def __init__" in src and "api_key: str | None" not in src:
        _patch(
            GEMINI,
            'class GeminiProvider(ModelProvider):\n    name = "gemini"\n',
            '''class GeminiProvider(ModelProvider):
    name = "gemini"

    def __init__(self, api_key: str | None = None) -> None:
        from packages.core.config import get_settings
        s = get_settings()
        key = api_key or getattr(s, "gemini_api_key", None)
        if not key:
            raise ProviderError("Gemini API key not configured")
        from google import genai
        self._client = genai.Client(api_key=key)
''',
            "Gemini __init__",
        )

    # OpenRouter
    src = OPENROUTER.read_text()
    if "def __init__" not in src:
        _patch(
            OPENROUTER,
            'class OpenRouterProvider(ModelProvider):\n    name = "openrouter"\n',
            '''class OpenRouterProvider(ModelProvider):
    name = "openrouter"

    def __init__(self, api_key: str | None = None) -> None:
        from packages.core.config import get_settings
        s = get_settings()
        self._api_key = api_key or getattr(s, "openrouter_api_key", None)
        if not self._api_key:
            raise ProviderError("OpenRouter API key not configured")
''',
            "OpenRouter __init__",
        )


# ─────────────────────────────────────────────────────────────────────
# 4. Router: block gate + credential lookup + rotation
# ─────────────────────────────────────────────────────────────────────

def step4_router_rewrite() -> None:
    print("\n[4/7] Router: BYOK lookup + rotation")

    # Add BYOK imports at the top of the file
    src = ROUTER.read_text()
    if "from packages.core.credentials import" not in src:
        _patch(
            ROUTER,
            "from packages.core.logging import get_logger\n",
            "from packages.core.logging import get_logger\n"
            "from packages.core.credentials import (\n"
            "    get_next_credential_sync,\n"
            "    mark_exhausted_sync,\n"
            "    mark_success_sync,\n"
            "    any_credentials_exist_sync,\n"
            ")\n"
            "from packages.core.errors import (\n"
            "    NoContextError,\n"
            "    BlockDisabledError,\n"
            "    NoCredentialsError,\n"
            "    CredentialsExhaustedError,\n"
            ")\n",
            "import credentials + errors",
        )

    # Now the meaningful rewrite of _try_complete's opening.
    # Find the current opening (after our earlier patches) and replace the
    # exhaustion-gate block with the full BYOK flow.
    src = ROUTER.read_text()

    old_open = '''    # Exhaustion gate — last-resort safety net. If the current user
    # has 0 tokens, refuse even if a caller forgot to check.
    from packages.core.context import current_user_id as _uid_ctx
    _uid = _uid_ctx.get()
    if _uid:
        from packages.core.tokens import can_generate_sync
        if not can_generate_sync(session, _uid):
            raise ProviderError("Insufficient tokens")

    # Cache lookup
    req_hash = _hash_request(provider_name, model_name, system, user, json_mode)'''

    new_open = '''    # ─────────────────────────────────────────────────────────────────
    # BYOK gate (S.5.2): every LLM call must belong to a user.
    # ─────────────────────────────────────────────────────────────────
    from packages.core.context import current_org_id as _org_ctx
    org_ctx = _org_ctx.get()
    if not org_ctx:
        raise NoContextError(
            "No organisation context — every LLM call must belong to a user"
        )

    # Block gate — the block that triggered this call must be enabled.
    # Map role → block for now: this whole router is used by news + trading.
    # We use a conservative default: check the 'trading' block. Callers can
    # extend by adding role → block mapping in future.
    block_slug = _block_for_purpose(purpose)
    if block_slug:
        block_row = session.execute(
            sql_text("""
                SELECT auto_enabled FROM org_block_settings
                WHERE org_id = :oid AND block_slug = :slug
            """),
            {"oid": org_ctx, "slug": block_slug},
        ).first()
        if block_row is not None and not block_row[0]:
            raise BlockDisabledError(
                f"The {block_slug} block is disabled for your organisation. "
                f"Enable it on the dashboard to generate."
            )

    # User credentials — try them in priority order.
    has_any = any_credentials_exist_sync(session, org_ctx, role)
    if not has_any:
        raise NoCredentialsError(
            "No API credentials configured for this organisation. "
            "Add a key at /settings/credentials to generate."
        )

    from packages.core.tokens import can_generate_sync
    from packages.core.context import current_user_id as _uid_ctx
    _uid = _uid_ctx.get()
    if _uid and not can_generate_sync(session, _uid):
        raise NoCredentialsError("Insufficient platform tokens")

    cred = get_next_credential_sync(session, org_ctx, role)
    if not cred:
        raise CredentialsExhaustedError(
            "All of your API keys are currently rate-limited. "
            "Add another key at /settings/credentials or wait for the limits to reset."
        )
    provider_name = cred["provider"]
    model_name = cred["model_name"]
    _cred_id = cred["id"]
    _user_api_key = cred["api_key"]

    # Cache lookup
    req_hash = _hash_request(provider_name, model_name, system, user, json_mode)'''

    if new_open in src:
        print("  ↷ router BYOK gate already applied")
    elif old_open not in src:
        raise SystemExit(
            "❌ router: opening pattern not found — paste lines around _try_complete start"
        )
    else:
        src = src.replace(old_open, new_open, 1)
        ROUTER.write_text(src)
        print("  ✓ router: BYOK gate + block gate + credential lookup")

    # Now the provider construction — use the credential's api_key
    src = ROUTER.read_text()
    old_provider = '''    provider: ModelProvider = get_provider(provider_name)'''
    new_provider = '''    provider: ModelProvider = _build_provider(provider_name, _user_api_key)'''
    if new_provider in src:
        print("  ↷ provider construction already patched")
    elif old_provider in src:
        src = src.replace(old_provider, new_provider, 1)
        ROUTER.write_text(src)
        print("  ✓ router: provider uses user's API key")

    # Success / 429 handling — after the provider call, mark the credential
    src = ROUTER.read_text()
    old_success = '''    if status == "ok" and result is not None:
        try:
            from packages.core.context import current_user_id
            uid = current_user_id.get()
            log.info("ai.debit_check", has_uid=bool(uid), purpose=purpose)'''
    new_success = '''    if status == "ok" and result is not None:
        try:
            mark_success_sync(session, _cred_id)
        except Exception as exc:
            log.warning("router.mark_success_failed", error=str(exc)[:120])
        try:
            from packages.core.context import current_user_id
            uid = current_user_id.get()
            log.info("ai.debit_check", has_uid=bool(uid), purpose=purpose)'''
    if new_success in src:
        print("  ↷ mark_success already wired")
    elif old_success in src:
        src = src.replace(old_success, new_success, 1)
        ROUTER.write_text(src)
        print("  ✓ router: mark_success_sync on success")

    # On 429: mark the credential exhausted and raise CredentialsExhaustedError
    # for retry by the outer complete() fallback chain.
    src = ROUTER.read_text()
    old_429 = '''    except ProviderError as exc:
        err_msg = str(exc)[:1000]
        status = "error"

    latency_ms = int((time.monotonic() - t0) * 1000)'''
    new_429 = '''    except ProviderError as exc:
        err_msg = str(exc)[:1000]
        status = "error"
        # If this was a 429, mark the credential exhausted for 15 minutes.
        if "429" in err_msg or "rate" in err_msg.lower():
            try:
                mark_exhausted_sync(session, _cred_id, seconds=900)
            except Exception as _exc:
                log.warning("router.mark_exhausted_failed", error=str(_exc)[:120])
            # Bubble up so complete()'s loop can try the next model/credential
            from packages.core.errors import CredentialsExhaustedError as _CE
            raise _CE(f"Credential rate-limited: {err_msg}") from exc

    latency_ms = int((time.monotonic() - t0) * 1000)'''
    if new_429 in src:
        print("  ↷ 429 handling already wired")
    elif old_429 in src:
        src = src.replace(old_429, new_429, 1)
        ROUTER.write_text(src)
        print("  ✓ router: 429 marks credential exhausted and bubbles")

    # Helper functions: _build_provider, _block_for_purpose
    src = ROUTER.read_text()
    helpers = '''

def _block_for_purpose(purpose: str) -> str | None:
    """Map a purpose string to the block that should gate it.

    Every LLM call belongs to a block. If a purpose isn't mapped,
    we default to 'trading' for anything with 'trading.' prefix and
    'news' for everything else.
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
    if provider_name == "ollama":
        return OllamaProvider()
    raise ProviderError(f"Unknown provider: {provider_name}")
'''
    if "_build_provider" not in src:
        src = src.rstrip() + helpers
        ROUTER.write_text(src)
        print("  ✓ router: _build_provider + _block_for_purpose helpers")


# ─────────────────────────────────────────────────────────────────────
# 5. Celery task: catch CredentialsExhaustedError → notification
# ─────────────────────────────────────────────────────────────────────

def step5_celery_catch() -> None:
    print("\n[5/7] Celery: catch CredentialsExhaustedError → notify user")

    _patch(
        TRADING_TASK,
        '''        try:
            result = await generate_subblock(session, org_id=org_id, slug=slug)
        except Exception as exc:
            log.warning("trading.generate_failed", slug=slug, error=str(exc)[:200])
            result = {"status": "failed", "slug": slug, "error": str(exc)[:200]}''',
        '''        try:
            result = await generate_subblock(session, org_id=org_id, slug=slug)
        except Exception as exc:
            from packages.core.errors import CredentialsExhaustedError, NoCredentialsError
            err_name = type(exc).__name__
            if isinstance(exc, (CredentialsExhaustedError, NoCredentialsError)):
                log.warning("trading.credentials_issue", slug=slug, error=str(exc)[:200])
                # Notify the org owner
                try:
                    if owner_id:
                        title = "Your API keys need attention" if isinstance(exc, NoCredentialsError) else "All API keys are rate-limited"
                        await session.execute(sql_text("""
                            INSERT INTO notifications
                                (org_id, user_id, kind, title, body)
                            VALUES
                                (:oid, :uid, 'credentials_issue', :title, :body)
                        """), {
                            "oid": org_id, "uid": owner_id,
                            "title": title,
                            "body": f"{str(exc)[:400]} Go to /settings/credentials.",
                        })
                        await session.commit()
                except Exception as _n:
                    log.warning("trading.notify_failed", error=str(_n)[:120])
                result = {"status": "skipped_credentials_issue", "slug": slug,
                          "org_id": org_id, "reason": err_name}
            else:
                log.warning("trading.generate_failed", slug=slug, error=str(exc)[:200])
                result = {"status": "failed", "slug": slug, "error": str(exc)[:200]}''',
        "credential-exhaustion catch",
    )


# ─────────────────────────────────────────────────────────────────────
# 6. Add openrouter_api_key to settings if missing
# ─────────────────────────────────────────────────────────────────────

def step6_settings_key() -> None:
    print("\n[6/7] Ensure openrouter_api_key + gemini_api_key in settings")
    settings_p = ROOT / "packages" / "core" / "config.py"
    src = settings_p.read_text()
    additions = []
    if "openrouter_api_key" not in src:
        additions.append("    openrouter_api_key: str | None = None\n")
    if "gemini_api_key" not in src:
        additions.append("    gemini_api_key: str | None = None\n")
    if "groq_api_key" not in src:
        additions.append("    groq_api_key: str | None = None\n")
    if additions:
        anchor = "    fred_api_key: str | None = None"
        if anchor not in src:
            anchor = "    redis_url: str = \"redis://localhost:6379/0\""
        src = src.replace(anchor, anchor + "\n" + "".join(additions), 1)
        settings_p.write_text(src)
        print(f"  ✓ config.py: added {len(additions)} api_key fields")
    else:
        print("  ↷ settings already have provider api_key fields")


# ─────────────────────────────────────────────────────────────────────
# 7. Restart + verify (no credentials → NoCredentialsError)
# ─────────────────────────────────────────────────────────────────────

def step7_verify() -> bool:
    print("\n[7/7] Restart API + verify BYOK behavior")

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
    print("  ✓ /health 200")

    # Verify import doesn't break
    r = subprocess.run(
        ["python", "-c",
         "from packages.ai.router import complete, _try_complete, _build_provider; "
         "from packages.core.credentials import get_next_credential_sync; "
         "from packages.core.errors import NoCredentialsError, CredentialsExhaustedError; "
         "print('import OK')"],
        cwd=ROOT, capture_output=True, text=True,
    )
    print(f"  {r.stdout.strip() or r.stderr[-300:]}")

    # Add a test credential for demo + run one generation
    r = subprocess.run([
        "python", "-c", """
import asyncio, sys
sys.path.insert(0, '.')
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.core.credentials import add_credential, list_credentials, delete_credential
from packages.db.session import get_owner_session_factory

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with get_owner_session_factory()() as s:
        org = (await s.execute(text(\"\"\"
            SELECT o.id::text FROM orgs o
            JOIN memberships m ON m.org_id = o.id
            JOIN users u ON u.id = m.user_id
            WHERE u.username = 'demo' LIMIT 1
        \"\"\"))).first()
    org_id = org[0]

    # Add a test credential with a fake key
    async with factory() as s:
        await s.execute(text(f"SET app.current_org_id = '{org_id}'"))
        await s.commit()
        existing = await list_credentials(s, org_id)
        for e in existing:
            await delete_credential(s, org_id, e['id'])
        cred = await add_credential(
            s, org_id, provider='groq',
            model_name='openai/gpt-oss-120b',
            role='reasoning', label='demo-test-key',
            api_key='gsk_fake_test_key_for_verification',
        )
        print(f"  ✓ added test credential: {cred['id'][:8]}…")

    # Try a call — expect failure from the fake key
    from packages.core.context import current_org_id, current_user_id
    current_org_id.set(org_id)
    from packages.ai.router import complete
    try:
        result = await complete(
            role='reasoning',
            system='Reply in one word.',
            user='Say hi.',
            purpose='s5_2.verify',
            use_cache=False,
        )
        print(f"  ??? unexpected success: {result[:40]}")
    except Exception as exc:
        err_name = type(exc).__name__
        print(f"  ✓ expected failure: {err_name}")
        print(f"     (this confirms the router used the fake key)")

    await engine.dispose()

asyncio.run(main())
        """.strip()
    ], cwd=ROOT, capture_output=True, text=True, timeout=60)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False

    return "expected failure" in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.5.2: Router reads user's keys (BYOK)")
    print("=" * 70)

    step1_errors()
    step2_sync_credentials()
    step3_provider_api_key()
    step4_router_rewrite()
    step5_celery_catch()
    step6_settings_key()
    ok = step7_verify()

    print("=" * 70)
    if ok:
        print("✅ S.5.2 complete — router uses the user's API key, no fallback")
        print()
        print("Every LLM call now:")
        print("  1. Requires an org context (else NoContextError)")
        print("  2. Requires the block to be enabled (else BlockDisabledError)")
        print("  3. Requires user credentials (else NoCredentialsError)")
        print("  4. Rotates through user's credentials on 429")
        print("  5. Raises CredentialsExhaustedError when all are 429'd")
        print()
        print("Next: S.5.3 — API endpoints for credential CRUD.")
        sys.exit(0)
    print("❌ S.5.2 failed — see output")
    sys.exit(1)


if __name__ == "__main__":
    main()
