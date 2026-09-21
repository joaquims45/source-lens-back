from uuid import UUID

from sourcelens.retrieval.lexical import ScoredChunk


def reciprocal_rank_fusion(
    *rankings: list[ScoredChunk], k: int = 60, limit: int = 20
) -> list[ScoredChunk]:
    """Combine independently-ranked candidate lists (semantic, lexical, ...)
    by rank position rather than raw score, so two scales that aren't
    comparable (cosine similarity vs. `ts_rank_cd`) can still be fused
    without one dominating just because its numbers are larger.
    """
    fused: dict[UUID, float] = {}
    for ranking in rankings:
        for rank, scored in enumerate(ranking, start=1):
            fused[scored.chunk_id] = fused.get(scored.chunk_id, 0.0) + 1.0 / (k + rank)
    ordered = sorted(fused.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [ScoredChunk(chunk_id, score) for chunk_id, score in ordered]
