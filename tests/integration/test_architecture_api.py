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
        (root / "orders" / "service.py").write_text(
            "import redis\n\n"
            "class OrderService:\n"
            "    def create(self):\n"
            "        client = redis.Redis()\n"
            "        session.query(Order).all()\n"
        )
        (root / "Dockerfile").write_text("FROM python:3.13\n")
        (root / "requirements.txt").write_text("fastapi\ncelery\n")

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


@pytest.mark.integration
def test_architecture_graph_reflects_real_evidence(client, analyzed_repository):
    analysis_id = analyzed_repository

    graph = client.get(f"/api/v1/analyses/{analysis_id}/architecture").json()

    types = {node["type"] for node in graph["nodes"]}
    assert "application" in types
    assert "module" in types
    assert "service" in types
    assert "database" in types
    assert "cache" in types
    assert "infrastructure" in types
    assert "queue" in types  # from requirements.txt's celery

    service_node = next(n for n in graph["nodes"] if n["type"] == "service")
    assert service_node["label"] == "OrderService"
    assert service_node["source_files"] == ["orders/service.py"]
    assert all(e["evidence"] for e in graph["edges"])


@pytest.mark.integration
def test_component_endpoint_returns_evidence_and_dependencies(client, analyzed_repository):
    analysis_id = analyzed_repository
    graph = client.get(f"/api/v1/analyses/{analysis_id}/architecture").json()
    service_node = next(n for n in graph["nodes"] if n["type"] == "service")

    response = client.get(
        f"/api/v1/analyses/{analysis_id}/architecture/components/{service_node['id']}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["node"]["label"] == "OrderService"
    outgoing_types = {e["type"] for e in body["outgoing"]}
    assert "reads" in outgoing_types  # session.query(...)
    assert "depends_on" in outgoing_types  # redis.Redis()


@pytest.mark.integration
def test_component_endpoint_404s_for_unknown_component(client, analyzed_repository):
    analysis_id = analyzed_repository
    response = client.get(f"/api/v1/analyses/{analysis_id}/architecture/components/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.integration
def test_architecture_404s_for_unknown_analysis(client):
    response = client.get(f"/api/v1/analyses/{uuid4()}/architecture")
    assert response.status_code == 404
