import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from sourcelens.architecture import service as architecture_service
from sourcelens.architecture.service import get_cached_architecture_graph
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
from sourcelens.tracing import service as tracing_service
from sourcelens.tracing.service import get_symbol_trace


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
        (root / "orders.py").write_text(
            "class OrderService:\n    def create(self):\n        return 1\n"
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


@pytest.mark.integration
def test_architecture_graph_is_computed_once_and_then_served_from_cache(
    analyzed_repository, monkeypatch
):
    analysis_id = analyzed_repository
    calls = []
    original = architecture_service.detect_modules

    def counting_detect_modules(builder, files):
        calls.append(1)
        return original(builder, files)

    monkeypatch.setattr(architecture_service, "detect_modules", counting_detect_modules)

    with session() as db:
        first = get_cached_architecture_graph(db, analysis_id)
    with session() as db:
        second = get_cached_architecture_graph(db, analysis_id)

    assert len(calls) == 1, "detection should only run on the first, uncached call"
    assert [n.id for n in first.nodes] == [n.id for n in second.nodes]


@pytest.mark.integration
def test_symbol_trace_call_detection_is_cached_across_different_symbols(
    analyzed_repository, monkeypatch
):
    analysis_id = analyzed_repository
    calls = []
    original = tracing_service.detect_calls

    def counting_detect_calls(symbol_rows, content_by_file):
        calls.append(1)
        return original(symbol_rows, content_by_file)

    monkeypatch.setattr(tracing_service, "detect_calls", counting_detect_calls)

    with session() as db:
        symbol_id = db.scalars(select(Symbol.id).where(Symbol.analysis_id == analysis_id)).first()
    assert symbol_id is not None

    with session() as db:
        get_symbol_trace(db, analysis_id, symbol_id)
    with session() as db:
        get_symbol_trace(db, analysis_id, symbol_id)

    assert len(calls) == 1, "the call-graph scan should only run once, not per trace request"
