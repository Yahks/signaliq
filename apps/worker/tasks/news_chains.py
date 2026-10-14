"""News chain pipeline task — block-aware, shortlist-aware."""
from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger
from apps.worker._async import run as _run

configure_logging()
log = get_logger(__name__)


async def _build_chain_async(
    org_id: str,
    events: list[dict],
    block_slug: str = "news",
    subtopic_slug: str | None = None,
    target_words: int | None = None,
) -> dict:
    """Build a chain from a list of events and produce a post.

    1. Load block config
    2. Insert events (with block_id + subtopic_slug)
    3. Generate embeddings for each event
    4. Shortlist events to MAX_EVENTS using 3-tier selector
    5. Run the block-aware pipeline
    6. Persist chain + post + evidence
    """
    from sqlalchemy import text as _t
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory
    from dateutil import parser as dateparser

    from packages.ai.chain.pipeline import run_chain_pipeline
    from packages.ai.chain.shortlist import EventWithEmbedding, select_shortlist
    from packages.ai.router import embed
    from packages.blocks.loader import load_block_by_slug
    from packages.core.config import get_settings

    settings = get_settings()
    factory = get_session_factory()

    # 1. Load block config
    block = load_block_by_slug(block_slug, org_id=org_id)
    log.info("chain.task.block_loaded", block=block.slug, subtopic=subtopic_slug)

    def _dt(v):
        if v is None or v == "":
            return None
        if hasattr(v, "year"):
            return v
        try:
            return dateparser.parse(str(v))
        except Exception:
            return None

    async with factory() as session:
        await session.execute(_t(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        from packages.core.context import set_context_from_org
        owner_id = await set_context_from_org(session, org_id)

        # 2. Insert events with block + subtopic
        event_id_map: dict[str, str] = {}
        for i, ev in enumerate(events):
            placeholder = ev.get("_placeholder_id") or str(uuid4())
            external = ev.get("external_id")
            row = (await session.execute(_t("""
                INSERT INTO news_events
                    (org_id, block_id, subtopic_slug, headline, summary, occurred_at, reported_at,
                     location, actors, event_type, status, confidence, external_id)
                VALUES
                    (:org_id, :block_id, :subtopic_slug, :headline, :summary, :occurred_at, :reported_at,
                     :location, :actors, :event_type, :status, :confidence, :external_id)
                RETURNING id::text
            """), {
                "org_id": org_id,
                "block_id": block.id,
                "subtopic_slug": subtopic_slug,
                "headline": ev["headline"][:500],
                "summary": ev["summary"],
                "occurred_at": _dt(ev.get("occurred_at")),
                "reported_at": _dt(ev.get("reported_at")),
                "location": ev.get("location"),
                "actors": ev.get("actors") or [],
                "event_type": ev.get("event_type"),
                "status": ev.get("status", "raw"),
                "confidence": float(ev.get("confidence", 0.5)),
                "external_id": external,
            })).first()
            event_id_map[placeholder] = row[0]
        await session.commit()

        # 3. Generate embeddings for each event
        embed_rows = (await session.execute(_t("""
            SELECT id::text, headline, summary
            FROM news_events
            WHERE org_id = :org_id AND id = ANY(:ids)
        """), {
            "org_id": org_id,
            "ids": list(event_id_map.values()),
        })).all()

        if embed_rows:
            texts = [f"{r[1]}. {r[2] or ''}"[:1500] for r in embed_rows]
            try:
                vectors = await embed(texts=texts, org_id=org_id, purpose=f"event.embed.{block.slug}")
                # Store vectors back (as text, cast on write)
                for r, vec in zip(embed_rows, vectors):
                    vec_str = "[" + ",".join(str(float(x)) for x in vec) + "]"
                    await session.execute(_t("""
                        UPDATE news_events SET embedding = CAST(:vec AS vector(768))
                        WHERE id = :id
                    """), {"vec": vec_str, "id": r[0]})
                await session.commit()
                log.info("chain.task.embedded", count=len(embed_rows))
            except Exception as exc:
                log.warning("chain.task.embed_failed", error=str(exc)[:200])

        # 4. Shortlist
        # Reload with embeddings
        shortlist_rows = (await session.execute(_t("""
            SELECT id::text, occurred_at, embedding::text, headline, summary,
                   actors, event_type, status
            FROM news_events
            WHERE org_id = :org_id AND id = ANY(:ids)
        """), {
            "org_id": org_id,
            "ids": list(event_id_map.values()),
        })).all()

        def _parse_vec(s):
            if not s:
                return None
            return [float(x) for x in s.strip("[]").split(",")]

        candidates: list[EventWithEmbedding] = []
        for r in shortlist_rows:
            candidates.append(EventWithEmbedding(
                id=r[0],
                occurred_at_iso=r[1].isoformat() if r[1] else None,
                embedding=_parse_vec(r[2]),
                raw={
                    "id": r[0],
                    "headline": r[3],
                    "summary": r[4],
                    "actors": list(r[5] or []),
                    "event_type": r[6],
                    "status": r[7],
                },
            ))

        shortlist = select_shortlist(candidates)
        log.info("chain.task.shortlist", total=len(candidates), selected=len(shortlist))

        pipeline_events = [c.raw for c in shortlist]

        # 5. Run block-aware pipeline
        result = await run_chain_pipeline(
            events=pipeline_events,
            org_id=org_id,
            block=block,
            subtopic_slug=subtopic_slug,
            target_words=target_words,
        )

        if result.error and not result.post:
                    return {
                "status": "error",
                "error": result.error,
                "events_created": len(event_id_map),
                "block": block.slug,
            }

        # 6. Persist chain
        chain_row = (await session.execute(_t("""
            INSERT INTO news_chains
                (org_id, block_id, subtopic_slug, title, thesis, status, primary_entities, confidence)
            VALUES
                (:org_id, :block_id, :subtopic_slug, :title, :thesis, :status, :primary_entities, :confidence)
            RETURNING id::text
        """), {
            "org_id": org_id,
            "block_id": block.id,
            "subtopic_slug": subtopic_slug,
            "title": (result.selected.title if result.selected else "Untitled")[:500],
            "thesis": result.selected.thesis if result.selected else None,
            "status": "ready" if result.post else "failed",
            "primary_entities": (result.selected.primary_entities if result.selected else [])[:20],
            "confidence": result.selected.confidence if result.selected else None,
        })).first()
        chain_id = chain_row[0]

        # Persist links
        for link in result.links:
            await session.execute(_t("""
                INSERT INTO chain_links
                    (org_id, chain_id, from_event_id, to_event_id, link_type, rationale, confidence, sequence)
                VALUES
                    (:org_id, :chain_id, :from_id, :to_id, :link_type, :rationale, :confidence, :sequence)
                ON CONFLICT (chain_id, from_event_id, to_event_id, link_type) DO NOTHING
            """), {
                "org_id": org_id, "chain_id": chain_id,
                "from_id": link.from_event_id, "to_id": link.to_event_id,
                "link_type": link.link_type, "rationale": link.rationale,
                "confidence": link.confidence, "sequence": link.sequence,
            })

        # Persist post + evidence
        post_id = None
        # S.6.4.5 — belt-and-braces. Never persist a 0-word post, even
        # if the pipeline's own guard didn't fire.
        if result.post and result.post.word_count < 50:
            log.warning(
                "chain.task.empty_post_skipped",
                word_count=result.post.word_count,
                title=result.post.title[:60],
            )
            result.post = None
        if result.post:
            # S.6.1b.1b — status reflects the verification outcome.
            # 'error' means verify itself failed → hold as draft for retry.
            editorial_status = (
                "draft" if result.verification_status == "error" else "review"
            )
            post_row = (await session.execute(_t("""
                INSERT INTO generated_posts
                    (org_id, chain_id, title, body, word_count, status,
                     verification_status)
                VALUES
                    (:org_id, :chain_id, :title, :body, :wc, :status,
                     :verification_status)
                RETURNING id::text
            """), {
                "org_id": org_id, "chain_id": chain_id,
                "title": result.post.title[:500],
                "body": result.post.body,
                "wc": result.post.word_count,
                "status": editorial_status,
                "verification_status": result.verification_status,
            })).first()
            post_id = post_row[0]

            if result.verification:
                for claim in result.verification.claims:
                    await session.execute(_t("""
                        INSERT INTO post_evidence
                            (org_id, post_id, claim_text, claim_type, source_kind, source_id, confidence)
                        VALUES
                            (:org_id, :post_id, :text, :ctype, 'news_event', NULL, :conf)
                    """), {
                        "org_id": org_id, "post_id": post_id,
                        "text": claim.claim_text, "ctype": claim.claim_type,
                        "conf": claim.confidence,
                    })

        # 7. Persist living enrichment (impacts, scenarios, watchlist)
        living_counts = {"impacts": 0, "scenarios": 0, "watchlist": 0}
        if result.living:
            for idx, imp in enumerate(result.living.impacts):
                await session.execute(_t("""
                    INSERT INTO chain_impacts
                        (org_id, chain_id, label, category, direction, strength, reasoning, position)
                    VALUES
                        (:org_id, :chain_id, :label, :category, :direction, :strength, :reasoning, :position)
                """), {
                    "org_id": org_id, "chain_id": chain_id,
                    "label": imp.label, "category": imp.category,
                    "direction": imp.direction, "strength": imp.strength,
                    "reasoning": imp.reasoning, "position": idx,
                })
            living_counts["impacts"] = len(result.living.impacts)

            import json as _json
            for idx, sc in enumerate(result.living.scenarios):
                await session.execute(_t("""
                    INSERT INTO chain_scenarios
                        (org_id, chain_id, name, condition, reasoning,
                         candidate_symbols, quantified, position)
                    VALUES
                        (:org_id, :chain_id, :name, :condition, :reasoning,
                         :symbols, CAST(:quantified AS jsonb), :position)
                """), {
                    "org_id": org_id, "chain_id": chain_id,
                    "name": sc.name, "condition": sc.condition,
                    "reasoning": sc.reasoning, "position": idx,
                    "symbols": sc.candidate_symbols or [],
                    "quantified": _json.dumps(sc.quantified or {}),
                })
            living_counts["scenarios"] = len(result.living.scenarios)

            for idx, w in enumerate(result.living.watchlist):
                await session.execute(_t("""
                    INSERT INTO chain_watchlist
                        (org_id, chain_id, label, symbol, reasoning, live_quote, position)
                    VALUES
                        (:org_id, :chain_id, :label, :symbol, :reasoning,
                         CAST(:live_quote AS jsonb), :position)
                """), {
                    "org_id": org_id, "chain_id": chain_id,
                    "label": w.label, "symbol": w.symbol,
                    "reasoning": w.reasoning, "position": idx,
                    "live_quote": _json.dumps(w.live_quote or {}),
                })
            living_counts["watchlist"] = len(result.living.watchlist)

        # 8. Embed the chain for future auto-reattach
        #    Compose a canonical "chain fingerprint" from thesis + top events + entity list
        try:
            from packages.ai.router import embed as _embed
            fingerprint_parts = [
                result.selected.thesis if result.selected else "",
                " ".join((result.selected.primary_entities if result.selected else [])[:10]),
            ]
            for eid in (result.sequence or [])[:5]:
                ev = next((x for x in pipeline_events if x["id"] == eid), None)
                if ev:
                    fingerprint_parts.append(ev.get("headline", ""))
            fingerprint = " | ".join(p for p in fingerprint_parts if p)[:2000]

            if fingerprint:
                vecs = await _embed(texts=[fingerprint], org_id=org_id, purpose="chain.embed")
                if vecs:
                    vstr = "[" + ",".join(str(float(x)) for x in vecs[0]) + "]"
                    await session.execute(_t("""
                        UPDATE news_chains
                        SET embedding = CAST(:vec AS vector(768)),
                            last_updated_at = now()
                        WHERE id = :chain_id
                    """), {"vec": vstr, "chain_id": chain_id})
                    log.info("chain.embedded", chain_id=chain_id[:8])
        except Exception as exc:
            log.warning("chain.embed_failed", error=str(exc)[:200])

        await session.commit()

        # S.5.4a — charge for the article just written.
        if owner_id and post_id and result.post and result.post.word_count:
            from packages.core.tokens import charge_for_content
            await charge_for_content(
                session, owner_id, kind="article",
                word_count=int(result.post.word_count),
                ref_id=post_id, org_id=org_id,
            )

    return {
        "status": "ok",
        "chain_id": chain_id,
        "post_id": post_id,
        "block": block.slug,
        "subtopic": subtopic_slug,
        "events_input": len(events),
        "events_shortlisted": len(pipeline_events),
        "links": len(result.links),
        "words": result.post.word_count if result.post else 0,
        "unsupported_facts": result.verification.unsupported_fact_count if result.verification else 0,
        "verification_status": result.verification_status,
        "regenerated": result.regenerated,
        "impacts": living_counts["impacts"],
        "scenarios": living_counts["scenarios"],
        "watchlist": living_counts["watchlist"],
        "error": result.error,
    }


# S.6.2.3a — the outer wrapper. Any exception that escapes the body is
# turned into {status: 'error', error: <message>} so the polling endpoint
# (and its classifier) always sees a dict, never a Celery exception.
def _safe_build_chain_async(*args, **kwargs) -> dict:
    import asyncio as _asyncio
    try:
        return _run(_build_chain_async(*args, **kwargs))
    except Exception as exc:
        log.exception("chain.task.unhandled_exception",
                      error_type=type(exc).__name__,
                      error=str(exc)[:500])
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {str(exc)[:800]}",
            "chain_id": None,
            "post_id": None,
            "block": kwargs.get("block_slug") or (args[2] if len(args) > 2 else "news"),
        }


@celery_app.task(name="tasks.build_chain", bind=True)
def build_chain(  # type: ignore[no-untyped-def]
    self,
    org_id: str,
    events: list[dict],
    block_slug: str = "news",
    subtopic_slug: str | None = None,
    target_words: int | None = None,
) -> dict:
    # S.6.2.3a — never let an unhandled exception reach Celery. The
    # polling endpoint expects a dict; return one on every path.
    return _safe_build_chain_async(org_id, events, block_slug, subtopic_slug, target_words)
