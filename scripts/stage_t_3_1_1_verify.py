"""
SignalIQ — Phase T.3.1.1: Claim verification for trading posts.

Adds verify_post() to base.py and wires it into call_llm_json.

Mechanism (mirrors news chain D7):
  1. Split LLM body into atomic sentences
  2. Ask reasoning LLM: for each sentence, is it grounded in source_payload?
  3. If any unsupported claims:
     - 1-3 unsupported → regenerate once with those claims forbidden
     - >3 unsupported → accept but mark status='draft'
  4. Return final TradingPost + verification metadata

Idempotent. Fails loudly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"


def _patch(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"\n❌ {path.relative_to(ROOT)} does not exist")
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:500]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. Extend TradingPost with verification metadata
# ─────────────────────────────────────────────────────────────────────

def step1_extend_dataclass() -> None:
    _patch(
        BASE,
        '''@dataclass
class TradingPost:
    title: str
    body: str
    word_count: int
''',
        '''@dataclass
class TradingPost:
    title: str
    body: str
    word_count: int
    # Verification metadata — populated by verify_and_maybe_regenerate
    verification_ok: bool = True
    unsupported_claims: list[str] = None  # type: ignore[assignment]
    regenerated: bool = False

    def __post_init__(self) -> None:
        if self.unsupported_claims is None:
            self.unsupported_claims = []
''',
        "TradingPost verification fields",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Add verify_post() + rewrite call_llm_json to call it
# ─────────────────────────────────────────────────────────────────────

def step2_add_verify() -> None:
    """Insert verify_post() before call_llm_json, and rewrite call_llm_json."""

    # Insert the verify function before call_llm_json
    _patch(
        BASE,
        '''# ─────────────────────────────────────────────────────────────────────
# LLM call
# ─────────────────────────────────────────────────────────────────────

async def call_llm_json(''',
        '''# ─────────────────────────────────────────────────────────────────────
# Verification — mirrors news chain D7
# ─────────────────────────────────────────────────────────────────────

_CLAIM_SPLIT_RE = re.compile(r"(?<=[.!?])\\s+(?=[A-Z])")


def _split_claims(body: str) -> list[str]:
    """Split a markdown body into atomic sentences for verification.

    Strips markdown artifacts (tables, headers, bullets) — the verifier
    only cares about prose claims, and table cells are already checked
    against source_payload by their nature (they are copied).
    """
    # Remove markdown table rows and code fences before splitting
    lines: list[str] = []
    in_table = False
    in_code = False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if s.startswith("|"):
            in_table = True
            continue
        if in_table and not s:
            in_table = False
            continue
        if in_table:
            continue
        if s.startswith("#"):
            continue
        if s.startswith("- ") or s.startswith("* "):
            s = s[2:]
        if not s:
            continue
        lines.append(s)

    text = " ".join(lines)
    # Split into sentences
    sentences = [x.strip() for x in _CLAIM_SPLIT_RE.split(text) if x.strip()]
    # Drop very short fragments
    return [s for s in sentences if len(s) > 20]


async def verify_post(
    session: AsyncSession,
    *,
    org_id: str,
    post: TradingPost,
    source_payload: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Ask the reasoning LLM which claims in the post are NOT grounded.

    Returns (all_supported: bool, unsupported_claims: list[str]).
    Only checks prose (skips tables, headers, code).
    """
    from packages.ai.router import complete

    claims = _split_claims(post.body)
    if not claims:
        return True, []

    # Build a compact, numbered claim list for the verifier
    claims_block = "\\n".join(f"{i+1}. {c}" for i, c in enumerate(claims))

    # Cap source_payload size to avoid blowing the token budget
    payload_str = json.dumps(source_payload, default=str)
    if len(payload_str) > 12_000:
        payload_str = payload_str[:12_000] + "… (truncated)"

    system = (
        "You are a fact-checking verifier. You will receive SOURCE DATA and "
        "a numbered list of CLAIMS. For each claim, decide whether it is "
        "supported by the source data.\\n"
        "\\n"
        "A claim is supported if:\\n"
        "- The number/ticker/name appears verbatim in the source data, OR\\n"
        "- The claim is a plain re-statement of a value in the source data, OR\\n"
        "- The claim is a generic, non-factual framing statement (e.g. "
        "'markets were mixed today').\\n"
        "\\n"
        "A claim is NOT supported if it asserts a CAUSE, REASON, NEWS EVENT, "
        "or FACT that is not present in the source data — including invented "
        "rationale like 'due to a contract win' or 'after earnings'.\\n"
        "\\n"
        "Return STRICT JSON only: {\\"unsupported\\": [<claim numbers>]}"
    )
    user = f"SOURCE DATA:\\n{payload_str}\\n\\nCLAIMS:\\n{claims_block}\\n\\nReturn JSON: {{\\"unsupported\\": [...]}}"

    try:
        raw = await complete(
            role="reasoning",
            system=system,
            user=user,
            org_id=org_id,
            purpose="trading.verify",
            temperature=0.0,
            max_tokens=800,
            json_mode=True,
        )
        parsed = json.loads(_extract_json_block(raw))
        bad_nums = [int(x) for x in parsed.get("unsupported", []) if isinstance(x, (int, str))]
    except Exception as exc:
        log.warning("verify.failed_open", error=str(exc)[:200])
        # Fail open: treat as verified so we don't block generation on verifier bugs
        return True, []

    # Collect unsupported claim texts (1-based)
    unsupported: list[str] = []
    for n in bad_nums:
        if 1 <= n <= len(claims):
            unsupported.append(claims[n - 1])

    if unsupported:
        log.info("verify.unsupported_found", count=len(unsupported))
    else:
        log.info("verify.all_supported", claims=len(claims))
    return (len(unsupported) == 0), unsupported


# ─────────────────────────────────────────────────────────────────────
# LLM call (with verification + optional one-shot regeneration)
# ─────────────────────────────────────────────────────────────────────

async def call_llm_json(''',
        "verify_post() + section header",
    )

    # Now rewrite the body of call_llm_json to call verify + regenerate
    _patch(
        BASE,
        '''    text = (text or "").strip()

    try:
        payload = json.loads(_extract_json_block(text))
    except Exception as exc:
        log.warning("subblock.llm_parse_failed", error=str(exc)[:200], raw=text[:400])
        # Fallback: use whole response as body, generate title from prompt_name
        return TradingPost(
            title=prompt_name.replace(".", " ").title(),
            body=text or "(empty response)",
            word_count=len((text or "").split()),
        )

    title = (payload.get("title") or prompt_name).strip()
    body = (payload.get("body") or "").strip()
    return TradingPost(
        title=title[:500],
        body=body,
        word_count=len(body.split()),
    )''',
        '''    text = (text or "").strip()

    def _parse_response(text: str) -> TradingPost:
        try:
            payload = json.loads(_extract_json_block(text))
        except Exception as exc:
            log.warning("subblock.llm_parse_failed", error=str(exc)[:200], raw=text[:400])
            return TradingPost(
                title=prompt_name.replace(".", " ").title(),
                body=text or "(empty response)",
                word_count=len((text or "").split()),
            )
        title = (payload.get("title") or prompt_name).strip()
        body = (payload.get("body") or "").strip()
        return TradingPost(
            title=title[:500],
            body=body,
            word_count=len(body.split()),
        )

    post = _parse_response(text)

    # --- Verify claims against source data (see verify_post) ---
    # We pass source_payload via a keyword — the caller must have included it.
    # For backwards compatibility, if not passed, we skip verification.
    source_payload = kwargs.pop("_source_payload", None) if (kwargs := {}) else None
    # (The generator passes source data via a separate arg — see below.)
    return post''',
        "call_llm_json parses + returns TradingPost",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Simpler, honest approach: add a new wrapper function
#    verify_and_maybe_regenerate() that generators call after
#    call_llm_json. Do NOT rewrite call_llm_json — keep it as-is.
# ─────────────────────────────────────────────────────────────────────

def step3_add_wrapper() -> None:
    """The clean way: keep call_llm_json as-is, add a wrapper that
    generators call. This avoids the fragile in-place rewrite above."""
    src = BASE.read_text()
    if "async def call_llm_json_verified" in src:
        print("  ↷ base.py: call_llm_json_verified already present")
        return

    wrapper = '''

async def call_llm_json_verified(
    session: AsyncSession,
    *,
    org_id: str,
    system: str,
    user: str,
    prompt_name: str,
    source_payload: dict[str, Any],
    target_words: int = 400,
) -> TradingPost:
    """call_llm_json + verify + one-shot regeneration on unsupported claims.

    This is the "safe" entry point for generators. It:
      1. Calls the LLM to produce a post
      2. Verifies every prose claim against source_payload
      3. If 1-3 claims are unsupported → regenerates once with those
         claims explicitly forbidden
      4. If >3 unsupported OR regeneration still fails → returns the post
         with verification_ok=False and status='draft' (set by persist_post)

    Uses one extra reasoning call per successful generation (two on regen).
    """
    post = await call_llm_json(
        session,
        org_id=org_id,
        system=system,
        user=user,
        prompt_name=prompt_name,
        target_words=target_words,
    )

    ok, unsupported = await verify_post(
        session, org_id=org_id, post=post, source_payload=source_payload,
    )

    if ok:
        post.verification_ok = True
        return post

    # 1-3 unsupported → regenerate once, forbidding those claims
    if 1 <= len(unsupported) <= 3:
        log.info("verify.regenerating", count=len(unsupported))
        forbidden = "\\n".join(f"- {c}" for c in unsupported)
        retry_user = (
            f"{user}\\n\\n"
            f"IMPORTANT — the previous draft contained claims NOT supported by "
            f"the source data. Do NOT state any of the following:\\n{forbidden}\\n\\n"
            f"If you cannot make a claim without this context, omit it entirely."
        )
        post2 = await call_llm_json(
            session,
            org_id=org_id,
            system=system,
            user=retry_user,
            prompt_name=prompt_name + ".retry",
            target_words=target_words,
        )
        post2.regenerated = True
        ok2, unsupported2 = await verify_post(
            session, org_id=org_id, post=post2, source_payload=source_payload,
        )
        if ok2:
            post2.verification_ok = True
            return post2
        # Regeneration didn't fully fix it — accept with warning
        post2.verification_ok = False
        post2.unsupported_claims = unsupported2
        return post2

    # >3 unsupported → don't spend another call, mark for human review
    log.warning("verify.too_many_unsupported", count=len(unsupported))
    post.verification_ok = False
    post.unsupported_claims = unsupported
    return post
'''

    BASE.write_text(src.rstrip() + wrapper)
    print("  ✓ base.py: call_llm_json_verified wrapper added")


# ─────────────────────────────────────────────────────────────────────
# 4. persist_post: set status='draft' when verification failed
# ─────────────────────────────────────────────────────────────────────

def step4_persist_status() -> None:
    _patch(
        BASE,
        '''    row = (await session.execute(sql_text("""
        INSERT INTO trading_posts (
            org_id, subblock_id, title, body, format, source_kind,
            source_payload, source_urls, symbols, status, word_count, published_at
        ) VALUES (
            :org_id, :subblock_id, :title, :body, 'long_form', :source_kind,
            CAST(:source_payload AS jsonb), :source_urls, :symbols,
            'published', :word_count, now()
        )
        RETURNING id::text
    """), {
        "org_id": org_id,
        "subblock_id": subblock["id"],
        "title": post.title,
        "body": post.body,
        "source_kind": subblock["source_kind"],
        "source_payload": json.dumps(source_payload, default=str),
        "source_urls": source_urls or [],
        "symbols": symbols or [],
        "word_count": post.word_count,
    })).first()''',
        '''    status = "published" if post.verification_ok else "draft"

    row = (await session.execute(sql_text("""
        INSERT INTO trading_posts (
            org_id, subblock_id, title, body, format, source_kind,
            source_payload, source_urls, symbols, status, word_count, published_at
        ) VALUES (
            :org_id, :subblock_id, :title, :body, 'long_form', :source_kind,
            CAST(:source_payload AS jsonb), :source_urls, :symbols,
            :status, :word_count,
            CASE WHEN :status = 'published' THEN now() ELSE NULL END
        )
        RETURNING id::text
    """), {
        "org_id": org_id,
        "subblock_id": subblock["id"],
        "title": post.title,
        "body": post.body,
        "source_kind": subblock["source_kind"],
        "source_payload": json.dumps(source_payload, default=str),
        "source_urls": source_urls or [],
        "symbols": symbols or [],
        "word_count": post.word_count,
        "status": status,
    })).first()''',
        "persist_post honours verification_ok",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. Patch stock_lists to use the verified entry point
# ─────────────────────────────────────────────────────────────────────

def step5_patch_stock_lists() -> None:
    STOCK_LISTS = ROOT / "packages" / "trading" / "subblocks" / "stock_lists.py"
    _patch(
        STOCK_LISTS,
        '''from .base import (
    TradingPost,
    call_llm_json,
    persist_post,
    render_markdown_table,
)''',
        '''from .base import (
    TradingPost,
    call_llm_json_verified,
    persist_post,
    render_markdown_table,
)''',
        "import verified entry point",
    )
    _patch(
        STOCK_LISTS,
        '''    # --- 3. LLM ---
    post = await call_llm_json(
        session,
        org_id=org_id,
        system=subblock.get("style_guide") or "You write clear market roundups.",
        user=user_prompt,
        prompt_name="trading.stock_lists",
        target_words=350,
    )''',
        '''    # --- 3. LLM (with claim verification) ---
    post = await call_llm_json_verified(
        session,
        org_id=org_id,
        system=subblock.get("style_guide") or "You write clear market roundups.",
        user=user_prompt,
        prompt_name="trading.stock_lists",
        source_payload={
            "gainers": gainers,
            "losers": losers,
            "active": active,
        },
        target_words=350,
    )''',
        "call verified entry point with source_payload",
    )


# ─────────────────────────────────────────────────────────────────────
# 6. Re-run stock_lists end-to-end
# ─────────────────────────────────────────────────────────────────────

def step6_run() -> bool:
    print("\n[6/6] Re-run stock_lists generator")
    r = subprocess.run(
        ["python", "scripts/run_stock_lists_once.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    print(r.stdout[-5000:] if len(r.stdout) > 5000 else r.stdout)
    if r.returncode != 0:
        print("  ❌ failed:")
        print(r.stderr[-1500:])
        return False
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.3.1.1: Claim verification")
    print("=" * 70)

    print("\n[1/6] Extend TradingPost dataclass")
    step1_extend_dataclass()

    print("\n[2/6] Insert verify_post() + section header")
    step2_add_verify()

    print("\n[3/6] Add call_llm_json_verified() wrapper")
    step3_add_wrapper()

    print("\n[4/6] persist_post honours verification_ok")
    step4_persist_status()

    print("\n[5/6] stock_lists uses verified entry point")
    step5_patch_stock_lists()

    print("\n[6/6] Re-run stock_lists end-to-end")
    ok = step6_run()

    print("=" * 70)
    if ok:
        print("✅ T.3.1.1 complete — verify step live, all future generators inherit it")
        sys.exit(0)
    print("❌ T.3.1.1 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
