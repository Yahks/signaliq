"""
S.6.1a — wire prompt versions into the router.

15 call sites. 12 use render(), 3 build prompts inline and need seeded rows.
Every patch is per-file and verified before the next runs. Any mismatch stops
the script and names the file + expected pattern.

Success criterion: ai_calls.prompt_version_id goes from 0/2098 to populated
on every chain-pipeline call, every brief call, every enrichment call.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"

LOADER = ROOT / "packages" / "ai" / "prompts" / "loader.py"
ROUTER = ROOT / "packages" / "ai" / "router.py"
SEED_EXTRA = ROOT / "packages" / "ai" / "prompts" / "seed_chain_extra.py"

CHAIN_BUILDER = ROOT / "packages" / "ai" / "chain" / "chain_builder.py"
CHAIN_LINKER = ROOT / "packages" / "ai" / "chain" / "chain_linker.py"
NARRATOR = ROOT / "packages" / "ai" / "chain" / "narrator.py"
VERIFIER = ROOT / "packages" / "ai" / "chain" / "verifier.py"
REATTACH = ROOT / "packages" / "ai" / "chain" / "reattach.py"
LIVING = ROOT / "packages" / "ai" / "chain" / "living.py"
BRIEF = ROOT / "packages" / "ai" / "brief" / "generator.py"
ENRICH = ROOT / "packages" / "ai" / "enrichment" / "pipeline.py"
STOCK = ROOT / "packages" / "ingestion" / "sources" / "stock_to_events.py"
CLUSTER = ROOT / "apps" / "worker" / "tasks" / "cluster.py"
SUBBASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() and new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(f"{path.name}: pattern not found for '{label}'\n"
             f"--- expected (first 400 chars) ---\n{old[:400]}")
    src = src.replace(old, new, 1)
    path.write_text(src)
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found (silent failure)")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. loader.py — render_with_version
# ─────────────────────────────────────────────────────────────────────

def step1_loader() -> None:
    print("\n[1/9] loader.py — add render_with_version()")
    if "def render_with_version" in LOADER.read_text():
        print("  ↷ already present")
        return
    patch(
        LOADER,
        '''def render(name: str, *, block_slug: str | None = None, **kwargs: object) -> str:
    """Load a prompt template and substitute {{var}} placeholders."""
    template, _ = get_prompt(name, block_slug=block_slug)
    out = template
    for key, value in kwargs.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out''',
        '''def render(name: str, *, block_slug: str | None = None, **kwargs: object) -> str:
    """Load a prompt template and substitute {{var}} placeholders."""
    template, _ = get_prompt(name, block_slug=block_slug)
    out = template
    for key, value in kwargs.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


def render_with_version(
    name: str, *, block_slug: str | None = None, **kwargs: object
) -> tuple[str, str]:
    """Like render(), but also returns the prompt_version_id.

    Callers using this should pass the id to complete(prompt_version_id=_pv)
    so ai_calls.prompt_version_id gets populated.
    """
    template, version_id = get_prompt(name, block_slug=block_slug)
    out = template
    for key, value in kwargs.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out, version_id''',
        "add render_with_version",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. router.py — accept + write prompt_version_id
# ─────────────────────────────────────────────────────────────────────

def step2_router() -> None:
    print("\n[2/9] router.py — thread prompt_version_id")

    # complete() signature
    patch(
        ROUTER,
        '''async def complete(
    *,
    role: str,
    system: str | None,
    user: str,
    org_id: str | None = None,
    purpose: str = "generic",
    temperature: float = 0.0,
    max_tokens: int | None = None,
    json_mode: bool = False,
    use_cache: bool = True,
) -> str:''',
        '''async def complete(
    *,
    role: str,
    system: str | None,
    user: str,
    org_id: str | None = None,
    purpose: str = "generic",
    temperature: float = 0.0,
    max_tokens: int | None = None,
    json_mode: bool = False,
    use_cache: bool = True,
    prompt_version_id: str | None = None,
) -> str:''',
        "complete() signature",
    )

    # _try_complete signature — find the same block
    patch(
        ROUTER,
        '''async def _try_complete(
    *,
    session: Session,
    role: str,
    purpose: str,
    org_id: str | None,
    system: str | None,
    user: str,
    temperature: float,
    max_tokens: int | None,
    json_mode: bool,
    use_cache: bool,
    model_id: str,
    provider_name: str,
    model_name: str,
) -> str:''',
        '''async def _try_complete(
    *,
    session: Session,
    role: str,
    purpose: str,
    org_id: str | None,
    system: str | None,
    user: str,
    temperature: float,
    max_tokens: int | None,
    json_mode: bool,
    use_cache: bool,
    model_id: str,
    provider_name: str,
    model_name: str,
    prompt_version_id: str | None = None,
) -> str:''',
        "_try_complete() signature",
    )

    # _try_complete INSERT — add prompt_version_id column
    patch(
        ROUTER,
        '''    session.execute(
        sql_text("""
            INSERT INTO ai_calls (
                org_id, model_id, purpose, input_tokens, output_tokens,
                latency_ms, status, error, request_hash, response_payload
            ) VALUES (
                :org_id, :model_id, :purpose, :in_tok, :out_tok,
                :latency, :status, :error, :hash, CAST(:payload AS jsonb)
            )
        """),''',
        '''    session.execute(
        sql_text("""
            INSERT INTO ai_calls (
                org_id, model_id, prompt_version_id, purpose, input_tokens, output_tokens,
                latency_ms, status, error, request_hash, response_payload
            ) VALUES (
                :org_id, :model_id, :prompt_version_id, :purpose, :in_tok, :out_tok,
                :latency, :status, :error, :hash, CAST(:payload AS jsonb)
            )
        """),''',
        "_try_complete INSERT columns",
    )

    # _try_complete INSERT params dict
    patch(
        ROUTER,
        '''        {
            "org_id": org_id,
            "model_id": model_id,
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
        '''        {
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
        "_try_complete INSERT params",
    )

    # complete() → _try_complete() call — pass the kwarg through
    patch(
        ROUTER,
        '''                result = await _try_complete(
                    session=session,
                    role=role,
                    purpose=purpose,
                    org_id=org_id,
                    system=system,
                    user=user,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    use_cache=use_cache,
                    model_id=model_id,
                    provider_name=provider_name,
                    model_name=model_name,
                )''',
        '''                result = await _try_complete(
                    session=session,
                    role=role,
                    purpose=purpose,
                    org_id=org_id,
                    system=system,
                    user=user,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    use_cache=use_cache,
                    model_id=model_id,
                    provider_name=provider_name,
                    model_name=model_name,
                    prompt_version_id=prompt_version_id,
                )''',
        "complete() → _try_complete() passthrough",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. seed_chain_extra.py — the three relocated prompts
# ─────────────────────────────────────────────────────────────────────

SEED_SRC = '''"""Seed the three relocated prompts (S.6.1a).

These replace inline strings that existed in cluster.py and subblocks/base.py.
The template text is copied verbatim from those call sites — no behavior change.
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings

CLUSTER_TOPIC_LABEL = """You are a news analyst. Below are {{count}} examples from a cluster of related records.

Give the cluster a SHORT title (max 8 words) and a 1-sentence summary.

Return ONLY valid JSON: {"title": "...", "summary": "..."}

Examples:
{{examples}}

JSON:"""

TRADING_VERIFY = """SOURCE DATA:
{{payload_str}}

CLAIMS:
{{claims_block}}

Reply only with 'UNSUPPORTED: <numbers>' or 'UNSUPPORTED: NONE'."""

TRADING_SUBBLOCK_GENERATE = """{{user}}

Respond using ONLY this format:
TITLE: <one line>
BODY:
<markdown>"""


PROMPTS = [
    ("cluster.topic_label", "fast", CLUSTER_TOPIC_LABEL, ["count", "examples"]),
    ("trading.verify", "reasoning", TRADING_VERIFY, ["payload_str", "claims_block"]),
    ("trading.subblock_generate", "reasoning", TRADING_SUBBLOCK_GENERATE,
     ["user", "target_words"]),
]


def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        for name, role, template, variables in PROMPTS:
            session.execute(
                sql_text("UPDATE prompt_versions SET is_active = false WHERE name = :name"),
                {"name": name},
            )
            session.execute(
                sql_text("""
                    INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                    VALUES (:name, 1, :role, :template, CAST(:vars AS jsonb), true)
                    ON CONFLICT (name, version) DO UPDATE
                    SET template = EXCLUDED.template,
                        variables = EXCLUDED.variables,
                        role = EXCLUDED.role,
                        is_active = true
                """),
                {"name": name, "role": role, "template": template,
                 "vars": json.dumps(variables)},
            )
        session.commit()
        print(f"  seeded {len(PROMPTS)} prompts")


if __name__ == "__main__":
    seed()
'''


def step3_seed() -> None:
    print("\n[3/9] seed_chain_extra.py — three relocated prompts")
    SEED_EXTRA.write_text(SEED_SRC)
    print(f"  ✓ wrote {SEED_EXTRA.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 4. The 12 render() → render_with_version() upgrades
# ─────────────────────────────────────────────────────────────────────

RENDER_UPGRADES = [
    # (file, old_render_line, new_render_line, old_complete_anchor, new_complete_anchor, label)
    (
        CHAIN_BUILDER,
        '    prompt = render("chain.select", block_slug=block_slug, events=_format_events(events))\n'
        '    raw = await complete(\n'
        '        role="reasoning",\n'
        '        system=None,\n'
        '        user=prompt,\n'
        '        org_id=org_id,\n'
        '        purpose="chain.select",',
        '    prompt, _pv = render_with_version("chain.select", block_slug=block_slug, events=_format_events(events))\n'
        '    raw = await complete(\n'
        '        role="reasoning",\n'
        '        system=None,\n'
        '        user=prompt,\n'
        '        org_id=org_id,\n'
        '        prompt_version_id=_pv,\n'
        '        purpose="chain.select",',
        "chain_builder.py: chain.select",
    ),
    (
        CHAIN_BUILDER,
        '    prompt = render("chain.order", block_slug=block_slug, events=_format_events(chain_events))\n'
        '    raw = await complete(\n'
        '        role="reasoning",\n'
        '        system=None,\n'
        '        user=prompt,\n'
        '        org_id=org_id,\n'
        '        purpose="chain.order",',
        '    prompt, _pv = render_with_version("chain.order", block_slug=block_slug, events=_format_events(chain_events))\n'
        '    raw = await complete(\n'
        '        role="reasoning",\n'
        '        system=None,\n'
        '        user=prompt,\n'
        '        org_id=org_id,\n'
        '        prompt_version_id=_pv,\n'
        '        purpose="chain.order",',
        "chain_builder.py: chain.order",
    ),
    (
        CHAIN_LINKER,
        '    prompt = render("chain.link", block_slug=block_slug, pairs=_format_pairs(sequence, by_id))\n'
        '    raw = await complete(\n'
        '        role="reasoning",\n'
        '        system=None,\n'
        '        user=prompt,\n'
        '        org_id=org_id,\n'
        '        purpose="chain.link",',
        '    prompt, _pv = render_with_version("chain.link", block_slug=block_slug, pairs=_format_pairs(sequence, by_id))\n'
        '    raw = await complete(\n'
        '        role="reasoning",\n'
        '        system=None,\n'
        '        user=prompt,\n'
        '        org_id=org_id,\n'
        '        prompt_version_id=_pv,\n'
        '        purpose="chain.link",',
        "chain_linker.py: chain.link",
    ),
    (
        NARRATOR,
        '    prompt = render(\n'
        '        "chain.narrate",\n'
        '        block_slug=block.slug,\n'
        '        thesis=thesis or "(no explicit thesis)",\n'
        '        events_with_links=events_with_links,\n'
        '        target_words=str(words),\n'
        '        style_guide=block.style_guide or "(no style guide)",\n'
        '        example_output=block.example_output or "(no example)",\n'
        '    )\n'
        '\n'
        '    raw = await complete(\n'
        '        role="reasoning",\n'
        '        system=None,\n'
        '        user=prompt,\n'
        '        org_id=org_id,\n'
        '        purpose=f"chain.narrate.{block.slug}",',
        '    prompt, _pv = render_with_version(\n'
        '        "chain.narrate",\n'
        '        block_slug=block.slug,\n'
        '        thesis=thesis or "(no explicit thesis)",\n'
        '        events_with_links=events_with_links,\n'
        '        target_words=str(words),\n'
        '        style_guide=block.style_guide or "(no style guide)",\n'
        '        example_output=block.example_output or "(no example)",\n'
        '    )\n'
        '\n'
        '    raw = await complete(\n'
        '        role="reasoning",\n'
        '        system=None,\n'
        '        user=prompt,\n'
        '        org_id=org_id,\n'
        '        prompt_version_id=_pv,\n'
        '        purpose=f"chain.narrate.{block.slug}",',
        "narrator.py: chain.narrate",
    ),
    (
        VERIFIER,
        '    prompt = render(\n'
        '        "chain.verify",\n'
        '        block_slug=block_slug,\n'
        '        body=body[:8000],\n'
        '        evidence=_format_evidence(events),\n'
        '    )',
        '    prompt, _pv = render_with_version(\n'
        '        "chain.verify",\n'
        '        block_slug=block_slug,\n'
        '        body=body[:8000],\n'
        '        evidence=_format_evidence(events),\n'
        '    )',
        "verifier.py: render upgrade",
    ),
    (
        VERIFIER,
        '        role="fast",\n'
        '        system=None,\n'
        '        user=prompt,\n'
        '        org_id=org_id,\n'
        '        purpose=f"chain.verify.{block_slug or \'default\'}",',
        '        role="fast",\n'
        '        system=None,\n'
        '        user=prompt,\n'
        '        org_id=org_id,\n'
        '        prompt_version_id=_pv,\n'
        '        purpose=f"chain.verify.{block_slug or \'default\'}",',
        "verifier.py: complete kwarg",
    ),
    (
        REATTACH,
        '    prompt = render(\n'
        '        "chain.reattach",\n'
        '        block_slug="trading",',
        '    prompt, _pv = render_with_version(\n'
        '        "chain.reattach",\n'
        '        block_slug="trading",',
        "reattach.py: render upgrade",
    ),
    (
        REATTACH,
        '        raw = await complete(\n'
        '            role="fast",  # bounded decision — no need for reasoning model\n'
        '            system=None,\n'
        '            user=prompt,',
        '        raw = await complete(\n'
        '            role="fast",  # bounded decision — no need for reasoning model\n'
        '            system=None,\n'
        '            user=prompt,\n'
        '            prompt_version_id=_pv,',
        "reattach.py: complete kwarg",
    ),
    (
        LIVING,
        '        prompt = render(\n'
        '            "chain.impacts",\n'
        '            block_slug=block_slug,\n'
        '            events=_format_events(events),\n'
        '        )\n'
        '        raw = await complete(\n'
        '            role="reasoning",\n'
        '            system=None,\n'
        '            user=prompt,\n'
        '            org_id=org_id,\n'
        '            purpose=f"chain.impacts.{block_slug}",',
        '        prompt, _pv = render_with_version(\n'
        '            "chain.impacts",\n'
        '            block_slug=block_slug,\n'
        '            events=_format_events(events),\n'
        '        )\n'
        '        raw = await complete(\n'
        '            role="reasoning",\n'
        '            system=None,\n'
        '            user=prompt,\n'
        '            org_id=org_id,\n'
        '            prompt_version_id=_pv,\n'
        '            purpose=f"chain.impacts.{block_slug}",',
        "living.py: chain.impacts",
    ),
    (
        LIVING,
        '        prompt = render(\n'
        '            "chain.scenarios",\n'
        '            block_slug=block_slug,\n'
        '            events=_format_events(events),\n'
        '            impacts=_format_impacts(impacts),\n'
        '        )\n'
        '        raw = await complete(\n'
        '            role="reasoning",\n'
        '            system=None,\n'
        '            user=prompt,\n'
        '            org_id=org_id,\n'
        '            purpose=f"chain.scenarios.{block_slug}",',
        '        prompt, _pv = render_with_version(\n'
        '            "chain.scenarios",\n'
        '            block_slug=block_slug,\n'
        '            events=_format_events(events),\n'
        '            impacts=_format_impacts(impacts),\n'
        '        )\n'
        '        raw = await complete(\n'
        '            role="reasoning",\n'
        '            system=None,\n'
        '            user=prompt,\n'
        '            org_id=org_id,\n'
        '            prompt_version_id=_pv,\n'
        '            purpose=f"chain.scenarios.{block_slug}",',
        "living.py: chain.scenarios",
    ),
    (
        LIVING,
        '        prompt = render(\n'
        '            "chain.watchlist",\n'
        '            block_slug=block_slug,\n'
        '            events=_format_events(events),\n'
        '            impacts=_format_impacts(impacts),\n'
        '        )\n'
        '        raw = await complete(\n'
        '            role="reasoning",\n'
        '            system=None,\n'
        '            user=prompt,\n'
        '            org_id=org_id,\n'
        '            purpose=f"chain.watchlist.{block_slug}",',
        '        prompt, _pv = render_with_version(\n'
        '            "chain.watchlist",\n'
        '            block_slug=block_slug,\n'
        '            events=_format_events(events),\n'
        '            impacts=_format_impacts(impacts),\n'
        '        )\n'
        '        raw = await complete(\n'
        '            role="reasoning",\n'
        '            system=None,\n'
        '            user=prompt,\n'
        '            org_id=org_id,\n'
        '            prompt_version_id=_pv,\n'
        '            purpose=f"chain.watchlist.{block_slug}",',
        "living.py: chain.watchlist",
    ),
    (
        BRIEF,
        '    prompt = render("daily_brief.trading", block_slug="trading", data=data_json)\n'
        '\n'
        '    try:\n'
        '        raw = await complete(\n'
        '            role="reasoning",\n'
        '            system=None,\n'
        '            user=prompt,\n'
        '            org_id=org_id,\n'
        '            purpose="daily_brief.trading",',
        '    prompt, _pv = render_with_version("daily_brief.trading", block_slug="trading", data=data_json)\n'
        '\n'
        '    try:\n'
        '        raw = await complete(\n'
        '            role="reasoning",\n'
        '            system=None,\n'
        '            user=prompt,\n'
        '            org_id=org_id,\n'
        '            prompt_version_id=_pv,\n'
        '            purpose="daily_brief.trading",',
        "brief/generator.py: daily_brief.trading",
    ),
    (
        ENRICH,
        '    prompt = render("enrich.combined", text=text)\n'
        '\n'
        '    try:\n'
        '        raw = await complete(\n'
        '            role="fast",\n'
        '            system=None,\n'
        '            user=prompt,\n'
        '            org_id=org_id,\n'
        '            purpose="enrich.combined",',
        '    prompt, _pv = render_with_version("enrich.combined", text=text)\n'
        '\n'
        '    try:\n'
        '        raw = await complete(\n'
        '            role="fast",\n'
        '            system=None,\n'
        '            user=prompt,\n'
        '            org_id=org_id,\n'
        '            prompt_version_id=_pv,\n'
        '            purpose="enrich.combined",',
        "enrichment/pipeline.py: enrich.combined",
    ),
    (
        STOCK,
        '    prompt = render(\n'
        '        PROMPT_NAME,\n'
        '        source_name=row.source_name,',
        '    prompt, _pv = render_with_version(\n'
        '        PROMPT_NAME,\n'
        '        source_name=row.source_name,',
        "stock_to_events.py: render upgrade",
    ),
    (
        STOCK,
        '        raw = await complete(\n'
        '            role="fast",\n'
        '            system=None,',
        '        raw = await complete(\n'
        '            role="fast",\n'
        '            prompt_version_id=_pv,\n'
        '            system=None,',
        "stock_to_events.py: complete kwarg",
    ),
]


def step4_render_upgrades() -> None:
    print("\n[4/9] the render() → render_with_version() upgrades")
    for i, entry in enumerate(RENDER_UPGRADES):
        if len(entry) == 3:
            old, new, label = entry
        elif len(entry) == 4:
            # Some entries carry (file, old, new, label)
            explicit_file, old, new, label = entry
            path_map = {"_explicit": explicit_file}
        else:
            raise SystemExit(
                f"RENDER_UPGRADES[{i}] has {len(entry)} elements, expected 3 or 4:\n"
                f"  first 100 chars of entry[0]: {str(entry[0])[:100]}"
            )

        # Detect which file this belongs to from the label
        fname = label.split(":")[0].strip()
        path_map = {
            "chain_builder.py": CHAIN_BUILDER,
            "chain_linker.py": CHAIN_LINKER,
            "narrator.py": NARRATOR,
            "verifier.py": VERIFIER,
            "reattach.py": REATTACH,
            "living.py": LIVING,
            "generator.py": BRIEF,
            "pipeline.py": ENRICH,
            "stock_to_events.py": STOCK,
        }
        if len(entry) == 4:
            p = entry[0]
        else:
            if fname not in path_map:
                raise SystemExit(
                    f"RENDER_UPGRADES[{i}]: unknown file '{fname}' in label '{label}'"
                )
            p = path_map[fname]
        patch(p, old, new, label)


# ─────────────────────────────────────────────────────────────────────
# 5. The 3 non-render sites
# ─────────────────────────────────────────────────────────────────────

def step5_non_render() -> None:
    print("\n[5/9] the 3 non-render sites")

    # 5a. cluster.py — replace inline f-string with render_with_version
    patch(
        CLUSTER,
        '''    prompt = f"""You are a news analyst. Below are {len(samples)} examples from a cluster of related records.

Give the cluster a SHORT title (max 8 words) and a 1-sentence summary.

Return ONLY valid JSON: {{"title": "...", "summary": "..."}}

Examples:
{joined}

JSON:"""

    try:
        raw = await complete(
            role="fast",
            system=None,
            user=prompt,
            org_id=org_id,
            purpose="cluster.label",
            json_mode=True,
            max_tokens=200,
            use_cache=False,
        )''',
        '''    from packages.ai.prompts.loader import render_with_version

    prompt, _pv = render_with_version(
        "cluster.topic_label",
        count=len(samples),
        examples=joined,
    )

    try:
        raw = await complete(
            role="fast",
            system=None,
            user=prompt,
            org_id=org_id,
            prompt_version_id=_pv,
            purpose="cluster.label",
            json_mode=True,
            max_tokens=200,
            use_cache=False,
        )''',
        "cluster.py: cluster.label",
    )

    # 5b. subblocks/base.py — the verify user= string
    patch(
        SUBBASE,
        '''    user = (
        f"SOURCE DATA:\\n{payload_str}\\n\\n"
        f"CLAIMS:\\n{claims_block}\\n\\n"
        f"Reply only with 'UNSUPPORTED: <numbers>' or 'UNSUPPORTED: NONE'."
    )

    async def _try_verify() -> str:
        return await complete(
            role="reasoning",
            system=system,
            user=user,
            org_id=org_id,
            purpose="trading.verify",''',
        '''    from packages.ai.prompts.loader import render_with_version

    user, _pv_verify = render_with_version(
        "trading.verify",
        payload_str=payload_str,
        claims_block=claims_block,
    )

    async def _try_verify() -> str:
        return await complete(
            role="reasoning",
            system=system,
            user=user,
            org_id=org_id,
            prompt_version_id=_pv_verify,
            purpose="trading.verify",''',
        "subblocks/base.py: trading.verify",
    )

    # 5c. subblocks/base.py — the main generation user= string
    patch(
        SUBBASE,
        '''    full_user = (
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
        purpose=prompt_name,''',
        '''    from packages.ai.prompts.loader import render_with_version

    full_user, _pv_gen = render_with_version(
        "trading.subblock_generate",
        user=user,
        target_words=str(target_words),
    )

    log.info("subblock.llm_call_start", prompt=prompt_name, words=target_words)

    # Text protocol — avoids Groq's JSON-mode token cap that truncated
    # long responses and failed with json_validate_failed.
    text = await complete(
        role="reasoning",
        system=full_system,
        user=full_user,
        org_id=org_id,
        prompt_version_id=_pv_gen,
        purpose=prompt_name,''',
        "subblocks/base.py: trading.subblock_generate",
    )


# ─────────────────────────────────────────────────────────────────────
# 6. Syntax check every modified Python file
# ─────────────────────────────────────────────────────────────────────

def step6_syntax() -> None:
    print("\n[6/9] syntax check every modified Python file")
    files = [
        LOADER, ROUTER, SEED_EXTRA,
        CHAIN_BUILDER, CHAIN_LINKER, NARRATOR, VERIFIER,
        REATTACH, LIVING, BRIEF, ENRICH, STOCK,
        CLUSTER, SUBBASE,
    ]
    for f in files:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            fail(f"{f.name}: syntax error\n{r.stderr[:400]}")
        print(f"  ✓ {f.name} parses")


# ─────────────────────────────────────────────────────────────────────
# 7. Run the seed
# ─────────────────────────────────────────────────────────────────────

def step7_seed_run() -> None:
    print("\n[7/9] run seed_chain_extra.seed()")
    r = subprocess.run(
        ["python", "-c",
         "from packages.ai.prompts.seed_chain_extra import seed; seed(); "
         "from packages.ai.prompts.loader import clear_cache; clear_cache()"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    print(r.stdout)
    if r.returncode != 0:
        fail(f"seed failed:\n{r.stderr[:400]}")
    print("  ✓ seed ran")

    # Confirm rows exist
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        "SELECT name, version, role, is_active FROM prompt_versions "
        "WHERE name IN ('cluster.topic_label','trading.verify','trading.subblock_generate') "
        "ORDER BY name;"
    ], capture_output=True, text=True)
    print(r.stdout)


# ─────────────────────────────────────────────────────────────────────
# 8. tsc
# ─────────────────────────────────────────────────────────────────────

def step8_tsc() -> bool:
    print("\n[8/9] tsc --noEmit")
    r = subprocess.run(
        ["./node_modules/.bin/tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        print("  ❌ TS errors:")
        for line in (r.stdout or r.stderr).splitlines()[:20]:
            print(f"      {line}")
        return False
    print("  ✓ TypeScript clean")
    return True


# ─────────────────────────────────────────────────────────────────────
# 9. Live verification — restart API, build one chain, check ids
# ─────────────────────────────────────────────────────────────────────

def step9_verify() -> bool:
    print("\n[9/9] live verification")

    # Restart API so it picks up the router change
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    import time
    time.sleep(2)
    log = Path("/tmp/signaliq-api.log")
    with log.open("w") as f:
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
        print(f"  ❌ API /health = {r.stdout}")
        return False
    print("  ✓ API up")

    # Also restart the worker so it picks up the router change
    subprocess.run(["pkill", "-9", "-f", "celery.*worker"], check=False)
    time.sleep(2)
    wlog = Path("/tmp/signaliq-worker.log")
    with wlog.open("w") as f:
        subprocess.Popen(
            ["celery", "-A", "apps.worker.celery_app", "worker",
             "-l", "info", "-Q", "default,ingestion", "--hostname=worker1@%h"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(8)

    # Count before
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-t", "-c",
        "SELECT COUNT(*) FILTER (WHERE prompt_version_id IS NOT NULL) "
        "FROM ai_calls;"
    ], capture_output=True, text=True)
    before = int(r.stdout.strip() or 0)
    print(f"  ai_calls with prompt_version_id before: {before}")

    # Build one chain via the test org
    print("  building one chain to trigger pipeline calls…")
    r = subprocess.run([
        "python", "-c", '''
import asyncio, json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
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
    from apps.worker.tasks.news_chains import _build_chain_async
    events = [
        {"_placeholder_id": "e1", "external_id": "e1", "headline": "Fed signals no rate cut",
         "summary": "Powell said rates will stay higher for longer.", "occurred_at": "2026-09-15T12:00:00Z",
         "reported_at": None, "location": None, "actors": ["Fed", "Powell"],
         "event_type": "statement", "status": "fact", "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e2", "external_id": "e2", "headline": "10-year yield hits 5%",
         "summary": "Treasury yields jumped on the Fed statement.", "occurred_at": "2026-09-15T14:00:00Z",
         "reported_at": None, "location": None, "actors": ["Treasury"], "event_type": "price_move",
         "status": "fact", "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e3", "external_id": "e3", "headline": "S&P 500 drops 2%",
         "summary": "Equities sold off sharply after yields rose.", "occurred_at": "2026-09-15T20:00:00Z",
         "reported_at": None, "location": None, "actors": ["S&P 500"], "event_type": "price_move",
         "status": "fact", "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e4", "external_id": "e4", "headline": "Dollar strengthens against yen",
         "summary": "Higher yields supported the dollar.", "occurred_at": "2026-09-16T08:00:00Z",
         "reported_at": None, "location": None, "actors": ["USD", "JPY"], "event_type": "price_move",
         "status": "fact", "confidence": 0.9, "sources": [], "topics": []},
    ]
    result = await _build_chain_async(oid, events, "news", None, 800)
    print(json.dumps(result, default=str))

asyncio.run(main())
'''.strip()
    ], cwd=ROOT, capture_output=True, text=True, timeout=180)
    print(f"  build result: {r.stdout.strip()[:300]}")
    if r.returncode != 0:
        print(f"  ⚠ build returned non-zero: {r.stderr[-300:]}")
        # Not fatal — just note it

    # Count after
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-t", "-c",
        "SELECT COUNT(*) FILTER (WHERE prompt_version_id IS NOT NULL) "
        "FROM ai_calls;"
    ], capture_output=True, text=True)
    after = int(r.stdout.strip() or 0)
    print(f"  ai_calls with prompt_version_id after: {after}")
    delta = after - before
    print(f"  delta: {delta}")

    if delta >= 3:
        print("  ✓ prompt_version_id is now being written")
        return True
    print(f"  ⚠ expected delta >= 3, got {delta}")
    print("     (build may have failed — see above; check worker log)")
    return False


def main() -> None:
    print("=" * 70)
    print("S.6.1a — wire prompt versions into the router")
    print("=" * 70)

    step1_loader()
    step2_router()
    step3_seed()
    step4_render_upgrades()
    step5_non_render()
    step6_syntax()
    step7_seed_run()
    ok_ts = step8_tsc()
    ok_live = step9_verify() if ok_ts else False

    print("=" * 70)
    if ok_ts and ok_live:
        print("✅ S.6.1a complete — every chain call now records its prompt version")
        sys.exit(0)
    print("❌ S.6.1a failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
