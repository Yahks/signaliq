"""
SignalIQ — Phase T.7.1: Fix Gemini text extraction.

The current fallback does:
    response.candidates[0].content.parts[0].text

When Gemini returns a response with no .text property (safety filter,
thinking-only response, or empty completion), candidates can be None,
and the indexing crashes with a confusing 'NoneType' error.

Fix: robust extraction that surfaces the real reason for empty output.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEMINI = ROOT / "packages" / "ai" / "providers" / "gemini_provider.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
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
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


def step1_fix_extraction() -> None:
    print("\n[1/3] Harden Gemini text extraction")

    _patch_and_verify(
        GEMINI,
        '''        text = getattr(response, "text", None)
        if not text:
            try:
                text = response.candidates[0].content.parts[0].text
            except Exception as exc:
                raise ProviderError(f"Gemini returned no text: {exc}") from exc''',
        '''        # Robust text extraction. Gemini 3.x may return:
        #   - A valid .text property (normal case)
        #   - candidates with only thought parts (no text)
        #   - candidates == None (safety block)
        #   - candidates[0].content == None (partial block)
        text = getattr(response, "text", None)
        if not text:
            text = ""
            cands = getattr(response, "candidates", None) or []
            for cand in cands:
                content = getattr(cand, "content", None)
                if content is None:
                    continue
                for part in (getattr(content, "parts", None) or []):
                    t = getattr(part, "text", None)
                    if t:
                        text += t
            if not text:
                # Surface the real reason — do NOT mask it
                finish = "unknown"
                if cands:
                    finish = str(getattr(cands[0], "finish_reason", "unknown"))
                prompt_feedback = getattr(response, "prompt_feedback", None)
                raise ProviderError(
                    f"Gemini returned no text "
                    f"(finish_reason={finish}, "
                    f"prompt_feedback={prompt_feedback})"
                )''',
        "robust extraction",
    )


def step2_re_run_rotation_test() -> bool:
    print("\n[2/3] Re-test provider rotation")

    def run_sql(sql: str) -> tuple[int, str, str]:
        r = subprocess.run(
            ["docker", "exec", "-i", "signaliq-postgres-1",
             "psql", "-U", "signaliq", "-d", "signaliq", "-v", "ON_ERROR_STOP=1"],
            input=sql, capture_output=True, text=True,
        )
        return r.returncode, r.stdout or "", r.stderr or ""

    def complete(question: str) -> tuple[bool, str]:
        r = subprocess.run([
            "python", "-c", f"""
import asyncio, sys
sys.path.insert(0, '.')
from packages.ai.router import complete

async def main():
    try:
        text = await complete(
            role='reasoning',
            system='Answer in one short sentence.',
            user='{question}',
            purpose='t7.rotation_test',
            temperature=0.0,
            max_tokens=80,
            use_cache=False,
        )
        print(f'OK: {{text[:100]}}')
    except Exception as e:
        print(f'ERR: {{type(e).__name__}}: {{str(e)[:300]}}')

asyncio.run(main())
            """.strip()
        ], cwd=".", capture_output=True, text=True, timeout=90)
        out = r.stdout.strip()
        # print the last non-log line
        for line in reversed(out.splitlines()):
            if line.startswith("OK:") or line.startswith("ERR:"):
                return line.startswith("OK:"), line
        return False, out

    print("  Test A — normal call (Groq primary)")
    ok_a, out_a = complete("What is 2+2?")
    print(f"    {out_a}")

    print()
    print("  Test B — Groq deactivated (Gemini fallback)")
    rc, _, _ = run_sql(
        "UPDATE ai_models SET is_active=false "
        "WHERE role='reasoning' AND provider='groq';"
    )
    print(f"    Groq deactivated (rc={rc})")
    try:
        ok_b, out_b = complete("What is 3+3?")
        print(f"    {out_b}")
    finally:
        rc2, _, _ = run_sql(
            "UPDATE ai_models SET is_active=true "
            "WHERE role='reasoning' AND provider='groq';"
        )
        print(f"    Groq reactivated (rc={rc2})")

    print()
    print(f"  Result: Test A {'✓' if ok_a else '❌'}  |  Test B {'✓' if ok_b else '❌'}")
    return ok_a and ok_b


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.7.1: Fix Gemini text extraction")
    print("=" * 70)

    step1_fix_extraction()
    ok = step2_re_run_rotation_test()

    print("=" * 70)
    if ok:
        print("✅ T.7.1 complete — provider rotation live")
        sys.exit(0)
    print("❌ T.7.1 incomplete — see the error above")
    print()
    print("If Test B still fails, paste the ERR line — that's the real reason")
    print("(safety block, empty candidates, or Gemini API shape change).")
    sys.exit(1)


if __name__ == "__main__":
    main()
