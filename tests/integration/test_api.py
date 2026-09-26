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
    """A repository already ingested end to end, exercising the API against
    real, committed rows rather than the rollback-isolated `db` fixture.
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
        ids = (repo.id, analysis.id, job.id)

    repo_id, analysis_id, job_id = ids
    with TemporaryDirectory() as workdir:
        root = Path(workdir)
        (root / "service.py").write_text(
            "class Service:\n    def run(self):\n        return 1\n"
        )

        @contextmanager
        def fake_clone(repo, ref, settings, heartbeat):
            yield root, "a" * 40

        monkeypatch.setattr(tasks, "clone_repository", fake_clone)
        tasks.ingest(str(job_id))

    yield ids

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
def test_submit_repository_requires_idempotency_key(client):
    response = client.post("/api/v1/repositories", json={"url": "https://github.com/a/b"})
    assert response.status_code == 422


@pytest.mark.integration
def test_submit_repository_returns_ids(client):
    response = client.post(
        "/api/v1/repositories",
        json={"url": f"https://github.com/a/{uuid4()}"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202
    body = response.json()
    assert {"repository_id", "analysis_id", "job_id", "status"} <= body.keys()
    assert body["status"] == "queued"

    with session() as db, db.begin():
        db.execute(delete(Outbox).where(Outbox.job_id == body["job_id"]))
        db.execute(delete(Job).where(Job.id == body["job_id"]))
        db.execute(delete(Analysis).where(Analysis.id == body["analysis_id"]))
        db.execute(delete(Repository).where(Repository.id == body["repository_id"]))


@pytest.mark.integration
def test_analysis_files_and_symbols_are_inspectable(client, analyzed_repository):
    _, analysis_id, _ = analyzed_repository

    analysis = client.get(f"/api/v1/analyses/{analysis_id}").json()
    assert analysis["status"] == "completed"
    assert analysis["commit_sha"] == "a" * 40
    assert analysis["stats"]["symbols"] == 2
    stage_names = {(stage["name"], stage["status"]) for stage in analysis["stages"]}
    assert ("parsing", "completed") in stage_names

    files = client.get(f"/api/v1/analyses/{analysis_id}/files").json()
    assert [file["path"] for file in files] == ["service.py"]
    file_id = files[0]["id"]

    detail = client.get(f"/api/v1/analyses/{analysis_id}/files/{file_id}").json()
    assert detail["content"].startswith("class Service")
    assert [symbol["qualified_name"] for symbol in detail["symbols"]] == ["Service", "Service.run"]

    symbols = client.get(f"/api/v1/analyses/{analysis_id}/symbols", params={"q": "run"}).json()
    assert [symbol["qualified_name"] for symbol in symbols] == ["Service.run"]


@pytest.mark.integration
def test_list_analyses_includes_newly_ingested_repository(client, analyzed_repository):
    repo_id, analysis_id, _ = analyzed_repository

    analyses = client.get("/api/v1/analyses").json()

    match = next(item for item in analyses if item["id"] == str(analysis_id))
    assert match["status"] == "completed"
    assert match["repository"] == {
        "id": str(repo_id),
        "owner": "test",
        "name": "a",
        "url": match["repository"]["url"],
    }


@pytest.mark.integration
def test_search_endpoint_returns_scored_hybrid_results(client, analyzed_repository):
    _, analysis_id, _ = analyzed_repository

    response = client.get(f"/api/v1/analyses/{analysis_id}/search", params={"q": "Service run"})
    assert response.status_code == 200
    results = response.json()
    assert results
    top = results[0]
    assert top["path"] == "service.py"
    assert top["semantic_score"] is not None
    assert top["fused_score"] is not None
    assert top["rerank_score"] is not None


@pytest.mark.integration
def test_search_endpoint_lexical_strategy_skips_embeddings(client, analyzed_repository):
    _, analysis_id, _ = analyzed_repository

    response = client.get(
        f"/api/v1/analyses/{analysis_id}/search",
        params={"q": "Service", "strategy": "lexical"},
    )
    assert response.status_code == 200
    results = response.json()
    assert results and all(r["semantic_score"] is None for r in results)


@pytest.mark.integration
def test_events_stream_replays_history_and_closes_at_terminal_status(client, analyzed_repository):
    _, analysis_id, _ = analyzed_repository

    with client.stream("GET", f"/api/v1/analyses/{analysis_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    events = [line for line in body.splitlines() if line.startswith("event: ")]
    assert "event: analysis.started" in events
    assert "event: analysis.completed" in events


@pytest.mark.integration
def test_missing_analysis_returns_problem_json(client):
    response = client.get(f"/api/v1/analyses/{uuid4()}")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
