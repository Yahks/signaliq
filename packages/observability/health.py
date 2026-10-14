"""System health checks.

Returns structured status for:
  - Database (Postgres)
  - Redis (broker + cache)
  - Celery (worker + queue depth)
  - LLM providers (Groq, Ollama)
  - Recent activity (success rate, error rate)

Never raises — always returns a dict with a top-level "status" of
"healthy" | "degraded" | "down".
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text as sql_text

from packages.core.config import get_settings
from packages.core.logging import get_logger

log = get_logger(__name__)


def _check_postgres() -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        settings = get_settings()
        from packages.db.session import get_sync_engine
        engine = get_sync_engine()
        with engine.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
            # Simple stats
            row = conn.execute(sql_text("""
                SELECT
                    count(*) FILTER (WHERE NOT pg_is_in_recovery()) AS primary_count
                FROM pg_stat_activity
                WHERE datname = current_database()
            """)).first()
        elapsed = (time.monotonic() - t0) * 1000
        return {"status": "ok", "latency_ms": round(elapsed, 2)}
    except Exception as exc:
        return {"status": "down", "error": str(exc)[:200]}


def _check_redis() -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        import redis
        from packages.core.config import get_settings
        _url = get_settings().redis_url.rsplit("/", 1)[0] + "/0"
        r = redis.from_url(_url, socket_timeout=2)
        r.ping()
        elapsed = (time.monotonic() - t0) * 1000
        return {"status": "ok", "latency_ms": round(elapsed, 2)}
    except Exception as exc:
        return {"status": "down", "error": str(exc)[:200]}


def _check_celery() -> dict[str, Any]:
    try:
        from apps.worker.celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=2)
        active = inspect.active() or {}
        scheduled = inspect.scheduled() or {}
        registered = inspect.registered() or {}

        # Queue depth via Redis (broker is db 1)
        import redis
        from packages.core.config import get_settings
        _url = get_settings().redis_url.rsplit("/", 1)[0] + "/1"
        r = redis.from_url(_url, socket_timeout=2)
        queue_depth = r.llen("ingestion") + r.llen("default") + r.llen("celery")

        worker_count = len([k for k in registered if registered[k]])
        active_count = sum(len(v) for v in active.values())
        return {
            "status": "ok" if worker_count > 0 else "degraded",
            "workers": worker_count,
            "active_tasks": active_count,
            "queue_depth": queue_depth,
        }
    except Exception as exc:
        return {"status": "down", "error": str(exc)[:200]}


def _check_providers() -> dict[str, Any]:
    """Check that at least one fast model provider is reachable."""
    settings = get_settings()
    out: dict[str, Any] = {}

    # Groq
    if settings.groq_api_key:
        t0 = time.monotonic()
        try:
            import httpx
            r = httpx.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                timeout=3.0,
            )
            elapsed = (time.monotonic() - t0) * 1000
            out["groq"] = {
                "status": "ok" if r.status_code == 200 else "degraded",
                "latency_ms": round(elapsed, 2),
                "http_status": r.status_code,
            }
        except Exception as exc:
            out["groq"] = {"status": "down", "error": str(exc)[:200]}

    # Ollama (local)
    t0 = time.monotonic()
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=3.0)
        elapsed = (time.monotonic() - t0) * 1000
        out["ollama"] = {
            "status": "ok" if r.status_code == 200 else "degraded",
            "latency_ms": round(elapsed, 2),
        }
    except Exception as exc:
        out["ollama"] = {"status": "down", "error": str(exc)[:200]}

    return out


def _recent_activity() -> dict[str, Any]:
    """AI call success/failure in the last hour."""
    try:
        settings = get_settings()
        from packages.db.session import get_sync_engine
        engine = get_sync_engine()
        with engine.connect() as conn:
            cutoff = datetime.now(UTC) - timedelta(hours=1)
            row = conn.execute(sql_text("""
                SELECT
                    count(*) AS total,
                    count(*) FILTER (WHERE status = 'ok') AS ok,
                    count(*) FILTER (WHERE status = 'error') AS errors,
                    avg(latency_ms)::int AS avg_latency_ms
                FROM ai_calls
                WHERE created_at >= :cutoff
            """), {"cutoff": cutoff}).first()
        total = int(row[0] or 0)
        ok = int(row[1] or 0)
        errors = int(row[2] or 0)
        return {
            "total_calls_1h": total,
            "ok_calls_1h": ok,
            "error_calls_1h": errors,
            "success_rate": round(ok / total, 4) if total > 0 else None,
            "avg_latency_ms": int(row[3]) if row[3] else None,
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


def get_health() -> dict[str, Any]:
    """Full system health snapshot. Never raises."""
    components = {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "celery": _check_celery(),
        "providers": _check_providers(),
    }
    activity = _recent_activity()

    # Overall status
    statuses = []
    for comp in components.values():
        if isinstance(comp, dict) and "status" in comp:
            statuses.append(comp["status"])
        elif isinstance(comp, dict):
            for v in comp.values():
                if isinstance(v, dict) and "status" in v:
                    statuses.append(v["status"])

    if "down" in statuses:
        overall = "down"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "checked_at": datetime.now(UTC).isoformat(),
        "components": components,
        "activity_1h": activity,
    }
