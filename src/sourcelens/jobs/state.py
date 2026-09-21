from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from sourcelens.api.errors import DomainError
from sourcelens.config import get_settings
from sourcelens.persistence.models import Analysis, Event, Job, Stage, now

TERMINAL = {"completed", "failed", "cancelled"}


def event(db: Session, job: Job, kind: str, payload: dict[str, Any]) -> None:
    """Caller must hold the job row lock; sequence allocation is transactional."""
    job.event_sequence += 1
    db.add(Event(job_id=job.id, sequence=job.event_sequence, type=kind, payload=payload))


def claim(db: Session, job_id: UUID) -> UUID | None:
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.status in TERMINAL:
        return None
    if job.lease_expires_at is not None and job.lease_expires_at > now():
        return None
    analysis = db.get(Analysis, job.analysis_id)
    assert analysis is not None
    if job.cancel_requested or job.attempt >= get_settings().max_job_attempts:
        job.status = "cancelled" if job.cancel_requested else "failed"
        job.error = "cancelled" if job.cancel_requested else "attempts_exhausted"
        analysis.status = job.status
        event(db, job, f"analysis.{job.status}", {"error": job.error})
        return None
    owner = uuid4()
    job.lease_owner = owner
    job.lease_expires_at = now() + timedelta(seconds=get_settings().lease_seconds)
    job.heartbeat_at = now()
    job.attempt += 1
    job.status = analysis.status = "running"
    event(db, job, "analysis.started", {"attempt": job.attempt})
    return owner


def owned(db: Session, job_id: UUID, owner: UUID) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if (
        job is None
        or job.lease_owner != owner
        or job.status != "running"
        or job.lease_expires_at is None
        or job.lease_expires_at <= now()
    ):
        raise DomainError("lease_lost", "Worker no longer owns this job", 409)
    if job.cancel_requested:
        raise DomainError("cancelled", "Analysis cancelled", 409)
    job.heartbeat_at = now()
    job.lease_expires_at = now() + timedelta(seconds=get_settings().lease_seconds)
    return job


def progress(
    db: Session,
    job_id: UUID,
    owner: UUID,
    name: str,
    status: str,
    processed: int = 0,
    total: int | None = None,
    unit: str = "files",
    details: dict[str, Any] | None = None,
) -> None:
    job = owned(db, job_id, owner)
    stage = db.get(Stage, (job_id, name))
    if stage is None:
        stage = Stage(job_id=job_id, name=name)
        db.add(stage)
    stage.status, stage.processed, stage.total = status, processed, total
    stage.unit, stage.details = unit, details or {}
    event(
        db,
        job,
        f"stage.{status}",
        {
            "stage": name,
            "status": status,
            "processed": processed,
            "total": total,
            "unit": unit,
            "metadata": stage.details,
        },
    )


def fail(db: Session, job_id: UUID, owner: UUID, code: str) -> None:
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.lease_owner != owner or job.status != "running":
        return
    job.status = "cancelled" if code == "cancelled" else "failed"
    job.error, job.lease_expires_at = code, None
    analysis = db.get(Analysis, job.analysis_id)
    assert analysis is not None
    analysis.status = job.status
    for stage in db.scalars(select(Stage).where(Stage.job_id == job_id, Stage.status == "started")):
        stage.status = job.status
        stage.details = {"error": code}
    event(db, job, f"analysis.{job.status}", {"error": code})
