from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from sourcelens.persistence.models import Chunk, SourceFile
from sourcelens.retrieval.embeddings import EmbeddingProvider
from sourcelens.retrieval.hybrid import reciprocal_rank_fusion
from sourcelens.retrieval.lexical import ScoredChunk, lexical_search
from sourcelens.retrieval.rerank import RerankCandidate, Reranker
from sourcelens.retrieval.semantic import semantic_search

Strategy = Literal["semantic", "lexical", "hybrid", "hybrid_rerank"]


@dataclass(frozen=True)
class SearchResult:
    chunk_id: UUID
    file_id: UUID
    path: str
    start_line: int
    end_line: int
    context_header: str
    content: str
    semantic_score: float | None
    lexical_score: float | None
    fused_score: float | None
    rerank_score: float | None


def search(
    db: Session,
    analysis_id: UUID,
    query: str,
    embedding_provider: EmbeddingProvider,
    reranker: Reranker | None = None,
    strategy: Strategy = "hybrid_rerank",
    k: int = 10,
    candidate_k: int = 30,
) -> list[SearchResult]:
    """Never sends the repository to an LLM: it returns a bounded, scored set
    of chunks with file/line citations, which is what M3's agent will ground
    answers in, and what the evaluation module measures Recall@K/MRR against.
    """
    semantic_results: list[ScoredChunk] = []
    lexical_results: list[ScoredChunk] = []
    if strategy in ("semantic", "hybrid", "hybrid_rerank"):
        query_embedding = embedding_provider.embed([query])[0]
        semantic_results = semantic_search(db, analysis_id, query_embedding, candidate_k)
    if strategy in ("lexical", "hybrid", "hybrid_rerank"):
        lexical_results = lexical_search(db, analysis_id, query, candidate_k)

    if strategy == "semantic":
        ranked = semantic_results
    elif strategy == "lexical":
        ranked = lexical_results
    else:
        ranked = reciprocal_rank_fusion(semantic_results, lexical_results, limit=candidate_k)

    candidate_ids = [item.chunk_id for item in ranked]
    if not candidate_ids:
        return []

    rows = db.execute(
        select(Chunk, SourceFile.path)
        .join(SourceFile, Chunk.file_id == SourceFile.id)
        .where(Chunk.id.in_(candidate_ids))
    ).all()
    by_id = {chunk.id: (chunk, path) for chunk, path in rows}

    rerank_scores: dict[UUID, float] = {}
    if strategy == "hybrid_rerank" and reranker is not None:
        candidates = [
            RerankCandidate(chunk_id, by_id[chunk_id][0].content)
            for chunk_id in candidate_ids
            if chunk_id in by_id
        ]
        reranked = reranker.rerank(query, candidates, limit=k)
        ordered_ids = [item.chunk_id for item in reranked]
        rerank_scores = {item.chunk_id: item.score for item in reranked}
    else:
        ordered_ids = candidate_ids[:k]

    semantic_by_id = {item.chunk_id: item.score for item in semantic_results}
    lexical_by_id = {item.chunk_id: item.score for item in lexical_results}
    fused_by_id = (
        {item.chunk_id: item.score for item in ranked}
        if strategy in ("hybrid", "hybrid_rerank")
        else {}
    )

    results = []
    for chunk_id in ordered_ids:
        if chunk_id not in by_id:
            continue
        chunk, path = by_id[chunk_id]
        results.append(
            SearchResult(
                chunk_id=chunk_id,
                file_id=chunk.file_id,
                path=path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                context_header=chunk.context_header,
                content=chunk.content,
                semantic_score=semantic_by_id.get(chunk_id),
                lexical_score=lexical_by_id.get(chunk_id),
                fused_score=fused_by_id.get(chunk_id),
                rerank_score=rerank_scores.get(chunk_id),
            )
        )
    return results
