import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from sourcelens.jobs.dispatcher import dispatch_once
from sourcelens.persistence.database import session
from sourcelens.persistence.models import Analysis, Job, Outbox, Repository, now


@pytest.fixture
def committed_job():
    """dispatch_once() opens its own session against the real engine, so this
    needs committed rows rather than the rollback-isolated `db` fixture.
    """
    if os.environ.get("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 with migrated PostgreSQL and Redis")
    with session() as db, db.begin():
        repo = Repository(
            canonical_url=f"https://github.com/test/{uuid4()}", owner="test", name="a"
        )
        db.add(repo)
        db.flush()
        analysis = Analysis(repository_id=repo.id)
        db.add(analysis)
        db.flush()
        job = Job(
            analysis_id=analysis.id, idempotency_key=str(uuid4()), request_hash="h", request_id="r"
        )
        db.add(job)
        db.flush()
        db.add(Outbox(job_id=job.id))
        ids = (repo.id, analysis.id, job.id)
    yield ids
    repo_id, analysis_id, job_id = ids
    with session() as db, db.begin():
        db.execute(delete(Outbox).where(Outbox.job_id == job_id))
        db.execute(delete(Job).where(Job.id == job_id))
        db.execute(delete(Analysis).where(Analysis.id == analysis_id))
        db.execute(delete(Repository).where(Repository.id == repo_id))


@pytest.mark.integration
def test_dispatch_sends_a_newly_queued_job_once(committed_job, monkeypatch):
    _, _, job_id = committed_job
    sent = []
    monkeypatch.setattr(
        "sourcelens.jobs.dispatcher.app.send_task",
        lambda name, args, queue: sent.append((name, args, queue)),
    )

    assert dispatch_once() == 1
    assert sent == [("sourcelens.ingest", [str(job_id)], "ingestion")]

    # Already published within the debounce window: no redelivery yet.
    assert dispatch_once() == 0
    assert len(sent) == 1


@pytest.mark.integration
def test_dispatch_redelivers_after_a_worker_loses_its_lease(committed_job, monkeypatch):
    _, _, job_id = committed_job
    sent = []
    monkeypatch.setattr(
        "sourcelens.jobs.dispatcher.app.send_task",
        lambda name, args, queue: sent.append(args),
    )

    assert dispatch_once() == 1

    with session() as db, db.begin():
        job = db.get(Job, job_id)
        assert job is not None
        job.status = "running"
        job.lease_expires_at = now() + timedelta(seconds=90)

    # The lease is still valid: a crashed-worker redelivery must not happen yet.
    assert dispatch_once() == 0
    assert sent == [[str(job_id)]]

    with session() as db, db.begin():
        job = db.get(Job, job_id)
        assert job is not None
        job.lease_expires_at = now() - timedelta(seconds=1)
        outbox = db.scalar(select(Outbox).where(Outbox.job_id == job_id))
        assert outbox is not None
        # The publish debounce also needs to have elapsed: in real crash
        # recovery, a lease outlives it (lease_seconds=90 > the 60s debounce),
        # so both naturally clear together well after the original publish.
        outbox.published_at = now() - timedelta(seconds=61)

    # The worker holding the lease is presumed dead: redeliver to a new one.
    assert dispatch_once() == 1
    assert sent == [[str(job_id)], [str(job_id)]]


@pytest.mark.integration
def test_dispatch_ignores_terminal_jobs(committed_job, monkeypatch):
    _, _, job_id = committed_job
    monkeypatch.setattr(
        "sourcelens.jobs.dispatcher.app.send_task",
        lambda name, args, queue: pytest.fail("terminal jobs must not be redelivered"),
    )
    with session() as db, db.begin():
        job = db.get(Job, job_id)
        assert job is not None
        job.status = "completed"

    assert dispatch_once() == 0
