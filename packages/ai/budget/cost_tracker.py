"""Per-org AI cost tracking and budget enforcement.

Design:
  - Redis is the hot path (sub-millisecond checks before every LLM call)
  - Postgres is the durable record (updated async, periodically)
  - Hard caps raise before the call; soft caps just warn

Usage:
    await budget.check_and_reserve(org_id, estimated_cost_usd=0.001)
    # ... run the LLM call ...
    await budget.record(org_id, input_tokens=..., output_tokens=..., cost_usd=...)
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings
from packages.core.error import QuotaExceededError
from packages.core.logging import get_logger

log = get_logger(__name__)

# Default budget for orgs without explicit ai_config
# S.9.23 — no caps. Users generate freely.
# The keys remain so an org can opt-in to a cap in its own
# ai_config, but the defaults are None (unlimited).
DEFAULT_CONFIG = {
    "daily_cost_cap_usd": None,
    "monthly_soft_cap_usd": None,
    "daily_token_cap": None,
    "enabled": True,
    "alert_at_percent": 80,
}

# Redis keys
_KEY_USAGE = "budget:{org_id}:{date}"          # daily usage (hash)
_KEY_LOCK = "budget:{org_id}:lock"             # prevents double-flush


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _get_redis():
    """Lazy Redis client (sync — used from sync contexts)."""
    import redis
    settings = get_settings()
    # Redis URL is hardcoded in celery_app; reuse here
    _url = settings.redis_url.rsplit("/", 1)[0] + "/3"
    return redis.from_url(_url, decode_responses=True)


def _sync_session():
    from packages.db.session import get_sync_engine
    engine = get_sync_engine()
    return sessionmaker(bind=engine, expire_on_commit=False)()


def get_org_config(org_id: str) -> dict:
    """Load org's ai_config from DB, with defaults filled in."""
    session = _sync_session()
    try:
        row = session.execute(
            sql_text("SELECT ai_config FROM orgs WHERE id = :oid"),
            {"oid": org_id},
        ).first()
        if not row:
            return DEFAULT_CONFIG
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(row[0] or {})
        return cfg
    finally:
        session.close()


def _today_usage(org_id: str) -> dict:
    """Read today's usage from Redis (fast). Falls back to empty."""
    try:
        r = _get_redis()
        key = _KEY_USAGE.format(org_id=org_id, date=_today())
        data = r.hgetall(key)
        if not data:
            return {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": 0.0}
        return {
            "input_tokens": int(data.get("input_tokens", 0)),
            "output_tokens": int(data.get("output_tokens", 0)),
            "calls": int(data.get("calls", 0)),
            "cost_usd": float(data.get("cost_usd", 0.0)),
        }
    except Exception as exc:
        log.warning("budget.redis_read_failed", error=str(exc)[:200])
        return {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": 0.0}


def check_budget(org_id: str, *, estimated_cost_usd: float = 0.0) -> None:
    """Raise QuotaExceededError if the org has hit its daily hard cap.

    Called BEFORE any expensive LLM call.
    """
    cfg = get_org_config(org_id)
    if not cfg.get("enabled", True):
        return

    usage = _today_usage(org_id)
    # S.9.23 — only enforce if the org set a cap.
    _cost_raw = cfg.get("daily_cost_cap_usd")
    daily_cap = float(_cost_raw) if _cost_raw is not None else None
    _token_raw = cfg.get("daily_token_cap")
    token_cap = int(_token_raw) if _token_raw is not None else None

    current_cost = usage["cost_usd"]
    total_tokens = usage["input_tokens"] + usage["output_tokens"]

    if daily_cap is not None and current_cost + estimated_cost_usd > daily_cap:
        log.warning(
            "budget.hard_cap_hit",
            org_id=org_id,
            current_cost=current_cost,
            daily_cap=daily_cap,
        )
        raise QuotaExceededError(
            f"Daily AI cost cap reached (${current_cost:.4f}/${daily_cap:.2f}). "
            f"Resets at midnight UTC.",
            org_id=org_id,
            current_cost_usd=current_cost,
            daily_cap_usd=daily_cap,
        )

    if token_cap is not None and total_tokens > token_cap:
        log.warning(
            "budget.token_cap_hit",
            org_id=org_id,
            current_tokens=total_tokens,
            daily_cap=token_cap,
        )
        raise QuotaExceededError(
            f"Daily token cap reached ({total_tokens}/{token_cap}). Resets at midnight UTC.",
            org_id=org_id,
            current_tokens=total_tokens,
            daily_token_cap=token_cap,
        )


def record_usage(
    org_id: str,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
) -> None:
    """Increment today's counters in Redis. Fire-and-forget."""
    if not org_id:
        return
    try:
        r = _get_redis()
        key = _KEY_USAGE.format(org_id=org_id, date=_today())
        pipe = r.pipeline()
        if input_tokens:
            pipe.hincrby(key, "input_tokens", int(input_tokens))
        if output_tokens:
            pipe.hincrby(key, "output_tokens", int(output_tokens))
        if cost_usd is not None and cost_usd > 0:
            # Store as micro-dollars to avoid float precision issues
            pipe.hincrbyfloat(key, "cost_usd", float(cost_usd))
        pipe.hincrby(key, "calls", 1)
        pipe.expire(key, 60 * 60 * 48)  # 48h TTL
        pipe.execute()
    except Exception as exc:
        log.warning("budget.redis_write_failed", error=str(exc)[:200])


def flush_to_postgres(org_id: str | None = None) -> int:
    """Persist Redis usage to Postgres. Called periodically by Celery Beat.

    If org_id is None, flushes ALL orgs.
    """
    session = _sync_session()
    flushed = 0
    try:
        if org_id:
            org_ids = [org_id]
        else:
            rows = session.execute(sql_text("SELECT id::text FROM orgs WHERE deleted_at IS NULL")).all()
            org_ids = [r[0] for r in rows]

        today = datetime.now(UTC).date()
        for oid in org_ids:
            usage = _today_usage(oid)
            if usage["calls"] == 0 and usage["cost_usd"] == 0:
                continue
            session.execute(
                sql_text("""
                    INSERT INTO org_ai_usage
                        (org_id, usage_date, input_tokens, output_tokens, calls, cost_usd, last_updated_at)
                    VALUES
                        (:oid, :d, :it, :ot, :c, :cost, now())
                    ON CONFLICT (org_id, usage_date) DO UPDATE
                    SET input_tokens = EXCLUDED.input_tokens,
                        output_tokens = EXCLUDED.output_tokens,
                        calls = EXCLUDED.calls,
                        cost_usd = EXCLUDED.cost_usd,
                        last_updated_at = now()
                """),
                {
                    "oid": oid, "d": today,
                    "it": usage["input_tokens"],
                    "ot": usage["output_tokens"],
                    "c": usage["calls"],
                    "cost": Decimal(str(usage["cost_usd"])),
                },
            )
            flushed += 1
        session.commit()
        log.info("budget.flushed", orgs=flushed)
        return flushed
    except Exception as exc:
        session.rollback()
        log.warning("budget.flush_failed", error=str(exc)[:200])
        return 0
    finally:
        session.close()


def get_usage_summary(org_id: str) -> dict:
    """Return today's usage + caps for UI display."""
    cfg = get_org_config(org_id)
    usage = _today_usage(org_id)
    daily_cap = float(cfg.get("daily_cost_cap_usd", DEFAULT_CONFIG["daily_cost_cap_usd"]))
    token_cap = int(cfg.get("daily_token_cap", DEFAULT_CONFIG["daily_token_cap"]))
    pct_cost = (usage["cost_usd"] / daily_cap * 100) if daily_cap > 0 else 0
    total_tokens = usage["input_tokens"] + usage["output_tokens"]
    pct_tokens = (total_tokens / token_cap * 100) if token_cap > 0 else 0
    return {
        "date": _today(),
        "cost_usd": usage["cost_usd"],
        "cost_cap_usd": daily_cap,
        "cost_pct": round(pct_cost, 2),
        "tokens_total": total_tokens,
        "token_cap": token_cap,
        "token_pct": round(pct_tokens, 2),
        "calls": usage["calls"],
        "enabled": cfg.get("enabled", True),
        "alert_at_percent": cfg.get("alert_at_percent", 80),
    }
