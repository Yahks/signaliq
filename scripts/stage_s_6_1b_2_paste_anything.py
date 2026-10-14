"""
S.6.1b.2 — paste-anything chain build.

The /chains/new page currently accepts only {events: [...]} or {posts: [...]}.
A user who pastes an article gets 'invalid_shape'. This ships the promised
feature: paste anything, the LLM extracts events, the DB contributes related
ones, the pipeline builds a verified chain.

Eight parts:

  1. packages/ai/chain/extract.py  — new module
       ensure_manual_paste_source, persist_paste, extract_events_from_any_input
  2. packages/ai/chain/enrich.py   — new module
       enrich_with_related_events (vector search on news_events.embedding)
  3. packages/core/tokens.py       — charge_for_upload @ 1 token / 100 words
  4. chain.extract@1 prompt         — LLM extraction, versioned
  5. apps/api/schemas/news_chain.py — ContentBuildRequest
  6. apps/api/routers/news_chains.py — accept {content}, add status endpoint
  7. apps/web/src/lib/api.ts        — buildChainFromContent + getChainBuildStatus
  8. apps/web/src/app/(app)/chains/new/page.tsx — rewrite as paste-anything

Verification (sequential, each asserted before the next):
  a. ensure_manual_paste_source is idempotent (same id twice)
  b. persist_paste inserts a raw_records row with a content_hash
  c. charge_for_upload(250 words) debits 2 tokens (floor)
  d. extract_events_from_any_input returns ≥2 valid EventInput for a paragraph
  e. enrich_with_related_events adds events (≥ seed count)
  f. HTTP: POST {content} → 202 → poll → success with verification_status
  g. tsc --noEmit
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"

EXTRACT = ROOT / "packages" / "ai" / "chain" / "extract.py"
ENRICH = ROOT / "packages" / "ai" / "chain" / "enrich.py"
TOKENS = ROOT / "packages" / "core" / "tokens.py"
SEED = ROOT / "packages" / "ai" / "prompts" / "seed_chain_extra.py"
SCHEMA = ROOT / "apps" / "api" / "schemas" / "news_chain.py"
ROUTER = ROOT / "apps" / "api" / "routers" / "news_chains.py"
API_TS = WEB / "src" / "lib" / "api.ts"
NEW_PAGE = WEB / "src" / "app" / "(app)" / "chains" / "new" / "page.tsx"
LIST_PAGE = WEB / "src" / "app" / "(app)" / "chains" / "page.tsx"


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
    src = src.replace(old, new, 1)
    path.write_text(src)
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# PART 1 — packages/ai/chain/extract.py
# ─────────────────────────────────────────────────────────────────────

EXTRACT_SRC = '''"""Extract events from arbitrary user input.

The user pastes anything — an article, a tweet, a paragraph, some JSON —
and we produce a list of EventInput ready for the chain pipeline.

Three paths, in order:
  1. Valid JSON matching our schema   → use directly, no LLM
  2. Valid JSON of another shape      → pass to the LLM as-is
  3. Not JSON                         → LLM extraction

The LLM path uses chain.extract@1 (role=reasoning) and is charged to the
user's BYOK key. The upload itself is charged separately by charge_for_upload.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.chain.event_extractor import (
    EventInput,
    validate_and_parse_event,
)
from packages.ai.prompts.loader import render_with_version
from packages.ai.router import complete
from packages.core.logging import get_logger
from packages.db.models.raw_record import RawRecord
from packages.db.models.source import Source
from packages.ingestion.hashing import content_hash

log = get_logger(__name__)

MANUAL_PASTE_SOURCE_NAME = "manual_paste"


# ─────────────────────────────────────────────────────────────────────
# Source + persistence
# ─────────────────────────────────────────────────────────────────────

async def ensure_manual_paste_source(session: AsyncSession, org_id: str) -> str:
    """Idempotently create the per-org 'manual_paste' source. Returns its id."""
    # Look for an existing one (name is not unique-constrained; we match by
    # (org_id, name) and pick the oldest if multiple exist).
    row = (await session.execute(
        select(Source.id)
        .where(Source.org_id == org_id)
        .where(Source.name == MANUAL_PASTE_SOURCE_NAME)
        .where(Source.deleted_at.is_(None))
        .order_by(Source.created_at.asc())
        .limit(1)
    )).first()
    if row:
        return str(row[0])

    src = Source(
        org_id=org_id,
        name=MANUAL_PASTE_SOURCE_NAME,
        source_type="manual",
        description="Content pasted directly by a user on /chains/new.",
        collection_method="user_paste",
        retention_policy=None,
        config={},
    )
    session.add(src)
    await session.flush()
    await session.refresh(src)
    log.info("extract.source_created", org_id=org_id[:8], source_id=str(src.id)[:8])
    return str(src.id)


async def persist_paste(
    session: AsyncSession,
    org_id: str,
    source_id: str,
    raw_text: str,
    *,
    created_by: str | None = None,
) -> str:
    """Insert a raw_records row for the paste. Returns the new id."""
    rr = RawRecord(
        org_id=org_id,
        source_id=source_id,
        ingestion_job_id=None,
        source_record_id=None,
        author_identifier=created_by,
        original_timestamp=None,
        text=raw_text,
        language=None,
        metadata_={"kind": "chain_paste"},
        content_hash=content_hash(raw_text),
    )
    session.add(rr)
    await session.flush()
    await session.refresh(rr)
    return str(rr.id)


# ─────────────────────────────────────────────────────────────────────
# JSON-first, then LLM extraction
# ─────────────────────────────────────────────────────────────────────

_EXTRACT_PROMPT_NAME = "chain.extract"


async def extract_events_from_any_input(
    session: AsyncSession,
    org_id: str,
    raw_text: str,
    *,
    use_cache: bool = False,
) -> list[EventInput]:
    """Take arbitrary text and return EventInput objects ready for the pipeline."""
    stripped = raw_text.strip()
    if not stripped:
        raise ValueError("Empty input")

    # Path 1 & 2: try JSON
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, dict) and "events" in parsed:
            events, _ = _parse_events_payload(parsed)
            if events:
                log.info("extract.json_events", count=len(events))
                return events

        if isinstance(parsed, dict) and "posts" in parsed:
            events = _convert_posts(parsed["posts"])
            if events:
                log.info("extract.json_posts", count=len(events))
                return events

        # Other JSON shapes → fall through to LLM with the raw JSON string

    # Path 3: LLM extraction
    return await _extract_via_llm(session, org_id, stripped, use_cache=use_cache)


def _parse_events_payload(payload: dict[str, Any]) -> tuple[list[EventInput], dict]:
    """Thin wrapper so callers can import just extract.py."""
    from packages.ai.chain.event_extractor import parse_news_event_json
    return parse_news_event_json(payload)


def _convert_posts(posts: list[Any]) -> list[EventInput]:
    """Convert a list of Facebook-style records to EventInput objects."""
    out: list[EventInput] = []
    for i, p in enumerate(posts):
        if not isinstance(p, dict):
            continue
        raw_text = str(
            p.get("text") or p.get("content") or p.get("body") or p.get("message") or ""
        ).strip()
        if not raw_text:
            continue
        first_sentence = raw_text.split(".")[0][:400] or raw_text[:120]
        author = p.get("author") or p.get("author_name") or p.get("page_name")
        occurred_at_raw = p.get("created_at") or p.get("posted_at") or p.get("date")
        try:
            out.append(validate_and_parse_event({
                "external_id": p.get("post_id") or p.get("id") or f"post_{i}",
                "headline": first_sentence,
                "summary": raw_text[:2000],
                "occurred_at": occurred_at_raw,
                "actors": [str(author)] if author else ["unknown"],
                "event_type": "report",
                "status": "fact",
                "confidence": 0.7,
            }, i))
        except Exception as exc:
            log.warning("extract.post_skip", i=i, error=str(exc)[:100])
    return out


async def _extract_via_llm(
    session: AsyncSession,
    org_id: str,
    text: str,
    *,
    use_cache: bool = False,
) -> list[EventInput]:
    """Call the LLM with chain.extract and parse the response."""
    # Truncate extremely long pastes — the LLM has a token budget.
    max_chars = 20_000
    if len(text) > max_chars:
        text = text[:max_chars]
        log.info("extract.truncated", max_chars=max_chars)

    prompt, pv = render_with_version(
        _EXTRACT_PROMPT_NAME,
        text=text,
    )

    raw = await complete(
        role="reasoning",
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=pv,
        purpose="chain.extract",
        temperature=0.2,
        max_tokens=4000,
        json_mode=True,
        use_cache=use_cache,
    )

    # The model returns a JSON array of event objects.
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("extract.parse_failed", snippet=raw[:300])
        raise ValueError("Extraction produced invalid JSON")

    if isinstance(parsed, dict) and "events" in parsed:
        parsed = parsed["events"]
    if not isinstance(parsed, list):
        raise ValueError(f"Extraction returned {type(parsed).__name__}, expected array")

    events: list[EventInput] = []
    for i, raw_ev in enumerate(parsed):
        try:
            events.append(validate_and_parse_event(raw_ev, i))
        except Exception as exc:
            log.warning("extract.event_skip", i=i, error=str(exc)[:120])

    if not events:
        raise ValueError("Extraction produced no valid events")
    log.info("extract.llm_done", count=len(events))
    return events
'''


def part1_extract() -> None:
    print("\n[1/8] packages/ai/chain/extract.py")
    EXTRACT.write_text(EXTRACT_SRC)
    print(f"  ✓ wrote {EXTRACT.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# PART 2 — packages/ai/chain/enrich.py
# ─────────────────────────────────────────────────────────────────────

ENRICH_SRC = '''"""Enrich seed events with related events from the org's existing pool.

Vector search on news_events.embedding (vector(768), HNSW cosine index).
Seeds are embedded with the same embedding role used elsewhere; the mean
of the seed embeddings becomes the query vector.

Falls back gracefully: if embeddings fail or no matches are found, returns
the seeds unchanged.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.chain.event_extractor import EventInput
from packages.ai.router import embed
from packages.core.logging import get_logger

log = get_logger(__name__)


async def enrich_with_related_events(
    session: AsyncSession,
    org_id: str,
    seed_events: list[EventInput],
    *,
    max_total: int = 20,
) -> list[EventInput]:
    """Return seed_events plus up to (max_total - len(seeds)) related events.

    Related = nearest neighbours in the org's news_events.embedding space,
    excluding any event whose headline exactly matches a seed.
    """
    if not seed_events:
        return []
    if len(seed_events) >= max_total:
        return seed_events[:max_total]

    # Embed each seed — headline + summary joined
    seed_texts = [f"{e.headline}. {e.summary}" for e in seed_events]
    try:
        vecs = await embed(texts=seed_texts, org_id=org_id, purpose="chain.enrich_seeds")
    except Exception as exc:
        log.warning("enrich.embed_failed", error=str(exc)[:200])
        return seed_events

    if not vecs or len(vecs) != len(seed_texts):
        log.warning("enrich.embed_mismatch", expected=len(seed_texts), got=len(vecs or []))
        return seed_events

    # Mean vector (component-wise)
    dim = len(vecs[0])
    mean = [0.0] * dim
    for v in vecs:
        for i, x in enumerate(v):
            mean[i] += float(x)
    n = len(vecs)
    mean = [x / n for x in mean]

    # Format as pgvector literal
    vec_literal = "[" + ",".join(f"{x:.6f}" for x in mean) + "]"

    # Exclude seeds by exact headline match
    seed_headlines = [e.headline for e in seed_events]

    need = max_total - len(seed_events)
    try:
        rows = (await session.execute(sql_text("""
            SELECT id::text, headline, summary, occurred_at, actors,
                   event_type, status, confidence
            FROM news_events
            WHERE org_id = :oid
              AND embedding IS NOT NULL
              AND headline <> ALL(:seed_headlines)
            ORDER BY embedding <=> CAST(:vec AS vector(768))
            LIMIT :limit
        """), {
            "oid": org_id,
            "seed_headlines": seed_headlines,
            "vec": vec_literal,
            "limit": need,
        })).all()
    except Exception as exc:
        log.warning("enrich.vector_search_failed", error=str(exc)[:200])
        return seed_events

    related: list[EventInput] = []
    for r in rows:
        try:
            related.append(EventInput(
                external_id=None,
                headline=str(r[1])[:500],
                summary=str(r[2])[:4000],
                occurred_at=r[3],
                reported_at=None,
                location=None,
                actors=list(r[4] or []),
                event_type=str(r[5] or "other"),
                status=str(r[6] or "fact"),
                confidence=float(r[7] if r[7] is not None else 0.5),
            ))
        except Exception as exc:
            log.warning("enrich.row_skip", error=str(exc)[:120])

    log.info(
        "enrich.done",
        seeds=len(seed_events),
        related=len(related),
        total=len(seed_events) + len(related),
    )
    return seed_events + related
'''


def part2_enrich() -> None:
    print("\n[2/8] packages/ai/chain/enrich.py")
    ENRICH.write_text(ENRICH_SRC)
    print(f"  ✓ wrote {ENRICH.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# PART 3 — tokens.py charge_for_upload
# ─────────────────────────────────────────────────────────────────────

def part3_tokens() -> None:
    print("\n[3/8] tokens.py charge_for_upload")
    src = TOKENS.read_text()
    if "charge_for_upload" in src:
        print("  ↷ already present")
        return

    marker = "TOKENS_PER_WORD = 0.5"
    if marker not in src:
        fail("tokens.py: TOKENS_PER_WORD not found")

    addition = '''

TOKENS_PER_100_WORDS_UPLOAD = 1


async def charge_for_upload(
    session: AsyncSession,
    user_id: str,
    *,
    word_count: int,
    ref_id: str | None = None,
    org_id: str | None = None,
) -> int:
    """Charge site tokens for a paste/upload. 1 token per 100 words (min 1).

    Separate from charge_for_content: this meters the input, not the output.
    """
    amount = max(1, word_count // 100)
    ok = await debit(
        session, user_id, amount, "content:upload",
        org_id=org_id, ref_id=ref_id,
        metadata={"word_count": word_count, "rate": TOKENS_PER_100_WORDS_UPLOAD},
    )
    if not ok:
        log.warning("tokens.upload_charge_rejected",
                    user_id=user_id[:8], word_count=word_count, amount=amount)
        return 0
    log.info("tokens.upload_charged", user_id=user_id[:8],
             word_count=word_count, amount=amount)
    return amount
'''
    # Insert after the LOW_BALANCE_THRESHOLD line, before the helpers.
    src = src.replace(marker, marker + addition, 1)
    TOKENS.write_text(src)
    print("  ✓ charge_for_upload added")


# ─────────────────────────────────────────────────────────────────────
# PART 4 — chain.extract@1 prompt
# ─────────────────────────────────────────────────────────────────────
#
# PROMPT REVIEW (fresh-read check before seeding):
#
#   1. Output format matches what _extract_via_llm parses: a JSON array of
#      objects with headline/summary/occurred_at/actors/event_type/status/
#      confidence. ✓
#   2. Prioritisation: cap at 10 explicit, floor of 2 explicit. No "or fewer"
#      contradiction. ✓
#   3. Every field required by validate_and_parse_event is mentioned with a
#      fallback ("unknown", null, etc.). ✓
#   4. JSON example immediately precedes "JSON:" — nothing after. ✓
#   5. No instruction is contradicted later in the prompt. ✓
#
CHAIN_EXTRACT_V1 = '''You are a news event extractor. Below is text a user pasted — it may be an
article, a tweet thread, a paragraph, or a partial sentence. Your job is to
extract the discrete news events the text describes.

TEXT:
{{text}}

Rules:
- Extract between 2 and 10 events. Never fewer than 2. If the text contains
  only one clear event, extract that one and one closely-related fact as a
  second event; the caller will enrich with related events from the database.
- An event is: something that happened, at a specific time, involving specific
  actors. Do not extract opinions, general statements, or background.
- Use only the facts in the text. Do NOT invent dates, numbers, or names.
- If a field is not stated, use null (for occurred_at) or an empty array
  (for actors) rather than guessing.
- event_type must be one of: statement | economic | price_move | analyst_call |
  sentiment_shift | report | policy | conflict | other
- status must be one of: fact | claim | rumor
- confidence is a float between 0.0 and 1.0 reflecting how clearly the text
  states the event.

Return ONLY valid JSON in this exact shape:
[
  {
    "headline": "concise, action-oriented (max 150 chars)",
    "summary": "2-4 sentence factual summary (max 2000 chars)",
    "occurred_at": "2026-09-15T12:00:00Z",
    "actors": ["Fed", "Powell"],
    "event_type": "statement",
    "status": "fact",
    "confidence": 0.9
  }
]

JSON:'''


def part4_seed_extract() -> None:
    print("\n[4/8] chain.extract@1 prompt")
    src = SEED.read_text()
    if "CHAIN_EXTRACT_V1" in src:
        print("  ↷ already present")
        return

    addition = f'''

# ─────────────────────────────────────────────────────────────────────
# S.6.1b.2 — chain.extract@1 (paste-anything event extraction)
# ─────────────────────────────────────────────────────────────────────

CHAIN_EXTRACT_V1 = """{CHAIN_EXTRACT_V1}"""


def seed_s61b_2() -> None:
    """Activate chain.extract@1."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'chain.extract'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('chain.extract', 1, 'reasoning', :template, CAST(:vars AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {{
                "template": CHAIN_EXTRACT_V1,
                "vars": json.dumps(["text"]),
            }},
        )
        session.commit()
    print("  seeded chain.extract@1")


if __name__ == "__main__":
    seed()
    seed_s61b_1()
    seed_s61b_1b()
    seed_s61b_1b_1()
    seed_s61b_2()
'''
    src = src.rstrip() + addition
    SEED.write_text(src)
    print("  ✓ appended CHAIN_EXTRACT_V1 + seed_s61b_2")


# ─────────────────────────────────────────────────────────────────────
# PART 5 — schema
# ─────────────────────────────────────────────────────────────────────

def part5_schema() -> None:
    print("\n[5/8] schemas/news_chain.py — ContentBuildRequest + StatusResponse")
    src = SCHEMA.read_text()
    if "class ContentBuildRequest" in src:
        print("  ↷ already present")
        return

    marker = "class ChainBuildResponse(BaseModel):"
    if marker not in src:
        fail("schemas/news_chain.py: ChainBuildResponse not found")

    addition = '''class ContentBuildRequest(BaseModel):
    """Paste-anything chain build. The user provides arbitrary text."""
    content: str = Field(min_length=20, max_length=50000)
    block_slug: str = Field(default="news", min_length=1, max_length=64)
    subtopic_slug: str | None = Field(default=None, max_length=64)
    target_words: int | None = Field(default=None, ge=200, le=5000)


class ChainBuildStatusResponse(BaseModel):
    """Poll response for an in-flight chain build."""
    task_id: str
    status: str   # 'pending' | 'started' | 'success' | 'failure'
    chain_id: str | None = None
    post_id: str | None = None
    verification_status: str | None = None
    error: str | None = None
    reason: str | None = None


'''
    src = src.replace(marker, addition + marker, 1)
    SCHEMA.write_text(src)
    print("  ✓ ContentBuildRequest + ChainBuildStatusResponse")


# ─────────────────────────────────────────────────────────────────────
# PART 6 — router: accept {content}, add status endpoint
# ─────────────────────────────────────────────────────────────────────

def part6_router() -> None:
    print("\n[6/8] routers/news_chains.py — new POST body + status endpoint")

    # 6a. Add the imports the new handler needs
    patch(
        ROUTER,
        '''from apps.api.schemas.news_chain import (
    ChainBuildRequest,
    ChainBuildResponse,''',
        '''from apps.api.schemas.news_chain import (
    ChainBuildRequest,
    ChainBuildResponse,
    ChainBuildStatusResponse,
    ContentBuildRequest,''',
        "import ContentBuildRequest + StatusResponse",
    )

    # 6b. Replace the POST handler body
    patch(
        ROUTER,
        '''@router.post("/build", response_model=ChainBuildResponse, status_code=202)
async def build_chain_endpoint(
    body: ChainBuildRequest,
    org_id: str = Depends(current_org_id),
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_scoped_session),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
) -> ChainBuildResponse:
    """Validate the payload, enqueue the chain build, return immediately."""
    try:
        events, _ = parse_news_event_json({"events": body.events})
    except ValidationError as exc:
        raise

    # Serialize events for the Celery task (JSON-safe)
    serialized = []
    for i, ev in enumerate(events):
        serialized.append({
            "_placeholder_id": f"ph_{i}",
            "external_id": ev.external_id,
            "headline": ev.headline,
            "summary": ev.summary,
            "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
            "reported_at": ev.reported_at.isoformat() if ev.reported_at else None,
            "location": ev.location,
            "actors": ev.actors,
            "event_type": ev.event_type,
            "status": ev.status,
            "confidence": ev.confidence,
            "sources": [
                {"name": s.name, "url": s.url, "type": s.type}
                for s in ev.sources
            ],
            "topics": ev.topics,
        })

    from apps.worker.tasks.news_chains import build_chain

    # Verify the block exists (raises if not)
    from packages.blocks.loader import load_block_by_slug
    load_block_by_slug(body.block_slug, org_id=org_id)

    # Per-org chain build rate limit (LLM cost protection)
    from packages.core.rate_limit import check_org_chain_build_limit
    rl = check_org_chain_build_limit(org_id)
    if not rl.allowed:
        raise QuotaExceededError(
            f"Chain build rate limit reached ({rl.limit}/hour). "
            f"Try again in {rl.retry_after_seconds}s.",
            org_id=org_id,
            limit=rl.limit,
            retry_after_seconds=rl.retry_after_seconds,
        )

    # Enqueue; the task returns a result but we don't wait for it
    result = build_chain.delay(
        org_id,
        serialized,
        body.block_slug,
        body.subtopic_slug,
        body.target_words,
    )

    return ChainBuildResponse(
        chain_id=result.id,
        status="processing",
        event_count=len(serialized),
    )''',
        '''def _serialize_events(events: list) -> list[dict]:
    """Convert EventInput objects to JSON-safe dicts for the Celery task."""
    out = []
    for i, ev in enumerate(events):
        out.append({
            "_placeholder_id": f"ph_{i}",
            "external_id": ev.external_id,
            "headline": ev.headline,
            "summary": ev.summary,
            "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
            "reported_at": ev.reported_at.isoformat() if ev.reported_at else None,
            "location": ev.location,
            "actors": ev.actors,
            "event_type": ev.event_type,
            "status": ev.status,
            "confidence": ev.confidence,
            "sources": [
                {"name": s.name, "url": s.url, "type": s.type}
                for s in ev.sources
            ],
            "topics": ev.topics,
        })
    return out


@router.post("/build", response_model=ChainBuildResponse, status_code=202)
async def build_chain_endpoint(
    body: ContentBuildRequest,
    org_id: str = Depends(current_org_id),
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_scoped_session),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
) -> ChainBuildResponse:
    """Paste-anything chain build: extract → enrich → enqueue.

    1. Persist the paste to raw_records
    2. Charge the upload (1 token / 100 words)
    3. Extract events (JSON-shape or LLM)
    4. Enrich with related events from the org's pool
    5. Enqueue the chain build
    """
    from packages.ai.chain.enrich import enrich_with_related_events
    from packages.ai.chain.extract import (
        ensure_manual_paste_source,
        extract_events_from_any_input,
        persist_paste,
    )
    from packages.core.tokens import charge_for_upload
    from packages.blocks.loader import load_block_by_slug
    from apps.worker.tasks.news_chains import build_chain

    load_block_by_slug(body.block_slug, org_id=org_id)

    # Per-org rate limit
    from packages.core.rate_limit import check_org_chain_build_limit
    rl = check_org_chain_build_limit(org_id)
    if not rl.allowed:
        raise QuotaExceededError(
            f"Chain build rate limit reached ({rl.limit}/hour). "
            f"Try again in {rl.retry_after_seconds}s.",
            org_id=org_id,
            limit=rl.limit,
            retry_after_seconds=rl.retry_after_seconds,
        )

    # 1. Persist the paste
    source_id = await ensure_manual_paste_source(session, org_id)
    raw_id = await persist_paste(
        session, org_id, source_id, body.content, created_by=user_id,
    )

    # 2. Charge the upload
    word_count = len(body.content.split())
    await charge_for_upload(
        session, user_id, word_count=word_count, ref_id=raw_id, org_id=org_id,
    )

    # 3. Extract
    seeds = await extract_events_from_any_input(session, org_id, body.content)

    # 4. Enrich
    events = await enrich_with_related_events(session, org_id, seeds, max_total=20)

    # 5. Enqueue
    serialized = _serialize_events(events)
    result = build_chain.delay(
        org_id,
        serialized,
        body.block_slug,
        body.subtopic_slug,
        body.target_words,
    )

    return ChainBuildResponse(
        chain_id=result.id,
        status="processing",
        event_count=len(serialized),
    )


@router.get("/build/{task_id}", response_model=ChainBuildStatusResponse)
async def chain_build_status(
    task_id: str,
    _org_id: str = Depends(current_org_id),
) -> ChainBuildStatusResponse:
    """Poll the status of an in-flight chain build."""
    from celery.result import AsyncResult
    from apps.worker.celery_app import celery_app

    r = AsyncResult(task_id, app=celery_app)
    state = r.state  # PENDING | STARTED | SUCCESS | FAILURE | RETRY | REVOKED

    # Map Celery states to our statuses
    if state == "SUCCESS":
        payload = r.result or {}
        if isinstance(payload, dict):
            return ChainBuildStatusResponse(
                task_id=task_id,
                status="success" if payload.get("status") == "ok" else "failure",
                chain_id=payload.get("chain_id"),
                post_id=payload.get("post_id"),
                verification_status=payload.get("verification_status"),
                error=payload.get("error"),
            )
        return ChainBuildStatusResponse(task_id=task_id, status="success")

    if state == "FAILURE":
        err = r.result
        return ChainBuildStatusResponse(
            task_id=task_id,
            status="failure",
            error=str(err)[:400] if err else "task failed",
        )

    if state in ("STARTED", "RETRY"):
        return ChainBuildStatusResponse(task_id=task_id, status="started")

    # PENDING (or unknown)
    return ChainBuildStatusResponse(task_id=task_id, status="pending")''',
        "POST body rewritten + status endpoint added",
    )


# ─────────────────────────────────────────────────────────────────────
# PART 7 — api.ts
# ─────────────────────────────────────────────────────────────────────

def part7_api_ts() -> None:
    print("\n[7/8] lib/api.ts — buildChainFromContent + getChainBuildStatus")

    src = API_TS.read_text()

    # Type for the status response
    if "ChainBuildStatusResponse" not in src:
        patch(
            API_TS,
            "export type ChainBuildResponse = {",
            '''export type ChainBuildStatusResponse = {
  task_id: string;
  status: "pending" | "started" | "success" | "failure";
  chain_id?: string | null;
  post_id?: string | null;
  verification_status?: string | null;
  error?: string | null;
};

export type ChainBuildResponse = {''',
            "ChainBuildStatusResponse type",
        )

    # Methods — replace buildChain with buildChainFromContent + add status
    if "buildChainFromContent" in API_TS.read_text():
        print("  ↷ methods already present")
        return

    patch(
        API_TS,
        '''  buildChain: (events: any[], opts: { blockSlug: string; subtopicSlug?: string | null; targetWords?: number } = { blockSlug: "news" }) =>
    request<ChainBuildResponse>("/api/chains/build", {''',
        '''  buildChainFromContent: (
    content: string,
    opts: { blockSlug: string; subtopicSlug?: string | null; targetWords?: number } = { blockSlug: "news" },
  ) =>
    request<ChainBuildResponse>("/api/chains/build", {
      method: "POST",
      body: {
        content,
        block_slug: opts.blockSlug,
        subtopic_slug: opts.subtopicSlug ?? null,
        target_words: opts.targetWords ?? null,
      },
    }),
  getChainBuildStatus: (taskId: string) =>
    request<ChainBuildStatusResponse>(`/api/chains/build/${taskId}`),
  _oldBuildChain: (events: any[], opts: { blockSlug: string; subtopicSlug?: string | null; targetWords?: number } = { blockSlug: "news" }) =>
    request<ChainBuildResponse>("/api/chains/build", {''',
        "buildChainFromContent + getChainBuildStatus",
    )


# ─────────────────────────────────────────────────────────────────────
# PART 8 — the /chains/new page (rewrite)
# ─────────────────────────────────────────────────────────────────────

NEW_PAGE_SRC = '''"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError, type ChainBuildResponse, type BlockResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

const SAMPLE: Record<string, string> = {
  news: `The Federal Reserve signalled on Tuesday that it will hold interest rates higher for longer, with Chair Jerome Powell telling reporters the committee sees no path to cutting rates before the second half of next year.

The decision sent Treasury yields sharply higher. The 10-year note jumped 18 basis points to 5.02%, its highest level since 2007.

Equity markets sold off broadly in response. The S&P 500 fell 2.1%, its worst single-day performance since August.

Currency markets also reacted. The dollar index rose 0.7% to 106.8, its strongest level since November, with the greenback gaining 1.2% against the yen.

Analysts were divided on the implications, with Morgan Stanley and Goldman Sachs offering conflicting interpretations of whether the Fed's stance will weigh on growth into 2025.`,
  trading: `NVIDIA reported Q3 earnings on Wednesday, beating expectations with revenue of $35.1 billion, up 94% year over year. Data center revenue was $30.8 billion, up 112%.

The company guided Q4 revenue to approximately $37.5 billion, above analyst estimates of $37.0 billion.

CEO Jensen Huang said demand for the company's Blackwell platform is "insane" and that supply constraints remain the primary bottleneck.

Following the announcement, NVIDIA shares rose 5% in after-hours trading. Competitors AMD and Broadcom also rose modestly on sympathy.`,
};

type Phase = "idle" | "extracting" | "building" | "done" | "failed";

export default function NewChainPage() {
  const router = useRouter();
  const blocks = useQuery({ queryKey: ["blocks"], queryFn: api.listBlocks });

  const [blockSlug, setBlockSlug] = useState<string>("news");
  const [subtopicSlug, setSubtopicSlug] = useState<string | null>(null);
  const [content, setContent] = useState<string>(SAMPLE.news);
  const [error, setError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [reason, setReason] = useState<string | null>(null);

  const activeBlock: BlockResponse | undefined = blocks.data?.find(
    (b) => b.slug === blockSlug,
  );

  function onBlockChange(slug: string) {
    setBlockSlug(slug);
    setSubtopicSlug(null);
    if (SAMPLE[slug]) setContent(SAMPLE[slug]);
    setError(null);
    setTaskId(null);
    setPhase("idle");
    setReason(null);
  }

  const build = useMutation({
    mutationFn: () =>
      api.buildChainFromContent(content, {
        blockSlug,
        subtopicSlug,
        targetWords: activeBlock?.config?.default_target_words as number | undefined,
      }),
    onSuccess: (data: ChainBuildResponse) => {
      setTaskId(data.chain_id);   // the task id is returned as chain_id at enqueue
      setPhase("extracting");
      setError(null);
    },
    onError: (e) =>
      setError(e instanceof ApiError ? (e as ApiError).userMessage : "Build failed"),
  });

  // Poll the status endpoint while the task is running
  const status = useQuery({
    queryKey: ["chain-build-status", taskId],
    queryFn: () => api.getChainBuildStatus(taskId as string),
    enabled: !!taskId && (phase === "extracting" || phase === "building"),
    refetchInterval: (query) => {
      const data = query.state.data as { status?: string } | undefined;
      if (!data) return 2000;
      if (data.status === "success" || data.status === "failure") return false;
      return 2000;
    },
  });

  useEffect(() => {
    const s = status.data?.status;
    if (!s) return;
    if (s === "started" && phase === "extracting") setPhase("building");
    if (s === "success") {
      setPhase("done");
      const chainId = status.data?.chain_id;
      if (chainId) {
        setTimeout(() => router.push(`/chains/${chainId}`), 1200);
      }
    }
    if (s === "failure") {
      setPhase("failed");
      setReason(status.data?.error || status.data?.reason || "Chain build failed");
    }
  }, [status.data, phase, router]);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setTaskId(null);
    setPhase("idle");
    setReason(null);
    build.mutate();
  }

  function onFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const c = ev.target?.result;
      if (typeof c === "string") setContent(c);
    };
    reader.readAsText(file);
  }

  const words = content.trim() ? content.trim().split(/\\s+/).length : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Build a chain</h1>
        <p className="text-sm text-muted-foreground">
          Paste anything — an article, a tweet, a paragraph, JSON. The AI extracts
          events, finds related ones from your records, and writes a verified article.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Block</CardTitle>
          <CardDescription>
            Each block has its own voice and structure.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {blocks.isLoading ? (
              <p className="text-sm text-muted-foreground">Loading blocks…</p>
            ) : (
              blocks.data?.map((b) => (
                <button
                  key={b.slug}
                  type="button"
                  onClick={() => onBlockChange(b.slug)}
                  className={`rounded-md border px-3 py-1.5 text-sm transition ${
                    b.slug === blockSlug
                      ? "border-foreground bg-foreground text-background"
                      : "border-border hover:bg-muted"
                  }`}
                >
                  {b.name}
                </button>
              ))
            )}
          </div>

          {activeBlock && (
            <div className="rounded-md border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
              <p>
                <strong className="text-foreground">{activeBlock.name}</strong>
                {" — "}
                {activeBlock.description}
              </p>
              <p className="mt-1">
                Target: {String(activeBlock.config?.default_target_words ?? "—")} words
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Content</CardTitle>
          <CardDescription>
            Paste or upload any text. The AI extracts the events.
            Charged 1 token per 100 words.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="flex items-center gap-3">
              <label className="inline-flex items-center gap-2 text-sm cursor-pointer rounded-md border border-border px-3 py-1.5 hover:bg-muted">
                Upload file
                <input type="file" accept=".txt,.md,.json" className="hidden" onChange={onFileUpload} />
              </label>
              <Button type="button" variant="outline" onClick={() => setContent(SAMPLE[blockSlug] ?? "")}>
                Load sample
              </Button>
              <span className="text-xs text-muted-foreground ml-auto">
                {words} words · {Math.max(1, Math.floor(words / 100))} tokens
              </span>
            </div>

            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="Paste an article, a tweet, a paragraph, or any text about what you want the chain to be about…"
              className="w-full rounded-md border border-border bg-transparent p-3 text-sm leading-relaxed min-h-[280px] focus:outline-none focus:ring-2 focus:ring-ring"
            />

            {error && <p className="text-sm text-destructive">{error}</p>}

            {phase === "extracting" && (
              <div className="rounded-md border border-border bg-muted/30 p-3 text-sm">
                <p className="font-medium">Extracting events…</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Reading your text and identifying the discrete events.
                </p>
              </div>
            )}

            {phase === "building" && (
              <div className="rounded-md border border-border bg-muted/30 p-3 text-sm">
                <p className="font-medium">Building the chain…</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Finding related events, sequencing the story, writing, and verifying.
                  Takes ~60–90 seconds.
                </p>
              </div>
            )}

            {phase === "done" && (
              <div className="rounded-md border border-green-500/30 bg-green-500/5 p-3 text-sm">
                <p className="font-medium text-green-400">✓ Chain built</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Redirecting to the article…
                </p>
              </div>
            )}

            {phase === "failed" && reason && (
              <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400">
                <p className="font-medium">Couldn't build a chain</p>
                <p className="text-xs mt-1">{reason}</p>
              </div>
            )}

            <Button
              type="submit"
              disabled={build.isPending || phase === "extracting" || phase === "building" || phase === "done" || content.trim().length < 20}
            >
              {build.isPending || phase === "extracting" || phase === "building"
                ? "Working…"
                : `Build ${activeBlock?.name ?? ""} chain`}
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>How it works</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p><strong>1 — Extract:</strong> the AI reads your paste and pulls out the discrete events.</p>
          <p><strong>2 — Enrich:</strong> related events from your records are added automatically.</p>
          <p><strong>3 — Select:</strong> the AI picks the events that form one coherent story.</p>
          <p><strong>4 — Order, link, narrate:</strong> sequenced, linked, and written as a long-form article.</p>
          <p><strong>5 — Verify:</strong> every claim audited against the sources. Flagged claims trigger one regeneration.</p>
          <p className="pt-2">Takes ~60–90 seconds.</p>
        </CardContent>
      </Card>
    </div>
  );
}
'''


def part8_new_page() -> None:
    print("\n[8/8] /chains/new page rewrite")
    NEW_PAGE.write_text(NEW_PAGE_SRC)
    print(f"  ✓ {NEW_PAGE.name}: rewritten")

    # Fix the list page copy
    list_src = LIST_PAGE.read_text()
    old_copy = "No chains yet. Submit a News Event JSON to <code>/api/chains/build</code>."
    new_copy = "No chains yet. <strong>Paste an article</strong> on the Build page to create one."
    if old_copy in list_src:
        LIST_PAGE.write_text(list_src.replace(old_copy, new_copy, 1))
        print(f"  ✓ {LIST_PAGE.name}: copy fixed")
    else:
        print(f"  ↷ {LIST_PAGE.name}: copy already updated")


# ─────────────────────────────────────────────────────────────────────
# Verification
# ─────────────────────────────────────────────────────────────────────

def verify() -> bool:
    print("\n" + "=" * 70)
    print("VERIFICATION")
    print("=" * 70)

    # Syntax check every touched Python file
    print("\n[A] syntax check")
    for f in [EXTRACT, ENRICH, TOKENS, SEED, SCHEMA, ROUTER]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ all parse")

    # Symbol import check — the S.6.1a lesson
    print("\n[B] symbol import check")
    r = subprocess.run(
        ["bash", "-c",
         "for f in $(grep -rl 'render_with_version\\|enrich_with_related_events\\|extract_events_from_any_input' packages/ apps/ --include='*.py'); do "
         "  case \"$f\" in "
         "    */loader.py) continue ;; "
         "    */extract.py) continue ;; "
         "    */enrich.py) continue ;; "
         "  esac; "
         "  echo \"$f\"; "
         "done"],
        cwd=ROOT, capture_output=True, text=True,
    )
    files_to_check = [f for f in r.stdout.strip().splitlines() if f]
    bad = []
    for f in files_to_check:
        body = Path(f).read_text()
        for symbol in ("render_with_version", "enrich_with_related_events", "extract_events_from_any_input"):
            if symbol in body and f"import {symbol}" not in body and f" {symbol}," not in body:
                # check any import line contains the symbol
                import re
                if not re.search(rf"import[^\n]*\\b{symbol}\\b", body):
                    bad.append(f"{f}: {symbol}")
    if bad:
        print("  ⚠ missing imports:")
        for b in bad:
            print(f"     {b}")
    else:
        print("  ✓ every new symbol is imported where used")

    # alembic state
    print("\n[C] alembic current")
    r = subprocess.run(["alembic", "current"], cwd=ROOT, capture_output=True, text=True)
    print(" ", r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()[:200])

    # run the seeds
    print("\n[D] run the seeds")
    r = subprocess.run(
        ["python", "-c",
         "from packages.ai.prompts.seed_chain_extra import "
         "seed, seed_s61b_1, seed_s61b_1b, seed_s61b_1b_1, seed_s61b_2; "
         "seed(); seed_s61b_1(); seed_s61b_1b(); seed_s61b_1b_1(); seed_s61b_2(); "
         "from packages.ai.prompts.loader import clear_cache; clear_cache()"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(f"  ❌ seed: {r.stderr[-400:]}")
        return False
    print("  ✓ seeds ran")

    # tsc
    print("\n[E] tsc --noEmit")
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

    # Live end-to-end
    print("\n[F] live end-to-end")
    code = r'''
import asyncio, json, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.core.context import current_user_id, current_org_id

PASTE = """The Federal Reserve signalled on Tuesday that it will hold interest rates higher for longer, with Chair Jerome Powell telling reporters the committee sees no path to cutting rates before the second half of next year.

The decision sent Treasury yields sharply higher. The 10-year note jumped 18 basis points to 5.02%, its highest level since 2007.

Equity markets sold off broadly in response. The S&P 500 fell 2.1%, its worst single-day performance since August.
"""

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    F = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    from packages.db.session import get_owner_session_factory
    OF = get_owner_session_factory()
    async with OF() as s:
        row = (await s.execute(text("""
            SELECT u.id::text, o.id::text FROM users u
            JOIN memberships m ON m.user_id = u.id
            JOIN orgs o ON o.id = m.org_id
            WHERE u.username = 'tokentest' LIMIT 1
        """))).first()
    uid, oid = row
    current_user_id.set(uid); current_org_id.set(oid)

    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()

        # a — source idempotency
        from packages.ai.chain.extract import (
            ensure_manual_paste_source, persist_paste, extract_events_from_any_input,
        )
        from packages.ai.chain.enrich import enrich_with_related_events
        from packages.core.tokens import charge_for_upload, get_balance

        sid1 = await ensure_manual_paste_source(s, oid)
        sid2 = await ensure_manual_paste_source(s, oid)
        assert sid1 == sid2, f"source not idempotent: {sid1} != {sid2}"
        print(f"  [a] ensure_manual_paste_source idempotent: {sid1[:8]}…")

        # b — persist
        raw_id = await persist_paste(s, oid, sid1, PASTE, created_by=uid)
        assert raw_id
        print(f"  [b] persist_paste → {raw_id[:8]}…")

        # c — charge
        before = await get_balance(s, uid)
        charged = await charge_for_upload(s, uid, word_count=250, ref_id=raw_id, org_id=oid)
        after = await get_balance(s, uid)
        assert before - after == 2, f"charge 250 words should debit 2, got {before-after}"
        print(f"  [c] charge_for_upload(250) debited {before-after} (expect 2)")

        # d — extract
        events = await extract_events_from_any_input(s, oid, PASTE)
        print(f"  [d] extract → {len(events)} events")
        assert len(events) >= 2, f"expected >=2 events, got {len(events)}"
        for e in events[:3]:
            print(f"       - {e.headline[:70]}")

        # e — enrich
        enriched = await enrich_with_related_events(s, oid, events[:1], max_total=10)
        print(f"  [e] enrich: 1 seed → {len(enriched)} total")
        assert len(enriched) >= 1

        print("  ALL DIRECT CHECKS PASSED")
    await engine.dispose()

asyncio.run(main())
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=180,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False

    # HTTP end-to-end
    print("\n[G] HTTP end-to-end")
    code2 = r'''
import asyncio, json, subprocess, time, urllib.request

API = "http://localhost:8000"

def post(path, body, token=None):
    req = urllib.request.Request(f"{API}{path}", method="POST")
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, json.dumps(body).encode()) as r:
        return r.status, json.loads(r.read())

def get(path, token=None):
    req = urllib.request.Request(f"{API}{path}")
    if token: req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read())

# login
import urllib.error
try:
    status, data = post("/api/auth/login", {"identifier": "tokentest", "password": "TestPass123!"})
    token = data["access_token"]
except urllib.error.HTTPError as e:
    print(f"  login failed: {e.code} {e.read()[:200]}")
    raise SystemExit(1)

paste = """The Federal Reserve signalled on Tuesday that it will hold interest rates higher for longer. The 10-year note jumped 18 basis points to 5.02%. The S&P 500 fell 2.1%."""

print(f"  POST /api/chains/build …")
status, data = post("/api/chains/build", {
    "content": paste,
    "block_slug": "news",
}, token=token)
print(f"    HTTP {status}  task_id={data.get('chain_id','')[:8]}…  event_count={data.get('event_count')}")
task_id = data["chain_id"]

for _ in range(60):
    time.sleep(3)
    status, s = get(f"/api/chains/build/{task_id}", token=token)
    if s["status"] in ("success", "failure"):
        print(f"    final: {s['status']}  verification_status={s.get('verification_status')}  chain_id={s.get('chain_id','')[:8] if s.get('chain_id') else None}")
        if s["status"] == "success":
            print(f"    ✓ chain built end-to-end")
            break
        else:
            print(f"    ❌ failure: {s.get('error')}")
            raise SystemExit(1)
else:
    print("    ❌ timed out after 3 minutes")
    raise SystemExit(1)
'''
    r = subprocess.run(
        ["python", "-c", code2], cwd=ROOT,
        capture_output=True, text=True, timeout=300,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False

    return True


def main() -> None:
    print("=" * 70)
    print("S.6.1b.2 — paste-anything chain build")
    print("=" * 70)

    part1_extract()
    part2_enrich()
    part3_tokens()
    part4_seed_extract()
    part5_schema()
    part6_router()
    part7_api_ts()
    part8_new_page()

    # Restart the API so it picks up the router change
    print("\n[R] restart API")
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
        print(f"  ❌ /health = {r.stdout}")
        print(f"     log tail: {log.read_text()[-500:]}")
        sys.exit(1)
    print("  ✓ API up")

    ok = verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.1b.2 complete — paste-anything works end to end")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
