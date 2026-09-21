from datetime import timedelta
from uuid import uuid4

import pytest

from sourcelens.api.errors import DomainError
from sourcelens.jobs.state import claim, owned, progress
from sourcelens.persistence.models import Analysis, Event, Job, Repository, now


@pytest.mark.integration
def test_redelivery_lease_fencing_and_event_sequence(db):
    repo = Repository(canonical_url=str(uuid4()), owner="a", name="b")
    db.add(repo)
    db.flush()
    analysis = Analysis(repository_id=repo.id)
    db.add(analysis)
    db.flush()
    job = Job(
        analysis_id=analysis.id, idempotency_key=str(uuid4()), request_hash="a", request_id="b"
    )
    db.add(job)
    db.flush()
    first = claim(db, job.id)
    assert first is not None
    assert claim(db, job.id) is None
    progress(db, job.id, first, "clone", "started", total=None)
    job.lease_expires_at = now() - timedelta(seconds=1)
    db.flush()
    second = claim(db, job.id)
    assert second and second != first
    with pytest.raises(DomainError, match="no longer owns"):
        owned(db, job.id, first)
    db.flush()
    assert db.get(Event, (job.id, 3)).type == "analysis.started"
    job.cancel_requested = True
    db.flush()
    with pytest.raises(DomainError, match="cancelled"):
        owned(db, job.id, second)
