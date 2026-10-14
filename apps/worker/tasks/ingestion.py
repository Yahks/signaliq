"""Ingestion task: parse a file, insert raw_records, then normalize."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from apps.worker.celery_app import celery_app
from packages.core.config import get_settings
from packages.core.logging import configure_logging, get_logger
from packages.ingestion.parsers.dispatch import parse_file
from packages.ingestion.dedup.exact import content_hash

configure_logging()
log = get_logger(__name__)


def _sync_session() -> Session:
    """A synchronous SQLAlchemy session for the worker.

    Workers use the OWNER role because they process jobs across the whole
    file for a known org — RLS is set explicitly per insert via SET LOCAL.
    """
    settings = get_settings()
    from packages.db.session import get_sync_engine
    engine = get_sync_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory()


@celery_app.task(name="tasks.ingest_file", bind=True)
def ingest_file(self, job_id: str) -> dict:  # type: ignore[no-untyped-def]
    """Parse a file, insert raw_records for the job's org, update job status."""
    from sqlalchemy import text as sql_text
    from packages.db.models.ingestion_job import IngestionJob
    from packages.db.models.raw_record import RawRecord

    session = _sync_session()
    try:
        job = session.execute(
            select(IngestionJob).where(IngestionJob.id == job_id)
        ).scalar_one_or_none()
        if not job:
            log.error("ingest.job_not_found", job_id=job_id)
            return {"error": "job not found"}

        job.status = "running"
        job.started_at = datetime.now(UTC)
        session.commit()

        # Set tenant context for this session (RLS)
        session.execute(
            sql_text("SET LOCAL app.current_org_id = :oid"),
            {"oid": str(job.org_id)},
        )

        raw = Path(job.file_path).read_bytes()  # type: ignore[arg-type]
        mapping = job.mapping or {}

        processed = 0
        failed = 0

        for rec in parse_file(raw, filename=Path(job.file_path).name, mapping=mapping):
            try:
                rr = RawRecord(
                    org_id=str(job.org_id),
                    source_id=str(job.source_id),
                    ingestion_job_id=str(job.id),
                    source_record_id=rec.source_record_id,
                    author_identifier=rec.author_identifier,
                    original_timestamp=rec.original_timestamp,
                    text=rec.text,
                    language=rec.language,
                    metadata_=rec.metadata,
                    content_hash=content_hash(rec.text),
                )
                session.add(rr)
                processed += 1
                if processed % 500 == 0:
                    session.commit()
                    session.execute(
                        sql_text("SET LOCAL app.current_org_id = :oid"),
                        {"oid": str(job.org_id)},
                    )
            except Exception as exc:
                failed += 1
                log.warning("ingest.record_failed", job_id=job_id, error=str(exc))

        job.status = "succeeded"
        job.total_records = processed
        job.processed_records = processed
        job.failed_records = failed
        job.finished_at = datetime.now(UTC)
        session.commit()

        log.info("ingest.done", job_id=job_id, processed=processed, failed=failed)

        # Enqueue normalization
        from apps.worker.tasks.normalize import normalize_job
        normalize_job.delay(job_id)

        return {"job_id": job_id, "processed": processed, "failed": failed}

    except Exception as exc:
        log.exception("ingest.failed", job_id=job_id)
        session.rollback()
        try:
            job = session.execute(
                select(IngestionJob).where(IngestionJob.id == job_id)
            ).scalar_one_or_none()
            if job:
                job.status = "failed"
                job.error_message = str(exc)[:2000]
                job.finished_at = datetime.now(UTC)
                session.commit()
        except Exception:
            pass
        raise
    finally:
        session.close()
