import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from sqlalchemy import delete

from sourcelens.evaluation.dataset import EvalQuestion
from sourcelens.evaluation.run import evaluate
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
def evaluated_repository(monkeypatch):
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
            "class OrderService:\n    def create_order(self):\n        return 1\n"
        )
        (root / "cache.py").write_text("import redis\nclient = redis.Redis()\n")

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
def test_evaluate_reports_perfect_recall_for_an_unambiguous_dataset(evaluated_repository):
    analysis_id = evaluated_repository
    dataset = [
        EvalQuestion(question="How are orders created?", expected_files=["orders.py"]),
        EvalQuestion(question="Where is the redis client?", expected_files=["cache.py"]),
    ]

    result = evaluate(analysis_id, dataset, strategy="hybrid_rerank", k=5)

    assert result["questions"] == 2
    assert result["recall_at_k"] == 1.0
    assert result["mrr"] == 1.0
    assert result["avg_latency_ms"] >= 0.0
