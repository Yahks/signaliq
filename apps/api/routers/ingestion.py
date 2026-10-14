"""Ingestion: upload a file, create a job, hand off to Celery."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, current_user_id, require_role
from apps.api.schemas.ingestion import JobResponse, UploadResponse
from packages.core.error import NotFoundError, ValidationError
from packages.db.models.ingestion_job import IngestionJob
from packages.db.models.source import Source
from packages.db.session import get_session
from packages.db.tenant import set_current_org
from packages.ingestion.parsers.dispatch import detect_format

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])

UPLOAD_DIR = Path("/tmp/signaliq_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.post("/upload", response_model=UploadResponse, status_code=202)
async def upload(
    source_id: str = Form(...),
    file: UploadFile = File(...),
    mapping: str | None = Form(default=None),  # JSON-encoded dict
    org_id: str = Depends(current_org_id),
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_scoped_session),
    _role: str = Depends(require_role("owner", "admin", "analyst")),
) -> UploadResponse:
    # Verify the source exists and belongs to this org (RLS-enforced)
    src_result = await session.execute(select(Source).where(Source.id == source_id))
    if not src_result.scalar_one_or_none():
        raise NotFoundError("Source not found")

    if not file.filename:
        raise ValidationError("No filename provided")

    # Detect format from filename
    try:
        fmt = detect_format(file.filename)
    except Exception as exc:
        raise ValidationError(str(exc)) from exc

    # Parse mapping if provided
    mapping_dict: dict[str, str] | None = None
    if mapping:
        try:
            parsed = json.loads(mapping)
            if not isinstance(parsed, dict):
                raise ValueError("mapping must be a JSON object")
            mapping_dict = {str(k): str(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValidationError(f"Invalid mapping JSON: {exc}") from exc

    # Read file into memory (bounded — see max-size check below)
    raw = await file.read()
    MAX_BYTES = 200 * 1024 * 1024  # 200 MB
    if len(raw) > MAX_BYTES:
        raise ValidationError(f"File exceeds {MAX_BYTES // 1024 // 1024} MB limit")

    # Create the job
    job = IngestionJob(
        org_id=org_id,
        source_id=source_id,
        status="pending",
        file_format=fmt,
        mapping=mapping_dict,
        created_by=user_id,
    )
    session.add(job)
    await session.flush()
    job_id = job.id

    # Persist file to disk for the worker
    file_path = UPLOAD_DIR / f"{job_id}.{fmt}"
    file_path.write_bytes(raw)

    # Update job with the file path
    job.file_path = str(file_path)
    await session.flush()
    await session.commit()

    # Enqueue the Celery task (import here to avoid circular imports)
    from apps.worker.tasks.ingestion import ingest_file
    ingest_file.delay(job_id)

    return UploadResponse(
        job_id=job_id,
        status="pending",
        message="Upload received. Processing in background.",
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(_scoped_session),
) -> JobResponse:
    result = await session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise NotFoundError("Job not found")
    return JobResponse.model_validate(job)


@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(
    session: AsyncSession = Depends(_scoped_session),
) -> list[JobResponse]:
    result = await session.execute(
        select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(100)
    )
    return [JobResponse.model_validate(j) for j in result.scalars().all()]
