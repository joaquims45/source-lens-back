import os
from uuid import uuid4

import pytest
from sqlalchemy import delete
from structlog.testing import capture_logs

from sourcelens.persistence.database import session
from sourcelens.persistence.models import Analysis, Chunk, Repository, SourceFile
from sourcelens.retrieval.embeddings import HashingEmbeddings
from sourcelens.retrieval.lexical import lexical_search
from sourcelens.retrieval.rerank import OverlapReranker
from sourcelens.retrieval.semantic import semantic_search
from sourcelens.retrieval.service import search

PROVIDER = HashingEmbeddings()


@pytest.fixture
def indexed_chunks():
    """Two chunks with real, committed embeddings and tsvectors: retrieval
    queries the actual engine/session, not the rollback-isolated `db` fixture.
    """
    if os.environ.get("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 with migrated PostgreSQL and Redis")
    order_service = "class OrderService:\n    def create_order(self):\n        return 1\n"
    redis_client = "import redis\nclient = redis.Redis()\n"
    with session() as db, db.begin():
        repo = Repository(
            canonical_url=f"https://github.com/test/{uuid4()}", owner="test", name="a"
        )
        db.add(repo)
        db.flush()
        analysis = Analysis(repository_id=repo.id)
        db.add(analysis)
        db.flush()
        file = SourceFile(
            analysis_id=analysis.id,
            path="a.py",
            language="python",
            size_bytes=1,
            content_hash="h",
            content=order_service,
            parse_status="ok",
        )
        db.add(file)
        db.flush()
        embeddings = PROVIDER.embed([order_service, redis_client])
        order_chunk = Chunk(
            analysis_id=analysis.id,
            file_id=file.id,
            ordinal=0,
            kind="class",
            content=order_service,
            context_header="file: a.py\nsymbol: OrderService",
            content_hash="c0",
            start_line=1,
            end_line=3,
            token_count=10,
            embedding=embeddings[0],
            embedding_model=PROVIDER.name,
        )
        redis_chunk = Chunk(
            analysis_id=analysis.id,
            file_id=file.id,
            ordinal=1,
            kind="module",
            content=redis_client,
            context_header="file: a.py\nsymbol: <module>",
            content_hash="c1",
            start_line=4,
            end_line=5,
            token_count=5,
            embedding=embeddings[1],
            embedding_model=PROVIDER.name,
        )
        db.add_all([order_chunk, redis_chunk])
        db.flush()
        ids = (repo.id, analysis.id, order_chunk.id, redis_chunk.id)

    yield ids

    repo_id, analysis_id, _, _ = ids
    with session() as db, db.begin():
        db.execute(delete(Chunk).where(Chunk.analysis_id == analysis_id))
        db.execute(delete(SourceFile).where(SourceFile.analysis_id == analysis_id))
        db.execute(delete(Analysis).where(Analysis.id == analysis_id))
        db.execute(delete(Repository).where(Repository.id == repo_id))


@pytest.mark.integration
def test_lexical_search_finds_exact_identifier_matches(indexed_chunks):
    _, analysis_id, order_chunk_id, _ = indexed_chunks
    with session() as db:
        results = lexical_search(db, analysis_id, "OrderService create_order")
    assert [r.chunk_id for r in results] == [order_chunk_id]


@pytest.mark.integration
def test_lexical_search_ignores_other_analyses(indexed_chunks):
    _, _, _, _ = indexed_chunks
    with session() as db:
        assert lexical_search(db, uuid4(), "OrderService") == []


@pytest.mark.integration
def test_semantic_search_ranks_by_embedding_similarity(indexed_chunks):
    _, analysis_id, order_chunk_id, redis_chunk_id = indexed_chunks
    query_embedding = PROVIDER.embed(["OrderService create_order"])[0]
    with session() as db:
        results = semantic_search(db, analysis_id, query_embedding)
    assert [r.chunk_id for r in results] == [order_chunk_id, redis_chunk_id]


@pytest.mark.integration
def test_service_search_hybrid_rerank_returns_scored_citations(indexed_chunks):
    _, analysis_id, order_chunk_id, _ = indexed_chunks
    with session() as db:
        results = search(
            db,
            analysis_id,
            "how are orders created",
            embedding_provider=PROVIDER,
            reranker=OverlapReranker(),
            strategy="hybrid_rerank",
        )
    assert results
    top = results[0]
    assert top.chunk_id == order_chunk_id
    assert top.path == "a.py"
    assert top.start_line == 1 and top.end_line == 3
    assert top.semantic_score is not None
    assert top.fused_score is not None
    assert top.rerank_score is not None


@pytest.mark.integration
def test_service_search_semantic_only_omits_lexical_and_fused_scores(indexed_chunks):
    _, analysis_id, _, _ = indexed_chunks
    with session() as db:
        results = search(
            db, analysis_id, "OrderService", embedding_provider=PROVIDER, strategy="semantic"
        )
    assert all(r.lexical_score is None and r.fused_score is None for r in results)
    assert all(r.semantic_score is not None for r in results)


@pytest.mark.integration
def test_service_search_logs_structured_retrieval_observability(indexed_chunks):
    _, analysis_id, _, _ = indexed_chunks
    with session() as db, capture_logs() as logs:
        search(
            db,
            analysis_id,
            "how are orders created",
            embedding_provider=PROVIDER,
            reranker=OverlapReranker(),
            strategy="hybrid_rerank",
        )

    events = [entry for entry in logs if entry.get("event") == "retrieval"]
    assert len(events) == 1
    event = events[0]
    assert event["repository_id"] == str(analysis_id)
    assert event["strategy"] == "hybrid_rerank"
    assert event["candidates"] >= 1
    assert event["top_scores"]["semantic"] is not None
    assert event["retrieval_latency_ms"] >= 0
