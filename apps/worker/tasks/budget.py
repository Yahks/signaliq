"""Budget flush task: persist Redis usage counters to Postgres.

Runs every 5 minutes via Celery Beat, and can also be triggered manually.
"""
from __future__ import annotations

from apps.worker.celery_app import celery_app
from packages.core.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


@celery_app.task(name="tasks.flush_ai_budget", bind=True)
def flush_ai_budget(self) -> dict:  # type: ignore[no-untyped-def]
    """Flush all orgs' Redis budget counters to Postgres."""
    from packages.ai.budget.cost_tracker import flush_to_postgres
    count = flush_to_postgres(None)
    return {"orgs_flushed": count}
