import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from sourcelens.jobs import tasks
from sourcelens.main import create_app
from sourcelens.persistence.database import session
from sourcelens.persistence.models import (
    Analysis,
    Chunk,
    Event,
    Exclusion,
    Import,
    Job,
    Outbox,
    Repository,
    SourceFile,
    Stage,
    Symbol,
)


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def analyzed_repository(monkeypatch):
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
        ids = (repo.id, analysis.id, job.id)

    repo_id, analysis_id, job_id = ids
    with TemporaryDirectory() as workdir:
        root = Path(workdir)
        (root / "orders").mkdir()
        (root / "orders" / "app.py").write_text(
            "class OrderController:\n"
            "    def create(self):\n"
            "        return OrderService().create_order()\n"
            "\n\n"
            "class OrderService:\n"
            "    def create_order(self):\n"
            "        return OrderRepository().save()\n"
            "\n\n"
            "class OrderRepository:\n"
            "    def save(self):\n"
            "        return True\n"
        )

        @contextmanager
        def fake_clone(repo, ref, settings, heartbeat):
            yield root, "a" * 40

        monkeypatch.setattr(tasks, "clone_repository", fake_clone)
        tasks.ingest(str(job_id))

    yield analysis_id

    with session() as db, db.begin():
        db.execute(delete(Chunk).where(Chunk.analysis_id == analysis_id))
        db.execute(delete(Import).where(Import.analysis_id == analysis_id))
        db.execute(delete(Symbol).where(Symbol.analysis_id == analysis_id))
        db.execute(delete(Exclusion).where(Exclusion.analysis_id == analysis_id))
        db.execute(delete(SourceFile).where(SourceFile.analysis_id == analysis_id))
        db.execute(delete(Event).where(Event.job_id == job_id))
        db.execute(delete(Stage).where(Stage.job_id == job_id))
        db.execute(delete(Outbox).where(Outbox.job_id == job_id))
        db.execute(delete(Job).where(Job.id == job_id))
        db.execute(delete(Analysis).where(Analysis.id == analysis_id))
        db.execute(delete(Repository).where(Repository.id == repo_id))


def _find_symbol(client, analysis_id, qualified_name):
    symbols = client.get(f"/api/v1/analyses/{analysis_id}/symbols").json()
    return next(s for s in symbols if s["qualified_name"] == qualified_name)


@pytest.mark.integration
def test_trace_follows_controller_service_repository_chain(client, analyzed_repository):
    analysis_id = analyzed_repository
    service_symbol = _find_symbol(client, analysis_id, "OrderService.create_order")

    response = client.get(
        f"/api/v1/analyses/{analysis_id}/symbols/{service_symbol['id']}/trace"
    )
    assert response.status_code == 200
    body = response.json()

    assert body["symbol"]["qualified_name"] == "OrderService.create_order"
    caller_names = {c["symbol"]["qualified_name"] for c in body["callers"]}
    callee_names = {c["symbol"]["qualified_name"] for c in body["callees"]}
    assert "OrderController.create" in caller_names
    assert "OrderRepository.save" in callee_names
    assert all(c["resolution"] == "resolved" for c in body["callers"] + body["callees"])


@pytest.mark.integration
def test_trace_404s_for_unknown_symbol(client, analyzed_repository):
    analysis_id = analyzed_repository
    response = client.get(f"/api/v1/analyses/{analysis_id}/symbols/{uuid4()}/trace")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.integration
def test_trace_404s_for_unknown_analysis(client):
    response = client.get(f"/api/v1/analyses/{uuid4()}/symbols/{uuid4()}/trace")
    assert response.status_code == 404
