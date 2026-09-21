from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from sourcelens.api.deps import get_db
from sourcelens.api.errors import DomainError
from sourcelens.api.schemas import SearchResultResponse
from sourcelens.config import get_settings
from sourcelens.persistence.models import Analysis
from sourcelens.retrieval.embeddings import get_embedding_provider
from sourcelens.retrieval.rerank import OverlapReranker
from sourcelens.retrieval.service import search

router = APIRouter(prefix="/api/v1")

NEEDS_EMBEDDINGS = {"semantic", "hybrid", "hybrid_rerank"}


@router.get("/analyses/{analysis_id}/search")
def search_analysis(
    analysis_id: UUID,
    q: str = Query(min_length=1),
    k: int = Query(10, ge=1, le=50),
    strategy: Literal["semantic", "lexical", "hybrid", "hybrid_rerank"] = "hybrid_rerank",
    db: Session = Depends(get_db),
) -> list[SearchResultResponse]:
    """Never sends the repository to an LLM: this returns the same scored,
    citable chunks the future chat agent will ground answers in — useful on
    its own for inspecting retrieval quality before Milestone 3 exists.
    """
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise DomainError("analysis_not_found", "Analysis not found", 404)
    if strategy in NEEDS_EMBEDDINGS and not analysis.capabilities.get("search", {}).get("semantic"):
        raise DomainError(
            "search_not_ready", "Embeddings are not ready for this analysis yet", 409
        )

    results = search(
        db,
        analysis_id,
        q,
        embedding_provider=get_embedding_provider(get_settings()),
        reranker=OverlapReranker(),
        strategy=strategy,
        k=k,
    )
    return [
        SearchResultResponse(
            chunk_id=result.chunk_id,
            file_id=result.file_id,
            path=result.path,
            start_line=result.start_line,
            end_line=result.end_line,
            context_header=result.context_header,
            content=result.content,
            semantic_score=result.semantic_score,
            lexical_score=result.lexical_score,
            fused_score=result.fused_score,
            rerank_score=result.rerank_score,
        )
        for result in results
    ]
