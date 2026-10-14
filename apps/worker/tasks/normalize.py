"""Normalize task: clean, dedup, language-detect raw_records into normalized_records."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, select, text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from apps.worker.celery_app import celery_app
from packages.core.config import get_settings
from packages.core.logging import configure_logging, get_logger
from packages.ingestion.cleaners.text_cleaner import clean
from packages.ingestion.language.detector import detect_language

configure_logging()
log = get_logger(__name__)


def _sync_session() -> Session:
    settings = get_settings()
    from packages.db.session import get_sync_engine
    engine = get_sync_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory()


@celery_app.task(name="tasks.normalize_job", bind=True)
def normalize_job(self, job_id: str) -> dict:  # type: ignore[no-untyped-def]
    """Read all raw_records for a job, produce normalized_records."""
    from packages.db.models.ingestion_job import IngestionJob
    from packages.db.models.normalized_record import NormalizedRecord
    from packages.db.models.raw_record import RawRecord

    session = _sync_session()
    try:
        job = session.execute(
            select(IngestionJob).where(IngestionJob.id == job_id)
        ).scalar_one_or_none()
        if not job:
            return {"error": "job not found"}

        session.execute(
            sql_text("SET LOCAL app.current_org_id = :oid"),
            {"oid": str(job.org_id)},
        )

        raws = session.execute(
            select(RawRecord).where(RawRecord.ingestion_job_id == job_id)
        ).scalars().all()

        processed = 0
        for rr in raws:
            try:
                cleaned = clean(rr.text)
            except ValueError:
                continue

            lang = detect_language(cleaned.text)

            nr = NormalizedRecord(
                org_id=str(rr.org_id),
                raw_record_id=str(rr.id),
                normalized_text=cleaned.text,
                detected_language=lang.language,
                language_confidence=lang.confidence,
                is_duplicate=False,
                urls=cleaned.urls,
                hashtags=cleaned.hashtags,
                mentions=cleaned.mentions,
                tokens_count=cleaned.tokens_count,
            )
            session.add(nr)
            processed += 1
            if processed % 500 == 0:
                session.commit()
                session.execute(
                    sql_text("SET LOCAL app.current_org_id = :oid"),
                    {"oid": str(job.org_id)},
                )

        session.commit()
        log.info("normalize.done", job_id=job_id, processed=processed)

        # Enqueue enrichment for this org (fire and forget)
        from apps.worker.tasks.enrich import enrich_org
        enrich_org.delay(str(job.org_id))

        return {"job_id": job_id, "normalized": processed}

    except Exception:
        session.rollback()
        log.exception("normalize.failed", job_id=job_id)
        raise
    finally:
        session.close()
