import hashlib
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sourcelens.api.errors import DomainError
from sourcelens.ingestion.github import validate_ref, validate_url
from sourcelens.persistence.models import Analysis, Job, Outbox, Repository


def submit(db: Session, url: str, ref: str | None, key: str, request_id: str) -> Job:
    repo = validate_url(url)
    ref = validate_ref(ref)
    fingerprint = hashlib.sha256(json.dumps([repo.url, ref]).encode()).hexdigest()
    # Serialize the idempotency key without a race between lookup and INSERT.
    from sqlalchemy import text

    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
    existing = db.scalar(select(Job).where(Job.idempotency_key == key))
    if existing:
        if existing.request_hash != fingerprint:
            raise DomainError("idempotency_conflict", "Key already used for another request", 409)
        return existing
    db.execute(
        insert(Repository)
        .values(id=uuid4(), canonical_url=repo.url, owner=repo.owner, name=repo.name)
        .on_conflict_do_nothing()
    )
    repository = db.scalar(select(Repository).where(Repository.canonical_url == repo.url))
    assert repository is not None
    analysis = Analysis(repository_id=repository.id, requested_ref=ref)
    db.add(analysis)
    db.flush()
    job = Job(
        analysis_id=analysis.id,
        idempotency_key=key,
        request_hash=fingerprint,
        request_id=request_id,
    )
    db.add(job)
    db.flush()
    db.add(Outbox(job_id=job.id))
    return job
