"""Trading subblock generation tasks.

One task per cadence group. Each iterates orgs and calls
packages.trading.subblocks.base.generate_subblock for each subblock
in that cadence.
"""
from __future__ import annotations

import asyncio

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger


# ── Per-worker-thread asyncio loop ───────────────────────────────
# Celery prefork children churn tasks. _run() creates and
# closes a loop per call, which leaves stale futures attached to a
# closed loop, and the next call crashes with "attached to a
# different loop". One loop per worker thread, reused forever,
# avoids it.
import asyncio as _asyncio
import threading as _threading

_loop_local = _threading.local()


def _get_loop() -> _asyncio.AbstractEventLoop:
    loop = getattr(_loop_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        _loop_local.loop = loop
    return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


async def _record_run(
    session,
    *,
    run_id: str,
    status: str,
    error: str | None = None,
    post_id: str | None = None,
) -> None:
    """Update a generation_runs row. Never raises — the run record
    is diagnostic, not transactional. A failed write must not fail
    the generation."""
    from sqlalchemy import text as sql_text
    try:
        await session.execute(sql_text("""
            UPDATE generation_runs
            SET status = :status,
                error = :error,
                post_id = :pid,
                finished_at = now()
            WHERE id = :rid
        """), {
            "rid": run_id,
            "status": status,
            "error": (error or "")[:1000] or None,
            "pid": post_id,
        })
        await session.commit()
    except Exception as exc:
        log.warning("trading.run_record_failed",
                    error=str(exc)[:120])


configure_logging()
log = get_logger(__name__)


def _list_orgs() -> list[dict]:
    from sqlalchemy import create_engine, text as sql_text
    from packages.core.config import get_settings

    settings = get_settings()
    from packages.db.session import get_sync_engine
    engine = get_sync_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql_text(
                "SELECT id::text, slug FROM orgs WHERE deleted_at IS NULL"
            )).all()
            return [{"id": r[0], "slug": r[1]} for r in rows]
    finally:
        engine.dispose()


async def _generate_for_org(org_id: str, slug: str) -> dict:
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.db.session import get_session_factory

    from packages.core.config import get_settings
    from packages.core.context import current_org_id, current_user_id
    from packages.core.tokens import charge_for_content
    from packages.trading.subblocks.base import generate_subblock

    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        await session.execute(sql_text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        # Resolve the org owner so token debits target the right user
        owner_row = (await session.execute(sql_text("""
            SELECT user_id::text FROM memberships
            WHERE org_id = :oid AND role = 'owner'
            LIMIT 1
        """), {"oid": org_id})).first()
        owner_id = owner_row[0] if owner_row else None
        if owner_id:
            current_user_id.set(owner_id)
        current_org_id.set(org_id)

        # Exhaustion gate — skip silently if the owner is out of tokens.
        if owner_id:
            from packages.core.tokens import can_generate
            if not await can_generate(session, owner_id):
                log.info("trading.generation_skipped_no_tokens",
                         org_id=org_id, slug=slug, user_id=owner_id[:8])
                return {"status": "skipped_no_tokens", "org_id": org_id, "slug": slug}

        # S.7.2 — auto-toggle gate. Missing row means disabled: the
        # org has not selected the trading block, or an operator removed
        # it. Either way, do not generate.
        from packages.core.blocks import is_block_enabled
        if not await is_block_enabled(session, org_id, "trading"):
            log.info("trading.generation_skipped_auto_disabled",
                     org_id=org_id, slug=slug)
            return {"status": "skipped_auto_disabled", "org_id": org_id, "slug": slug}

        # Record this attempt. Every generation — success, failure,
        # draft — leaves one row. The UI reads it so the user sees
        # what happened, not just the outcome.
        import uuid as _uuid
        _run_id = str(_uuid.uuid4())
        try:
            await session.execute(sql_text("""
                INSERT INTO generation_runs
                    (id, org_id, subblock_slug, status)
                VALUES
                    (:rid, :oid, :slug, 'running')
            """), {"rid": _run_id, "oid": org_id, "slug": slug})
            await session.commit()
        except Exception as _re:
            log.warning("trading.run_insert_failed",
                        error=str(_re)[:120])

        try:
            result = await generate_subblock(session, org_id=org_id, slug=slug)
        except Exception as exc:
            import traceback as _tb
            _tb.print_exc()
            from packages.core.error import CredentialsExhaustedError, NoCredentialsError
            from packages.trading.subblocks.base import DuplicatePostError
            err_name = type(exc).__name__
            # S.7.5.2 — a duplicate is a successful no-op. No charge,
            # no failure log, no notification.
            if isinstance(exc, DuplicatePostError):
                log.info("trading.duplicate_skipped",
                         org_id=org_id, slug=slug, detail=str(exc)[:200])
                await _record_run(session, run_id=_run_id,
                                  status="duplicate")
                return {"status": "duplicate", "org_id": org_id, "slug": slug}
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
                await _record_run(
                    session, run_id=_run_id,
                    status="credentials_issue",
                    error=str(exc)[:500],
                )
                result = {"status": "skipped_credentials_issue", "slug": slug,
                          "org_id": org_id, "reason": err_name}
            else:
                log.warning("trading.generate_failed", slug=slug, error=str(exc)[:200])
                await _record_run(
                    session, run_id=_run_id,
                    status="failed",
                    error=str(exc)[:500],
                )
                result = {"status": "failed", "slug": slug, "error": str(exc)[:200]}

        # S.5.4a — charge for the post that was just written.
        # Only when the generator reports a real post (id + word_count).
        if owner_id and isinstance(result, dict):
            post_id = result.get("post_id")
            word_count = result.get("word_count")
            if post_id and word_count:
                await charge_for_content(
                    session, owner_id, kind="post",
                    word_count=int(word_count), ref_id=post_id, org_id=org_id,
                )

        # Success — record the outcome. Distinguish published from
        # draft: a draft means the validator rejected the body.
        if isinstance(result, dict):
            _status = "published"
            _err = None
            if result.get("status") == "draft" or result.get("unsupported_claims"):
                _status = "draft"
                _claims = result.get("unsupported_claims") or []
                _err = "; ".join(str(c) for c in _claims[:5])[:500]
            await _record_run(
                session, run_id=_run_id, status=_status,
                error=_err, post_id=result.get("post_id"),
            )

    return result


@celery_app.task(name="tasks.trading_generate_stock_lists", bind=True)
def trading_generate_stock_lists(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate stock_lists for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "stock_lists"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_market_signals", bind=True)
def trading_generate_market_signals(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate market_signals for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "market_signals"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_screener_picks", bind=True)
def trading_generate_screener_picks(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate screener_picks for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "screener_picks"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_trading_psychology", bind=True)
def trading_generate_trading_psychology(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate trading_psychology for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "trading_psychology"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_investment_strategies", bind=True)
def trading_generate_investment_strategies(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate investment_strategies for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "investment_strategies"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_trading_styles", bind=True)
def trading_generate_trading_styles(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate trading_styles for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "trading_styles"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_class_room", bind=True)
def trading_generate_class_room(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate class_room for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "class_room"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_trading_tools", bind=True)
def trading_generate_trading_tools(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate trading_tools for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "trading_tools"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_market_analysis", bind=True)
def trading_generate_market_analysis(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate market_analysis for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "market_analysis"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}



@celery_app.task(name="tasks.trading_generate_movers", bind=True)
def trading_generate_movers(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate market_analysis for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "movers"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}


@celery_app.task(name="tasks.trading_generate_market_condition", bind=True)
def trading_generate_market_condition(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate market_condition for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "market_condition"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_market_cap", bind=True)
def trading_generate_market_cap(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate market_cap for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "market_cap"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_asset_classes", bind=True)
def trading_generate_asset_classes(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate asset_classes for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "asset_classes"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_technical_indicators", bind=True)
def trading_generate_technical_indicators(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate technical_indicators for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "technical_indicators"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_economic_events", bind=True)
def trading_generate_economic_events(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate economic_events for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "economic_events"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_risk_management", bind=True)
def trading_generate_risk_management(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate risk_management for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "risk_management"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}

@celery_app.task(name="tasks.trading_generate_fundamental_metrics", bind=True)
def trading_generate_fundamental_metrics(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate fundamental_metrics for one org, or all orgs if none given."""
    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results = []
    for o in orgs:
        r = _run(_generate_for_org(o["id"], "fundamental_metrics"))
        results.append({"org": o["id"], **r})
    return {"count": len(results), "results": results}




@celery_app.task(name="tasks.trading_generate_synthetic_trades", bind=True)
def trading_generate_synthetic_trades(self, org_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Generate synthetic trade_log rows for each org. Every 2 hours."""
    import asyncio
    from packages.db.session import get_session_factory
    from packages.trading.synthetic_trades import generate_synthetic_trades
    from sqlalchemy import text as _sql_text

    orgs = [{"id": org_id}] if org_id else _list_orgs()
    results: list[dict] = []

    async def _run_one(oid: str) -> dict:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                _sql_text("SELECT set_config('app.current_org_id', :oid, false)"),
                {"oid": oid},
            )
            r = await generate_synthetic_trades(session, org_id=oid)
            r["org_id"] = oid
            return r

    for o in orgs:
        try:
            results.append(_run(_run_one(o["id"])))
        except Exception as exc:
            results.append({"org_id": o["id"], "status": "error",
                            "error": str(exc)[:200]})

    return {"count": len(results), "results": results}
