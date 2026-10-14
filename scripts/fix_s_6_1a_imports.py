"""S.6.1a.1: add the missing render_with_version imports.

Step 4 of S.6.1a upgraded every render() call site to render_with_version()
but didn't update the imports. Any chain build now fails with
NameError: name 'render_with_version' is not defined.

This script finds every file that references render_with_version without
importing it, adds the import next to the existing loader import (or as a
new line), verifies each file parses, then runs one in-process chain build
to confirm prompt_version_id is now populated.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Every file that now calls render_with_version
CANDIDATES = [
    ROOT / "packages" / "ai" / "chain" / "chain_builder.py",
    ROOT / "packages" / "ai" / "chain" / "chain_linker.py",
    ROOT / "packages" / "ai" / "chain" / "narrator.py",
    ROOT / "packages" / "ai" / "chain" / "verifier.py",
    ROOT / "packages" / "ai" / "chain" / "reattach.py",
    ROOT / "packages" / "ai" / "chain" / "living.py",
    ROOT / "packages" / "ai" / "brief" / "generator.py",
    ROOT / "packages" / "ai" / "enrichment" / "pipeline.py",
    ROOT / "packages" / "ingestion" / "sources" / "stock_to_events.py",
    ROOT / "apps" / "worker" / "tasks" / "cluster.py",
    ROOT / "packages" / "trading" / "subblocks" / "base.py",
]


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


# Match:  from packages.ai.prompts.loader import <names>
IMPORT_RE = re.compile(
    r"from packages\.ai\.prompts\.loader import ([^\n]+)"
)


def fix_file(path: Path) -> str:
    """Returns 'fixed', 'already', or 'no-call'."""
    if not path.exists():
        return "missing"

    src = path.read_text()
    if "render_with_version" not in src:
        return "no-call"

    # Already imported?
    m = IMPORT_RE.search(src)
    if m and "render_with_version" in m.group(1):
        return "already"

    if m:
        # Extend the existing import
        old_line = m.group(0)
        names = m.group(1).strip()
        new_line = (
            f"from packages.ai.prompts.loader import "
            f"render, render_with_version"
            if "render" in names
            else f"from packages.ai.prompts.loader import {names}, render_with_version"
        )
        src = src.replace(old_line, new_line, 1)
        path.write_text(src)
        return "fixed"

    # No existing import — add one after the last `from packages...` import,
    # or after the module docstring if there are no such imports.
    lines = src.splitlines()
    insert_at = None
    for i, line in enumerate(lines):
        if line.startswith("from packages.") or line.startswith("import packages."):
            insert_at = i + 1
        elif line.startswith("from apps.") or line.startswith("import apps."):
            insert_at = i + 1
    if insert_at is None:
        # Fall back to just after the docstring
        in_doc = False
        for i, line in enumerate(lines):
            if line.strip().startswith('"""') and not in_doc:
                in_doc = True
                continue
            if in_doc and line.strip().endswith('"""'):
                insert_at = i + 1
                break
    if insert_at is None:
        fail(f"{path.name}: cannot find a place to insert the import")

    new_import = "from packages.ai.prompts.loader import render_with_version"
    lines.insert(insert_at, new_import)
    path.write_text("\n".join(lines) + ("\n" if not src.endswith("\n") else ""))
    return "fixed"


def step1_fix_imports() -> None:
    print("\n[1/3] fix missing imports")
    for p in CANDIDATES:
        result = fix_file(p)
        icon = {"fixed": "✓", "already": "↷", "no-call": "·", "missing": "!"}[result]
        print(f"  {icon} {p.name}: {result}")


def step2_syntax() -> None:
    print("\n[2/3] syntax check")
    for p in CANDIDATES:
        if not p.exists():
            continue
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{p}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            fail(f"{p.name}: {r.stderr[:400]}")
    print("  ✓ all files parse")


def step3_verify() -> bool:
    print("\n[3/3] in-process chain build → check prompt_version_id delta")
    code = r'''
import asyncio, json, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession,
)
from packages.core.config import get_settings

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    F = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    from packages.db.session import get_owner_session_factory
    OF = get_owner_session_factory()

    async with OF() as s:
        row = (await s.execute(text("""
            SELECT o.id::text FROM orgs o
            JOIN memberships m ON m.org_id = o.id
            JOIN users u ON u.id = m.user_id
            WHERE u.username = 'tokentest' LIMIT 1
        """))).first()
    oid = row[0]

    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        before = (await s.execute(text(
            "SELECT COUNT(*) FROM ai_calls WHERE prompt_version_id IS NOT NULL"
        ))).scalar()
    print(f"  before: {before}")

    from apps.worker.tasks.news_chains import _build_chain_async
    events = [
        {"_placeholder_id": "e1", "external_id": "e1",
         "headline": "Fed signals no rate cut",
         "summary": "Powell said rates will stay higher for longer.",
         "occurred_at": "2026-09-15T12:00:00Z", "reported_at": None, "location": None,
         "actors": ["Fed", "Powell"], "event_type": "statement", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e2", "external_id": "e2",
         "headline": "10-year yield hits 5%",
         "summary": "Treasury yields jumped on the Fed statement.",
         "occurred_at": "2026-09-15T14:00:00Z", "reported_at": None, "location": None,
         "actors": ["Treasury"], "event_type": "price_move", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e3", "external_id": "e3",
         "headline": "S&P 500 drops 2%",
         "summary": "Equities sold off sharply after yields rose.",
         "occurred_at": "2026-09-15T20:00:00Z", "reported_at": None, "location": None,
         "actors": ["S&P 500"], "event_type": "price_move", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e4", "external_id": "e4",
         "headline": "Dollar strengthens against yen",
         "summary": "Higher yields supported the dollar.",
         "occurred_at": "2026-09-16T08:00:00Z", "reported_at": None, "location": None,
         "actors": ["USD", "JPY"], "event_type": "price_move", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
    ]
    print("  building...")
    result = await _build_chain_async(oid, events, "news", None, 800)
    print(f"  result: {json.dumps(result, default=str)[:250]}")

    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        after = (await s.execute(text(
            "SELECT COUNT(*) FROM ai_calls WHERE prompt_version_id IS NOT NULL"
        ))).scalar()
        rows = (await s.execute(text("""
            SELECT purpose, prompt_version_id::text, status
            FROM ai_calls WHERE prompt_version_id IS NOT NULL
            ORDER BY created_at DESC LIMIT 10
        """))).all()

    print(f"  after:  {after}")
    print(f"  delta:  {after - before}")
    for r in rows:
        print(f"    {r[0]:30}  pv={r[1][:8]}…  {r[2]}")

    await engine.dispose()

asyncio.run(main())
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=180,
    )
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False

    return "delta:  0" not in r.stdout and "delta: 0" not in r.stdout


def main() -> None:
    print("=" * 70)
    print("S.6.1a.1 — add missing render_with_version imports")
    print("=" * 70)

    step1_fix_imports()
    step2_syntax()
    ok = step3_verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.1a.1 complete — imports fixed, prompt_version_id populated")
        sys.exit(0)
    print("❌ still failing — see output above")
    sys.exit(1)


if __name__ == "__main__":
    main()
