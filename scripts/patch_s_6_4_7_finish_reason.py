"""S.6.4.7 — surface finish_reason; never cache a truncated response.

The root cause of the empty-body failures:

  gpt-oss-20b is a reasoning model. Its max_tokens budget covers the
  hidden reasoning trace plus the visible content. At max_tokens=3000,
  a 260-word target often consumed the whole budget on reasoning, so
  the visible content came back empty and finish_reason='length'. The
  provider discarded finish_reason; the router cached the empty string
  as status='ok'; every subsequent call for the same prompt hit the
  poisoned cache and returned empty again.

  75 such rows were found in ai_calls, all status='ok', all with
  response_payload->>'text' = ''.

The immediate unblock (raise max_tokens 3000->6000, mark the 75 rows
'unusable') has been applied. This commit fixes the underlying bugs so
the cache cannot be poisoned again:

  1. CompletionResult gains finish_reason. (The duplicate rate_headers
     field is also removed — it was declared twice.)
  2. All four providers populate finish_reason from the API response.
  3. _try_with_credential refuses to cache a response when
     finish_reason == 'length' OR text.strip() == ''. Those rows are
     written with status='truncated' instead of 'ok'. The cache-lookup
     query already filters status='ok', so a truncated row is never
     served as a cached response.
  4. The 6000 in base.py:443 becomes a named constant
     _MAX_TOKENS_LONG, near _MIN_WORDS_RATIO.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_PROVIDER = ROOT / "packages" / "ai" / "providers" / "base.py"
GROQ = ROOT / "packages" / "ai" / "providers" / "groq_provider.py"
OPENROUTER = ROOT / "packages" / "ai" / "providers" / "openrouter_provider.py"
GEMINI = ROOT / "packages" / "ai" / "providers" / "gemini_provider.py"
OLLAMA = ROOT / "packages" / "ai" / "providers" / "ollama_provider.py"
ROUTER = ROOT / "packages" / "ai" / "router.py"
SUBBASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"

MARK = "S.6.4.7"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def apply_once(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src:
        print(f"  ↷ {label}: already applied")
        return
    if old not in src:
        fail(f"{label}: pattern not found in {path.name}\n"
             f"--- expected (first 500 chars) ---\n{old[:500]}")
    path.write_text(src.replace(old, new, 1))
    try:
        ast.parse(path.read_text())
    except SyntaxError as e:
        fail(f"{path.name}: syntax error after '{label}': {e}")
    print(f"  ✓ {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. CompletionResult — remove duplicate, add finish_reason
# ─────────────────────────────────────────────────────────────────────

def step1_completion_result() -> None:
    print("\n[1/6] providers/base.py — CompletionResult")
    apply_once(
        BASE_PROVIDER,
        '''@dataclass
class CompletionResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    # S.6.4.3 — provider rate-limit headers when available. Groq returns
    # x-ratelimit-* on every response; we store them so the UI can show
    # the user's remaining quota per key. None for providers that don't.
    rate_headers: dict[str, str] | None = None
    # S.6.4.3 — provider rate-limit headers when available. Groq returns
    # x-ratelimit-* on every response; we store them so the UI can show
    # the user's remaining quota per key. None for providers that don't.
    rate_headers: dict[str, str] | None = None''',
        '''@dataclass
class CompletionResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    # S.6.4.3 — provider rate-limit headers when available. Groq returns
    # x-ratelimit-* on every response; we store them so the UI can show
    # the user's remaining quota per key. None for providers that don't.
    rate_headers: dict[str, str] | None = None
    # S.6.4.7 — why the model stopped. 'stop' = complete, 'length' =
    # hit max_tokens (truncated), others are provider-specific. The
    # router refuses to cache a response with finish_reason='length'
    # or with empty text; those are written as status='truncated' so
    # the cache-lookup (which filters status='ok') never serves them.
    finish_reason: str | None = None''',
        "CompletionResult: drop dup rate_headers, add finish_reason",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Groq provider — populate finish_reason
# ─────────────────────────────────────────────────────────────────────

def step2_groq() -> None:
    print("\n[2/6] groq_provider.py — populate finish_reason")
    apply_once(
        GROQ,
        '''        data = r.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Groq malformed response: {data}") from exc

        usage = data.get("usage") or {}
        rate_headers = {
            k.lower(): v for k, v in r.headers.items()
            if k.lower().startswith("x-ratelimit-")
        }
        return CompletionResult(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            raw={},
            rate_headers=rate_headers or None,
        )''',
        '''        data = r.json()
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Groq malformed response: {data}") from exc

        usage = data.get("usage") or {}
        rate_headers = {
            k.lower(): v for k, v in r.headers.items()
            if k.lower().startswith("x-ratelimit-")
        }
        return CompletionResult(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            raw={},
            rate_headers=rate_headers or None,
            finish_reason=finish_reason,
        )''',
        "Groq: read choice.finish_reason",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. OpenRouter provider — populate finish_reason
# ─────────────────────────────────────────────────────────────────────

def step3_openrouter() -> None:
    print("\n[3/6] openrouter_provider.py — populate finish_reason")
    src = OPENROUTER.read_text()
    if "finish_reason=finish_reason" in src:
        print("  ↷ already applied")
        return

    # Find the CompletionResult construction and the line that reads
    # data["choices"][0]["message"]["content"].
    old_extract = '        data = r.json()\n'
    if old_extract not in src:
        fail("openrouter: r.json() anchor not found")
    # Insert a finish_reason read on the next line after the text
    # extraction. We look for the exact substring around text=.
    old_block = (
        '        data = r.json()\n'
    )
    # Simpler approach — find the CompletionResult(...) call and add
    # finish_reason= to it, reading data["choices"][0].get("finish_reason").
    idx = src.find("data = r.json()")
    if idx < 0:
        fail("openrouter: data = r.json() not found")
    # After r.json(), find the text = ... line.
    text_idx = src.find('text = data["choices"][0]["message"]["content"]', idx)
    if text_idx < 0:
        fail("openrouter: text extraction line not found")
    # Insert a finish_reason read right after that line.
    insert_at = src.find("\n", text_idx) + 1
    insert = '        finish_reason = data["choices"][0].get("finish_reason")\n'
    src = src[:insert_at] + insert + src[insert_at:]
    # Now add finish_reason= to CompletionResult — find the last
    # CompletionResult( in the file (there is only one in complete()).
    cr_idx = src.rfind("CompletionResult(")
    if cr_idx < 0:
        fail("openrouter: CompletionResult( not found")
    # Find the matching close paren.
    open_paren = cr_idx + len("CompletionResult(") - 1
    depth = 0
    i = open_paren
    while i < len(src):
        c = src[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth != 0:
        fail("openrouter: could not find matching close paren")
    # Insert "finish_reason=finish_reason, " before the close paren.
    src = src[:i] + "            finish_reason=finish_reason,\n        " + src[i:]
    OPENROUTER.write_text(src)
    try:
        ast.parse(src)
    except SyntaxError as e:
        fail(f"openrouter: syntax error: {e}")
    print("  ✓ OpenRouter: read choice.finish_reason")


# ─────────────────────────────────────────────────────────────────────
# 4. Gemini provider — populate finish_reason
# ─────────────────────────────────────────────────────────────────────

def step4_gemini() -> None:
    print("\n[4/6] gemini_provider.py — populate finish_reason")
    apply_once(
        GEMINI,
        '''        return CompletionResult(text=text, input_tokens=in_tok, output_tokens=out_tok, raw={})''',
        '''        return CompletionResult(
            text=text, input_tokens=in_tok, output_tokens=out_tok, raw={},
            finish_reason=str(getattr(cands[0], "finish_reason", None)) if cands else None,
        )''',
        "Gemini: pass cands[0].finish_reason",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. Ollama provider — populate finish_reason
# ─────────────────────────────────────────────────────────────────────

def step5_ollama() -> None:
    print("\n[5/6] ollama_provider.py — populate finish_reason")
    src = OLLAMA.read_text()
    if "finish_reason=" in src and "S.6.4.7" in src:
        print("  ↷ already applied")
        return
    if "finish_reason=" in src:
        print("  ↷ already applied")
        return
    idx = src.find("data = r.json()")
    if idx < 0:
        fail("ollama: data = r.json() not found")
    # Find the CompletionResult( after that.
    cr_idx = src.find("CompletionResult(", idx)
    if cr_idx < 0:
        fail("ollama: CompletionResult( after r.json() not found")
    open_paren = cr_idx + len("CompletionResult(") - 1
    depth = 0
    i = open_paren
    while i < len(src):
        c = src[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    src = src[:i] + "            finish_reason=data.get(\"done_reason\"),\n        " + src[i:]
    OLLAMA.write_text(src)
    try:
        ast.parse(src)
    except SyntaxError as e:
        fail(f"ollama: syntax error: {e}")
    print("  ✓ Ollama: pass done_reason")


# ─────────────────────────────────────────────────────────────────────
# 6. Router — never cache truncated or empty responses
# ─────────────────────────────────────────────────────────────────────

def step6_router() -> None:
    print("\n[6/6] router.py — refuse to cache truncated/empty responses")
    apply_once(
        ROUTER,
        '''        status = "ok"
    except ProviderError as exc:
        err_msg = str(exc)[:1000]
        status = "error"''',
        '''        # S.6.4.7 — a response is only "ok" if it's complete and
        # non-empty. finish_reason='length' means the model hit
        # max_tokens and was truncated; an empty text means nothing
        # was produced. Both are uncacheable: the cache lookup filters
        # status='ok', so a truncated row will never be served as a
        # cached response, and the next call will retry fresh.
        _text = (result.text or "").strip() if result else ""
        _truncated = bool(result) and getattr(result, "finish_reason", None) == "length"
        if result and (_truncated or not _text):
            status = "truncated"
            log.warning(
                "ai.truncated",
                purpose=purpose,
                provider=provider_name,
                model=model_name,
                in_tokens=result.input_tokens,
                out_tokens=result.output_tokens,
                max_tokens=max_tokens,
                finish_reason=getattr(result, "finish_reason", None),
                text_len=len(_text),
            )
        else:
            status = "ok"
    except ProviderError as exc:
        err_msg = str(exc)[:1000]
        status = "error"''',
        "router: set status='truncated' on truncation/empty",
    )


# ─────────────────────────────────────────────────────────────────────
# 7. subblocks/base.py — name the max-tokens constant
# ─────────────────────────────────────────────────────────────────────

def step7_subblock_constant() -> None:
    print("\n[7/7] subblocks/base.py — name the 6000 constant")
    apply_once(
        SUBBASE,
        '''# S.6.4.6 — minimum acceptable length as a fraction of target_words.
# A 260-word target refuses anything under 130 words; a 150-word target
# refuses anything under 75. This catches refusals like
# "requires inspection of SEC filings" before verification, which would
# otherwise see a single true claim and accept the post.
_MIN_WORDS_RATIO = 0.5''',
        '''# S.6.4.6 — minimum acceptable length as a fraction of target_words.
# A 260-word target refuses anything under 130 words; a 150-word target
# refuses anything under 75. This catches refusals like
# "requires inspection of SEC filings" before verification, which would
# otherwise see a single true claim and accept the post.
_MIN_WORDS_RATIO = 0.5

# S.6.4.7 — output token budget for reasoning-model subblock generation.
# gpt-oss-20b is a reasoning model: max_tokens covers the hidden
# reasoning trace AND the visible content. At 3000, a 350-word target
# often consumed the whole budget on reasoning and returned empty
# content with finish_reason='length'. 6000 gives headroom for the
# reasoning trace plus the post. Do NOT lower this without checking
# the observed out_tokens of the longest subblock (class_room ~4600).
_MAX_TOKENS_LONG = 6000''',
        "add _MAX_TOKENS_LONG constant",
    )
    apply_once(
        SUBBASE,
        '''        max_tokens=6000,''',
        '''        max_tokens=_MAX_TOKENS_LONG,''',
        "use the constant in the call",
    )


def main() -> None:
    print("=" * 70)
    print("S.6.4.7 — surface finish_reason; never cache a truncated response")
    print("=" * 70)
    step1_completion_result()
    step2_groq()
    step3_openrouter()
    step4_gemini()
    step5_ollama()
    step6_router()
    step7_subblock_constant()
    print()
    print("=" * 70)
    print("Done. Truncated responses can no longer poison the cache.")
    print("=" * 70)


if __name__ == "__main__":
    main()
