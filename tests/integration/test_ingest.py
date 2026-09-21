import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from sourcelens.jobs import tasks
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
def committed_job():
    """The ingest task opens its own sessions against the real engine, so it
    needs rows that are actually committed rather than the rollback-isolated
    `db` fixture used by transaction-scoped tests.
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
    yield ids
    repo_id, analysis_id, job_id = ids
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
def test_ingest_parses_and_completes_analysis(committed_job, monkeypatch):
    _, analysis_id, job_id = committed_job

    with TemporaryDirectory() as workdir:
        root = Path(workdir)
        (root / "service.py").write_text(
            "class Service:\n    def run(self):\n        return 1\n"
        )
        excluded = root / "node_modules"
        excluded.mkdir()
        (excluded / "x.js").write_text("ignored")

        @contextmanager
        def fake_clone(repo, ref, settings, heartbeat):
            heartbeat()
            yield root, "a" * 40

        monkeypatch.setattr(tasks, "clone_repository", fake_clone)
        tasks.ingest(str(job_id))

    with session() as db:
        analysis = db.get(Analysis, analysis_id)
        assert analysis is not None
        assert analysis.status == "completed"
        assert analysis.commit_sha == "a" * 40
        assert analysis.stats["symbols"] == 2
        assert analysis.stats["chunks"] == 2
        assert analysis.stats["embedded_chunks"] == 2
        assert analysis.capabilities["languages"] == ["javascript", "python", "tsx", "typescript"]
        assert analysis.capabilities["search"] == {
            "lexical": True,
            "semantic": True,
            "hybrid": True,
        }
        assert analysis.versions["embedding"] == "hashing-v1-256"

        chunks = db.scalars(select(Chunk).where(Chunk.analysis_id == analysis_id)).all()
        assert all(chunk.embedding is not None for chunk in chunks)
        assert all(chunk.embedding_model == "hashing-v1-256" for chunk in chunks)

        files = db.scalars(select(SourceFile).where(SourceFile.analysis_id == analysis_id)).all()
        assert [file.path for file in files] == ["service.py"]

        symbols = db.scalars(select(Symbol).where(Symbol.analysis_id == analysis_id)).all()
        assert {symbol.qualified_name for symbol in symbols} == {"Service", "Service.run"}

        exclusions = db.scalars(
            select(Exclusion).where(Exclusion.analysis_id == analysis_id)
        ).all()
        assert exclusions[0].path == "node_modules"

        job = db.get(Job, job_id)
        assert job is not None
        assert job.status == "completed"


@pytest.mark.integration
def test_ingest_fails_job_and_analysis_on_clone_error(committed_job, monkeypatch):
    from sourcelens.api.errors import DomainError

    _, analysis_id, job_id = committed_job

    @contextmanager
    def fake_clone(repo, ref, settings, heartbeat):
        raise DomainError("clone_failed", "boom")
        yield  # pragma: no cover

    monkeypatch.setattr(tasks, "clone_repository", fake_clone)
    tasks.ingest(str(job_id))

    with session() as db:
        analysis = db.get(Analysis, analysis_id)
        assert analysis is not None
        assert analysis.status == "failed"
        job = db.get(Job, job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.error == "clone_failed"
