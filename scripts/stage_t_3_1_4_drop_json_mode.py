"""
SignalIQ — Phase T.3.1.4: Drop JSON mode. Use a text protocol.

Root cause of T.3.1.2/.3 failures:
  Groq's response_format={"type":"json_object"} imposes a much lower
  output ceiling than max_tokens. Long responses get truncated mid-JSON
  and rejected with HTTP 400 json_validate_failed.

Fix:
  - call_llm_json:  request "TITLE: ...\nBODY:\n..." instead of JSON
  - verify_post:    request "UNSUPPORTED: 1, 3, 7" instead of JSON
  - stock_lists:    target_words 350 → 150 to match the tightened prompt

Parsing is 5 lines each. No schema enforcement needed for 1-2 fields.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"
STOCK_LISTS = ROOT / "packages" / "trading" / "subblocks" / "stock_lists.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
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
    check = path.read_text()
    if new not in check:
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new text not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. New text-protocol parser helpers (insert before call_llm_json)
# ─────────────────────────────────────────────────────────────────────

def step1_add_text_parsers() -> None:
    print("\n[1/4] Add text-protocol parser helpers")

    _patch_and_verify(
        BASE,
        '''def _extract_json_block(text: str) -> str:
    """Pull the first balanced JSON object out of a possibly-chatty response."""
    text = text.strip()
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\\s*", "", text)
    text = re.sub(r"\\s*```\\s*$", "", text)
    # Find the first { ... } balanced block
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("unbalanced JSON")''',
        '''def _extract_json_block(text: str) -> str:
    """Pull the first balanced JSON object out of a possibly-chatty response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\\s*", "", text)
    text = re.sub(r"\\s*```\\s*$", "", text)
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("unbalanced JSON")


# ─────────────────────────────────────────────────────────────────────
# Text-protocol parsers (avoid Groq's JSON-mode token cap)
# ─────────────────────────────────────────────────────────────────────

_TITLE_RE = re.compile(r"^\\s*TITLE\\s*:\\s*(.+?)\\s*$", re.IGNORECASE | re.MULTILINE)
_BODY_RE = re.compile(r"^\\s*BODY\\s*:\\s*$", re.IGNORECASE | re.MULTILINE)


def _parse_title_body(text: str) -> tuple[str, str]:
    """Parse a 'TITLE: ...\\nBODY:\\n...' response.

    Fallbacks:
      - No TITLE: use the first non-empty line
      - No BODY: everything after the first blank line is the body
      - Neither: use the whole text as body with a generic title
    """
    text = text.strip()
    # Strip markdown fences if the model wrapped everything
    text = re.sub(r"^```(?:markdown|text)?\\s*", "", text)
    text = re.sub(r"\\s*```\\s*$", "", text)

    body_match = _BODY_RE.search(text)
    title_match = _TITLE_RE.search(text)

    if title_match and body_match and body_match.start() > title_match.end():
        title = title_match.group(1).strip().strip('"').strip("'")
        body = text[body_match.end():].lstrip("\\n").strip()
        return title or "Untitled", body

    # Fallback: first line = title, rest = body
    lines = text.split("\\n")
    if len(lines) >= 2 and lines[0].strip():
        title = lines[0].strip().lstrip("#").strip()
        # Find first blank line to split heading from body
        try:
            split_at = next(i for i in range(1, len(lines)) if not lines[i].strip())
            body = "\\n".join(lines[split_at + 1:]).strip()
        except StopIteration:
            body = "\\n".join(lines[1:]).strip()
        return title[:500] or "Untitled", body

    return "Untitled", text


def _parse_unsupported_nums(text: str) -> list[int]:
    """Parse 'UNSUPPORTED: 1, 3, 7' or 'UNSUPPORTED: NONE'.

    Accepts comma-separated, space-separated, or bracketed lists.
    Returns [] for NONE.
    """
    text = text.strip()
    m = re.search(r"UNSUPPORTED\\s*:\\s*(.+?)(?:\\n|$)", text, re.IGNORECASE)
    if not m:
        # Fallback: any digit groups in the text
        nums = re.findall(r"\\b(\\d+)\\b", text)
        return [int(n) for n in nums]
    payload = m.group(1).strip()
    if not payload or payload.upper().startswith("NONE") or payload.upper().startswith("NO "):
        return []
    nums = re.findall(r"\\b(\\d+)\\b", payload)
    return [int(n) for n in nums]''',
        "_parse_title_body + _parse_unsupported_nums helpers",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Rewrite call_llm_json to use text protocol
# ─────────────────────────────────────────────────────────────────────

def step2_rewrite_call_llm_json() -> None:
    print("\n[2/4] Rewrite call_llm_json to text protocol")

    _patch_and_verify(
        BASE,
        '''    full_system = (
        f"{system}\\n\\n"
        f"RULES:\\n"
        f"- Output MUST be valid JSON: {{\\"title\\": \\"...\\", \\"body\\": \\"...\\"}}\\n"
        f"- The body is markdown. Use tables, headings, and short paragraphs.\\n"
        f"- Target length: ~{target_words} words.\\n"
        f"- Use ONLY numbers and facts present in the input data.\\n"
        f"- Do not invent tickers, prices, percentages, or dates.\\n"
        f"- If the data looks degraded or empty, say so plainly rather than fabricating."
    )
    full_user = f"{user}\\n\\nReturn valid JSON with 'title' and 'body' fields."

    log.info("subblock.llm_call_start", prompt=prompt_name, words=target_words)

    # router.complete() returns the raw text (str), not a result object.
    # json_mode=True makes the provider constrain output to valid JSON.
    text = await complete(
        role="reasoning",
        system=full_system,
        user=full_user,
        org_id=org_id,
        purpose=prompt_name,
        temperature=0.3,
        max_tokens=3000,
        json_mode=True,
    )
    text = (text or "").strip()
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
        '''    full_system = (
        f"{system}\\n\\n"
        f"OUTPUT FORMAT (strict):\\n"
        f"TITLE: <one line — the post title>\\n"
        f"BODY:\\n"
        f"<markdown body — tables, headings, short paragraphs>\\n"
        f"\\n"
        f"RULES:\\n"
        f"- Start with the literal line 'TITLE: ' (nothing before it).\\n"
        f"- Then a line with exactly 'BODY:' (nothing else on the line).\\n"
        f"- The body is markdown. Use tables, headings, and short paragraphs.\\n"
        f"- Target length: ~{target_words} words.\\n"
        f"- Use ONLY numbers and facts present in the input data.\\n"
        f"- Do not invent tickers, prices, percentages, or dates.\\n"
        f"- If the data looks degraded or empty, say so plainly rather than fabricating."
    )
    full_user = (
        f"{user}\\n\\n"
        f"Respond using ONLY this format:\\n"
        f"TITLE: <one line>\\n"
        f"BODY:\\n"
        f"<markdown>"
    )

    log.info("subblock.llm_call_start", prompt=prompt_name, words=target_words)

    # Text protocol — avoids Groq's JSON-mode token cap that truncated
    # long responses and failed with json_validate_failed.
    text = await complete(
        role="reasoning",
        system=full_system,
        user=full_user,
        org_id=org_id,
        purpose=prompt_name,
        temperature=0.3,
        max_tokens=3000,
        json_mode=False,
    )
    text = (text or "").strip()

    try:
        title, body = _parse_title_body(text)
    except Exception as exc:
        log.warning("subblock.llm_parse_failed", error=str(exc)[:200], raw=text[:400])
        return TradingPost(
            title=prompt_name.replace(".", " ").title(),
            body=text or "(empty response)",
            word_count=len((text or "").split()),
        )

    if not body:
        log.warning("subblock.llm_empty_body", raw=text[:200])
        body = text or "(empty response)"

    return TradingPost(
        title=(title or "Untitled")[:500],
        body=body,
        word_count=len(body.split()),
    )''',
        "call_llm_json — text protocol, json_mode=False",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Rewrite verify_post to text protocol
# ─────────────────────────────────────────────────────────────────────

def step3_rewrite_verify() -> None:
    print("\n[3/4] Rewrite verify_post to text protocol")

    _patch_and_verify(
        BASE,
        '''    system = (
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
        return True, []''',
        '''    system = (
        "You are a fact-checking verifier. You will receive SOURCE DATA and "
        "a numbered list of CLAIMS. For each claim, decide whether it is "
        "supported by the source data.\\n"
        "\\n"
        "A claim is supported if:\\n"
        "- The number/ticker/name appears verbatim in the source data, OR\\n"
        "- The claim is a plain re-statement of a value in the source data, OR\\n"
        "- The claim is a generic, non-factual framing statement.\\n"
        "\\n"
        "A claim is NOT supported if it asserts a CAUSE, REASON, NEWS EVENT, "
        "or FACT that is not present in the source data — including invented "
        "rationale like 'due to a contract win' or 'after earnings'.\\n"
        "\\n"
        "OUTPUT FORMAT (strict):\\n"
        "UNSUPPORTED: <comma-separated claim numbers, e.g. '2, 5'>\\n"
        "If every claim is supported, output exactly: UNSUPPORTED: NONE\\n"
        "Do not include any other text."
    )
    user = (
        f"SOURCE DATA:\\n{payload_str}\\n\\n"
        f"CLAIMS:\\n{claims_block}\\n\\n"
        f"Reply only with 'UNSUPPORTED: <numbers>' or 'UNSUPPORTED: NONE'."
    )

    try:
        raw = await complete(
            role="reasoning",
            system=system,
            user=user,
            org_id=org_id,
            purpose="trading.verify",
            temperature=0.0,
            max_tokens=200,
            json_mode=False,
        )
        bad_nums = _parse_unsupported_nums(raw or "")
    except Exception as exc:
        log.warning("verify.failed_open", error=str(exc)[:200])
        return True, []''',
        "verify_post — text protocol, json_mode=False",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. Fix target_words in stock_lists
# ─────────────────────────────────────────────────────────────────────

def step4_fix_target_words() -> None:
    print("\n[4/4] Fix target_words in stock_lists")

    _patch_and_verify(
        STOCK_LISTS,
        '''        target_words=350,
        use_cache=False,   # TODO: remove after first verified run proves it works
    )''',
        '''        target_words=150,
        use_cache=False,   # TODO: remove after first verified run proves it works
    )''',
        "target_words 350 → 150",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. Re-run
# ─────────────────────────────────────────────────────────────────────

def step5_rerun() -> bool:
    print("\n[5/5] Re-run stock_lists generator")
    r = subprocess.run(
        ["python", "scripts/run_stock_lists_once.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    print(r.stdout[-6000:] if len(r.stdout) > 6000 else r.stdout)
    if r.returncode != 0:
        print("  ❌ failed:")
        print(r.stderr[-1500:])
        return False
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.3.1.4: Drop JSON mode → text protocol")
    print("=" * 70)

    step1_add_text_parsers()
    step2_rewrite_call_llm_json()
    step3_rewrite_verify()
    step4_fix_target_words()
    ok = step5_rerun()

    print("=" * 70)
    if ok:
        print("✅ T.3.1.4 complete — text protocol replaces Groq JSON mode")
        sys.exit(0)
    print("❌ T.3.1.4 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
